"""Main JupyterCluster application"""

import logging
import os
from typing import Dict, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from tornado import web
from tornado.ioloop import IOLoop
from traitlets import Dict as TraitDict
from traitlets import Integer, Unicode, default
from traitlets.config import Application

from . import orm
from .api.base import APIHandler
from .api.hubs import HubActionAPIHandler, HubAPIHandler, HubListAPIHandler
from .api.users import UserAPIHandler, UserListAPIHandler
from .auth import Authenticator, OAuthenticatorWrapper, SimpleAuthenticator
from .handlers.admin import AdminHandler
from .handlers.home import HomeHandler
from .handlers.hubs import HubCreateHandler, HubDetailHandler
from .handlers.login import LoginHandler, LogoutHandler
from .hub import HubInstance

try:
    from .handlers.oauth import OAuthCallbackHandler, OAuthLoginHandler
except ImportError:
    OAuthCallbackHandler = None
    OAuthLoginHandler = None

logger = logging.getLogger(__name__)


class JupyterCluster(Application):
    """Main JupyterCluster application"""

    name = "jupytercluster"
    description = "Multi-hub management system for JupyterHub"

    # Database configuration
    db_url = Unicode(
        "sqlite:///jupytercluster.db",
        help="Database URL",
    ).tag(config=True)

    # Authentication
    authenticator_class = Unicode(
        "jupytercluster.auth.SimpleAuthenticator",
        help="Authenticator class to use",
    ).tag(config=True)

    # Hub configuration
    default_namespace_prefix = Unicode(
        "jupyterhub-",
        help="Prefix for hub namespaces",
    ).tag(config=True)

    default_helm_chart = Unicode(
        "jupyterhub/jupyterhub",
        help="Default Helm chart for hubs",
    ).tag(config=True)

    # Server configuration
    port = Integer(
        8080,
        help="Port for the hub server",
    ).tag(config=True)

    ip = Unicode(
        "0.0.0.0",
        help="IP address to bind to",
    ).tag(config=True)

    cookie_secret = Unicode(
        "",
        help="Secret key for secure cookies. If empty, will be generated and stored in database.",
    ).tag(config=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Initialize database
        self._init_database()

        # Initialize authenticator
        self._init_authenticator()

        # Load hubs from database
        self.hubs: Dict[str, HubInstance] = {}
        self._load_hubs()

        # Initialize web application
        self._init_web_app()

    def _init_database(self):
        """Initialize database connection"""
        self.engine = create_engine(self.db_url, echo=False)
        orm.Base.metadata.create_all(self.engine)
        self.Session = scoped_session(sessionmaker(bind=self.engine))
        self.db = self.Session()
        
        # Initialize or load cookie secret
        self._init_cookie_secret()

    def _init_authenticator(self):
        """Initialize authenticator"""
        authenticator_class = self._load_class(self.authenticator_class, Authenticator)
        self.authenticator = authenticator_class(parent=self)

    def _load_class(self, class_path, base_class):
        """Load a class by path"""
        module_path, class_name = class_path.rsplit(".", 1)
        module = __import__(module_path, fromlist=[class_name])
        cls = getattr(module, class_name)
        if not issubclass(cls, base_class):
            raise TypeError(f"{class_path} is not a subclass of {base_class.__name__}")
        return cls

    def _load_hubs(self):
        """Load hubs from database"""
        try:
            orm_hubs = self.db.query(orm.Hub).all()
            for orm_hub in orm_hubs:
                hub = HubInstance(orm_hub)
                self.hubs[hub.name] = hub
            logger.info(f"Loaded {len(self.hubs)} hubs from database")
        except Exception as e:
            logger.error(f"Failed to load hubs: {e}")

    def _init_web_app(self):
        """Initialize Tornado web application"""
        # Template and static paths
        # When installed as package, templates are in jupytercluster/templates
        # __file__ is /app/jupytercluster/app.py, so templates are at /app/jupytercluster/templates
        here = os.path.dirname(__file__)
        template_path = os.path.join(here, "templates")
        static_path = os.path.join(here, "static")
        
        # Verify paths exist
        if not os.path.exists(template_path):
            logger.warning(f"Template path not found: {template_path}")
        if not os.path.exists(static_path):
            logger.warning(f"Static path not found: {static_path}")

        handlers = [
            # Web UI
            (r"/", HomeHandler),
            (r"/home", HomeHandler),
            (r"/login", LoginHandler),
            (r"/logout", LogoutHandler),
            (r"/admin", AdminHandler),
            (r"/hubs/create", HubCreateHandler),
            (r"/hubs/([^/]+)", HubDetailHandler),
            # OAuth handlers
            (
                (r"/oauth_login", OAuthLoginHandler)
                if OAuthLoginHandler
                else (r"/oauth_login", web.ErrorHandler, {"status_code": 404})
            ),
            (
                (r"/oauth_callback", OAuthCallbackHandler)
                if OAuthCallbackHandler
                else (r"/oauth_callback", web.ErrorHandler, {"status_code": 404})
            ),
            # API
            (r"/api/hubs", HubListAPIHandler),
            (r"/api/hubs/([^/]+)", HubAPIHandler),
            (r"/api/hubs/([^/]+)/(start|stop)", HubActionAPIHandler),
            (r"/api/users", UserListAPIHandler),
            (r"/api/users/([^/]+)", UserAPIHandler),
            (r"/api/health", HealthHandler),
            # Error handlers (must be last)
            (r".*", NotFoundHandler),
        ]

        # Add OAuth handlers if using OAuthenticator
        if isinstance(self.authenticator, OAuthenticatorWrapper):
            oauth_handlers = self.authenticator.get_handlers(self)
            handlers.extend(oauth_handlers)

        settings = {
            "cookie_secret": self._get_cookie_secret(),
            "login_url": "/login",
            "template_path": template_path,
            "static_path": static_path,
            "static_url_prefix": "/static/",
            "debug": True,  # Set to False in production
            "xsrf_cookies": True,
            "autoescape": "xhtml_escape",
        }

        self.web_app = web.Application(handlers, **settings)
        self.web_app.settings["jupytercluster"] = self

    async def create_hub(
        self,
        name: str,
        owner: str,
        values: Optional[Dict] = None,
        description: str = "",
    ) -> HubInstance:
        """Create a new hub instance

        SECURITY:
        - Namespace is derived from hub name, not user input
        - Values are validated and sanitized in HubSpawner
        - Owner is stored and used for permission checks
        """
        # CRITICAL: Generate namespace from hub name (not user input)
        # This ensures users cannot deploy to arbitrary namespaces
        namespace = f"{self.default_namespace_prefix}{name}"

        # Validate namespace name (Kubernetes requirements)
        if not self._is_valid_namespace_name(namespace):
            raise ValueError(f"Invalid namespace name: {namespace}")

        # Check if namespace already exists (one hub per namespace)
        if namespace in [h.namespace for h in self.hubs.values()]:
            raise ValueError(f"Namespace {namespace} already in use")

        # Check user's hub limit (if configured)
        user_hubs = [h for h in self.hubs.values() if h.owner == owner]
        # Get user from database to check limits
        user = self.db.query(orm.User).filter_by(name=owner).first()
        if user and user.max_hubs and len(user_hubs) >= user.max_hubs:
            raise ValueError(f"User {owner} has reached maximum hub limit of {user.max_hubs}")

        # Validate namespace prefix restrictions (if configured)
        if user and user.allowed_namespace_prefixes:
            allowed = any(
                namespace.startswith(prefix) for prefix in user.allowed_namespace_prefixes
            )
            if not allowed:
                raise ValueError(f"User {owner} is not allowed to deploy to namespace {namespace}")

        # Generate Helm release name
        helm_release_name = f"jupyterhub-{name}"

        # Create ORM object
        orm_hub = orm.Hub(
            name=name,
            namespace=namespace,
            owner=owner,
            helm_release_name=helm_release_name,
            helm_chart=self.default_helm_chart,
            values=values or {},
            description=description,
            status="pending",
        )

        self.db.add(orm_hub)
        self.db.commit()

        # Create HubInstance
        hub = HubInstance(orm_hub)
        self.hubs[name] = hub

        logger.info(f"Created hub {name} for owner {owner}")
        return hub

    async def delete_hub(self, name: str):
        """Delete a hub instance"""
        if name not in self.hubs:
            raise ValueError(f"Hub {name} not found")

        hub = self.hubs[name]

        # Stop hub if running
        if hub.status == "running":
            await hub.stop()

        # Delete from database
        self.db.delete(hub.orm_hub)
        self.db.commit()

        # Remove from cache
        del self.hubs[name]

        logger.info(f"Deleted hub {name}")

    def _is_valid_namespace_name(self, name: str) -> bool:
        """Validate Kubernetes namespace name"""
        # Kubernetes namespace names must be:
        # - lowercase alphanumeric characters or '-'
        # - start and end with alphanumeric
        # - max 63 characters
        if len(name) > 63:
            return False
        if not name[0].isalnum() or not name[-1].isalnum():
            return False
        return all(c.isalnum() or c == "-" for c in name)

    def start(self):
        """Start the JupyterCluster server"""
        logger.info(f"Starting JupyterCluster on {self.ip}:{self.port}")
        self.web_app.listen(self.port, address=self.ip)
        IOLoop.current().start()


    def _init_cookie_secret(self):
        """Initialize or load cookie secret from database"""
        if self.cookie_secret:
            # Use configured secret
            return
        
        # Try to load from database
        config_entry = self.db.query(orm.Config).filter_by(key="cookie_secret").first()
        if config_entry:
            self._cookie_secret = config_entry.value
        else:
            # Generate new secret and store it
            secret = os.urandom(32).hex()
            config_entry = orm.Config(key="cookie_secret", value=secret)
            self.db.add(config_entry)
            self.db.commit()
            self._cookie_secret = secret
        logger.info("Cookie secret initialized")

    def _get_cookie_secret(self) -> str:
        """Get cookie secret (from config or database)"""
        if self.cookie_secret:
            return self.cookie_secret
        return getattr(self, "_cookie_secret", os.urandom(32).hex())


class HealthHandler(APIHandler):
    """Health check endpoint"""

    def get(self):
        self.write({"status": "ok"})


def main():
    """Main entry point"""
    app = JupyterCluster()
    app.initialize()
    app.start()


if __name__ == "__main__":
    main()
