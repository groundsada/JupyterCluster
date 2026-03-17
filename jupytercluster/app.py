"""Main JupyterCluster application"""

import asyncio
import json
import logging
import os
from typing import Dict, Optional

from tornado import web
from tornado.ioloop import IOLoop, PeriodicCallback
from traitlets import Bool
from traitlets import Dict as TraitDict
from traitlets import Integer
from traitlets import List as TraitList
from traitlets import Unicode, default
from traitlets.config import Application

from . import orm
from .api.base import APIHandler
from .api.events import HubEventsAPIHandler
from .api.hubs import HubActionAPIHandler, HubAPIHandler, HubListAPIHandler
from .api.info import InfoAPIHandler
from .api.tokens import UserTokenAPIHandler, UserTokenListAPIHandler
from .api.users import UserAPIHandler, UserListAPIHandler
from .auth import Authenticator, OAuthenticatorWrapper, SimpleAuthenticator
from .handlers.admin import AdminHandler
from .handlers.error import NotFoundHandler
from .handlers.home import HomeHandler
from .handlers.hubs import HubCreateHandler, HubDetailHandler
from .handlers.login import LoginHandler, LogoutHandler
from .handlers.profile import ProfileHandler
from .hub import HubInstance

try:
    from .handlers.oauth import OAuthCallbackHandler, OAuthLoginHandler
except ImportError:
    OAuthCallbackHandler = None
    OAuthLoginHandler = None

logger = logging.getLogger(__name__)

# Default schema for the hub values GUI editor.
# Admins can override this via JUPYTERCLUSTER_HUB_VALUES_SCHEMA (JSON string or file path).
DEFAULT_HUB_VALUES_SCHEMA = {
    "groups": [
        {
            "title": "Proxy",
            "fields": [
                {
                    "label": "Service Type",
                    "path": "proxy.service.type",
                    "type": "select",
                    "default": "ClusterIP",
                    "options": ["ClusterIP", "NodePort"],
                    "help": "ClusterIP is required on most managed clusters. LoadBalancer is blocked by admission webhooks on many clusters.",
                }
            ],
        },
        {
            "title": "Hub Database",
            "fields": [
                {
                    "label": "DB Type",
                    "path": "hub.db.type",
                    "type": "select",
                    "default": "sqlite-memory",
                    "options": ["sqlite-memory", "sqlite-pvc"],
                    "help": "sqlite-memory is ephemeral (lost on restart). sqlite-pvc persists data to a PVC.",
                },
                {
                    "label": "Storage Class",
                    "path": "hub.db.pvc.storageClassName",
                    "type": "text",
                    "default": "standard",
                    "showWhen": {"path": "hub.db.type", "value": "sqlite-pvc"},
                },
                {
                    "label": "PVC Size",
                    "path": "hub.db.pvc.storage",
                    "type": "text",
                    "default": "1Gi",
                    "showWhen": {"path": "hub.db.type", "value": "sqlite-pvc"},
                },
            ],
        },
        {
            "title": "Single-User Image",
            "fields": [
                {
                    "label": "Image Name",
                    "path": "singleuser.image.name",
                    "type": "text",
                    "placeholder": "quay.io/jupyter/scipy-notebook",
                },
                {
                    "label": "Image Tag",
                    "path": "singleuser.image.tag",
                    "type": "text",
                    "placeholder": "latest",
                },
            ],
        },
        {
            "title": "Single-User Resources",
            "fields": [
                {
                    "label": "CPU Limit",
                    "path": "singleuser.cpu.limit",
                    "type": "number",
                    "step": 0.1,
                    "placeholder": "2",
                },
                {
                    "label": "CPU Guarantee",
                    "path": "singleuser.cpu.guarantee",
                    "type": "number",
                    "step": 0.1,
                    "placeholder": "0.5",
                },
                {
                    "label": "Memory Limit",
                    "path": "singleuser.memory.limit",
                    "type": "text",
                    "placeholder": "4G",
                },
                {
                    "label": "Memory Guarantee",
                    "path": "singleuser.memory.guarantee",
                    "type": "text",
                    "placeholder": "1G",
                },
            ],
        },
        {
            "title": "Single-User Storage",
            "fields": [
                {
                    "label": "Storage Type",
                    "path": "singleuser.storage.type",
                    "type": "select",
                    "default": "none",
                    "options": ["none", "dynamic"],
                    "help": "dynamic provisions a persistent PVC per user.",
                },
                {
                    "label": "Storage Class",
                    "path": "singleuser.storage.dynamic.storageClass",
                    "type": "text",
                    "default": "standard",
                    "showWhen": {"path": "singleuser.storage.type", "value": "dynamic"},
                },
                {
                    "label": "Storage Size",
                    "path": "singleuser.storage.capacity",
                    "type": "text",
                    "default": "5Gi",
                    "showWhen": {"path": "singleuser.storage.type", "value": "dynamic"},
                },
            ],
        },
    ]
}


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

    allow_user_namespace_management = Bool(
        True,
        help=(
            "Allow non-admin users to create/delete hubs (which create/delete namespaces). "
            "When False, only admins can create or delete hubs. "
            "Can be overridden per-user via can_create_namespaces / can_delete_namespaces."
        ),
    ).tag(config=True)

    cors_allow_origins = TraitList(
        [],
        help=(
            "Origins permitted for CORS requests to the API. "
            "Use ['*'] to allow all origins (not recommended in production). "
            "Empty list (default) disables CORS headers."
        ),
    ).tag(config=True)

    hub_values_schema = TraitDict(
        {},
        help=(
            "JSON schema driving the hub values GUI editor. "
            "When empty, the built-in DEFAULT_HUB_VALUES_SCHEMA is used. "
            "Set via JUPYTERCLUSTER_HUB_VALUES_SCHEMA env var (JSON string or path to a JSON file)."
        ),
    ).tag(config=True)

    allow_namespace_deletion = Bool(
        False,
        help=(
            "When True, deleting a hub also deletes its Kubernetes namespace. "
            "When False (default), the namespace is left intact after hub deletion."
        ),
    ).tag(config=True)

    # Background polling — mirrors JupyterHub's poll_interval on spawners
    poll_interval = Integer(
        30,
        help=(
            "Interval in seconds between hub health-status polls. "
            "Set to 0 to disable background polling."
        ),
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

        # Mirrors JupyterHub: all PeriodicCallback instances stored here so they
        # can be started / stopped as a group and inspected in tests.
        self._periodic_callbacks: Dict[str, PeriodicCallback] = {}

        # Apply env var overrides for bool settings not handled by traitlets env loading
        self._apply_env_overrides()

        # Initialize database
        self._init_database()

        # Initialize authenticator
        self._init_authenticator()

        # Load hubs from database
        self.hubs: Dict[str, HubInstance] = {}
        self._load_hubs()

        # Initialize web application
        self._init_web_app()

    def _apply_env_overrides(self):
        """Apply environment variable overrides for settings not covered by traitlets config loading"""

        def _parse_bool(val: str) -> bool:
            return val.lower() in ("true", "1", "yes")

        env = os.environ
        if "JUPYTERCLUSTER_DB_URL" in env:
            self.db_url = env["JUPYTERCLUSTER_DB_URL"]
        if "JUPYTERCLUSTER_DEFAULT_NAMESPACE_PREFIX" in env:
            self.default_namespace_prefix = env["JUPYTERCLUSTER_DEFAULT_NAMESPACE_PREFIX"]
        if "JUPYTERCLUSTER_ALLOW_USER_NAMESPACE_MANAGEMENT" in env:
            self.allow_user_namespace_management = _parse_bool(
                env["JUPYTERCLUSTER_ALLOW_USER_NAMESPACE_MANAGEMENT"]
            )
        if "JUPYTERCLUSTER_ALLOW_NAMESPACE_DELETION" in env:
            self.allow_namespace_deletion = _parse_bool(
                env["JUPYTERCLUSTER_ALLOW_NAMESPACE_DELETION"]
            )
        if "JUPYTERCLUSTER_HUB_VALUES_SCHEMA" in env:
            raw = env["JUPYTERCLUSTER_HUB_VALUES_SCHEMA"].strip()
            if raw:
                # Support both inline JSON and a file path
                if raw.startswith("{") or raw.startswith("["):
                    try:
                        self.hub_values_schema = json.loads(raw)
                    except json.JSONDecodeError as e:
                        logger.warning("Could not parse JUPYTERCLUSTER_HUB_VALUES_SCHEMA: %s", e)
                else:
                    # Treat as file path
                    try:
                        with open(raw) as f:
                            self.hub_values_schema = json.load(f)
                    except (OSError, json.JSONDecodeError) as e:
                        logger.warning(
                            "Could not load JUPYTERCLUSTER_HUB_VALUES_SCHEMA from %s: %s", raw, e
                        )
        if "JUPYTERCLUSTER_CORS_ALLOW_ORIGINS" in env:
            raw = env["JUPYTERCLUSTER_CORS_ALLOW_ORIGINS"].strip()
            if raw:
                self.cors_allow_origins = [o.strip() for o in raw.split(",") if o.strip()]

    def get_hub_values_schema(self) -> dict:
        """Return the effective hub values schema (custom or built-in default)."""
        return self.hub_values_schema if self.hub_values_schema else DEFAULT_HUB_VALUES_SCHEMA

    def apply_schema_fixed_values(self, values: dict) -> dict:
        """Apply schema-defined fixed values on top of user-supplied values.

        Fixed values (schema.fixed) are always enforced regardless of what the
        user submits — both through the GUI and the API.
        """
        schema = self.get_hub_values_schema()
        fixed = schema.get("fixed", {})
        if not fixed:
            return values

        def set_nested(obj, path, value):
            parts = path.split(".")
            for part in parts[:-1]:
                obj = obj.setdefault(part, {})
            obj[parts[-1]] = value

        result = dict(values)  # shallow copy; deep paths are set via set_nested
        for path, value in fixed.items():
            set_nested(result, path, value)
        logger.debug("Applied %d fixed schema value(s)", len(fixed))
        return result

    def _can_user_create_namespace(self, username: str) -> bool:
        """Check whether a user is permitted to create namespaces (and thus hubs).

        Resolution order:
        1. Admins are always allowed.
        2. Per-user ``can_create_namespaces`` if set (not None).
        3. Global ``allow_user_namespace_management``.
        """
        user = self.db.query(orm.User).filter_by(name=username).first()
        if user and user.admin:
            return True
        if user and user.can_create_namespaces is not None:
            return bool(user.can_create_namespaces)
        return self.allow_user_namespace_management

    def _can_user_delete_namespace(self, username: str) -> bool:
        """Check whether a user is permitted to delete namespaces (and thus hubs).

        Resolution order:
        1. Admins are always allowed.
        2. Per-user ``can_delete_namespaces`` if set (not None).
        3. Global ``allow_user_namespace_management``.
        """
        user = self.db.query(orm.User).filter_by(name=username).first()
        if user and user.admin:
            return True
        if user and user.can_delete_namespaces is not None:
            return bool(user.can_delete_namespaces)
        return self.allow_user_namespace_management

    def _init_database(self):
        """Initialize database, run Alembic migrations, and open a session.

        Follows JupyterHub's database initialisation pattern:
        1. Run ``alembic upgrade head`` so pending migrations are applied before
           any ORM code touches the schema.
        2. Create the engine + session factory via ``dbutil.new_session_factory``
           which sets expire_on_commit=False and pool_pre_ping=True (pessimistic
           disconnect handling), mirroring JupyterHub's orm.new_session_factory().
        3. Call ``Base.metadata.create_all`` as a safety net for any tables that
           Alembic might have missed (e.g. on a brand-new SQLite file).
        """
        from .dbutil import new_session_factory, upgrade

        # --- Feature 3: Alembic migrations ---
        try:
            upgrade(self.db_url)
        except Exception as e:
            # On a brand-new database the alembic_version table won't exist yet;
            # create_all below will bootstrap the schema, so we just warn here.
            logger.warning("Alembic upgrade skipped: %s", e)

        # --- Feature 2: Consistent session factory (JupyterHub pattern) ---
        self.engine, _session_factory = new_session_factory(self.db_url)
        # create_all is idempotent; guards against the alembic-skip case above
        orm.Base.metadata.create_all(self.engine)
        # Single session instance shared across all handlers via app reference —
        # identical to JupyterHub's self.db = self.session_factory()
        self.db = _session_factory()

        # Initialize or load cookie secret
        self._init_cookie_secret()

        # Initialize default users from environment
        self._init_users()

    def _init_users(self):
        """Initialize default users from environment variable"""
        users_env = os.getenv("JUPYTERCLUSTER_DEFAULT_USERS")
        if not users_env:
            return

        try:
            users_config = json.loads(users_env)
            for username, user_data in users_config.items():
                # Check if user already exists
                user = self.db.query(orm.User).filter_by(name=username).first()
                if not user:
                    user = orm.User(
                        name=username,
                        admin=user_data.get("admin", False),
                        allowed_namespaces=user_data.get("allowed_namespaces", []),
                        max_hubs=user_data.get("max_hubs"),
                        can_create_namespaces=user_data.get("can_create_namespaces"),
                        can_delete_namespaces=user_data.get("can_delete_namespaces"),
                    )
                    self.db.add(user)
                    logger.info(
                        f"Created default user: {username} (admin={user.admin}, "
                        f"namespaces={user.allowed_namespaces}, "
                        f"can_create={user.can_create_namespaces}, can_delete={user.can_delete_namespaces})"
                    )
                else:
                    # Update existing user
                    user.admin = user_data.get("admin", user.admin)
                    user.allowed_namespaces = user_data.get(
                        "allowed_namespaces", user.allowed_namespaces
                    )
                    user.max_hubs = user_data.get("max_hubs", user.max_hubs)
                    if "can_create_namespaces" in user_data:
                        user.can_create_namespaces = user_data["can_create_namespaces"]
                    if "can_delete_namespaces" in user_data:
                        user.can_delete_namespaces = user_data["can_delete_namespaces"]
                    logger.info(f"Updated default user: {username}")
            self.db.commit()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse default users from environment: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize default users: {e}")
            self.db.rollback()

    def _init_authenticator(self):
        """Initialize authenticator"""
        authenticator_class = self._load_class(self.authenticator_class, Authenticator)
        self.authenticator = authenticator_class(parent=self)

        # Load users from environment if available (for SimpleAuthenticator)
        if isinstance(self.authenticator, SimpleAuthenticator):
            users_env = os.getenv("JUPYTERCLUSTER_SIMPLEAUTHENTICATOR_USERS")
            if users_env:
                try:
                    users_dict = json.loads(users_env)
                    self.authenticator.users = users_dict
                    logger.info(f"Loaded {len(users_dict)} users from environment")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse users from environment: {e}")

            admin_users_env = os.getenv("JUPYTERCLUSTER_SIMPLEAUTHENTICATOR_ADMIN_USERS")
            if admin_users_env:
                try:
                    admin_users_dict = json.loads(admin_users_env)
                    self.authenticator.admin_users = admin_users_dict
                    logger.info(f"Loaded admin users from environment")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse admin_users from environment: {e}")

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
            (r"/profile", ProfileHandler),
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
            # API — info (unauthenticated)
            (r"/api/info", InfoAPIHandler),
            # API — hubs (more-specific routes first to avoid capture by the generic one)
            (r"/api/hubs", HubListAPIHandler),
            (r"/api/hubs/([^/]+)/events", HubEventsAPIHandler),
            (r"/api/hubs/([^/]+)/(start|stop)", HubActionAPIHandler),
            (r"/api/hubs/([^/]+)", HubAPIHandler),
            # API — users + tokens (token routes before user route to avoid /tokens being a username)
            (r"/api/users", UserListAPIHandler),
            (r"/api/users/([^/]+)/tokens", UserTokenListAPIHandler),
            (r"/api/users/([^/]+)/tokens/([^/]+)", UserTokenAPIHandler),
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
        namespace: Optional[str] = None,
    ) -> HubInstance:
        """Create a new hub instance

        SECURITY:
        - Namespace is validated against user's allowed_namespaces
        - If namespace not provided, it's derived from hub name using default_namespace_prefix
        - Values are validated and sanitized in HubSpawner
        - Owner is stored and used for permission checks
        """
        # Use provided namespace or derive from hub name
        if namespace is None:
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

        # Check namespace creation permission
        if not self._can_user_create_namespace(owner):
            raise ValueError(
                f"User {owner} is not allowed to create namespaces/hubs. "
                "Contact an administrator to enable this permission."
            )

        # Validate namespace restrictions (if configured) - exact match
        if user and user.allowed_namespaces:
            if namespace not in user.allowed_namespaces:
                raise ValueError(
                    f"User {owner} is not allowed to deploy to namespace {namespace}. Allowed namespaces: {user.allowed_namespaces}"
                )

        # Generate Helm release name
        helm_release_name = f"jupyterhub-{name}"

        # Validate and sanitize values before storing
        # This ensures httpRoute is disabled, extraVolumes/extraVolumeMounts are fixed, etc.
        from .spawner import HubSpawner

        temp_spawner = HubSpawner(
            hub_name=name,
            namespace=namespace,
            owner=owner,
            helm_chart=self.default_helm_chart,
        )
        # Apply fixed schema values on top (enforced regardless of user input)
        user_values = self.apply_schema_fixed_values(values or {})
        sanitized_values = temp_spawner._validate_helm_values(user_values)

        # Auto-enable ingress/httpRoute when hosts are configured so the stored
        # values always include enabled=true (spawner.start() does the same as a
        # safety net, but we also want the persisted values to be correct).
        _ingress = sanitized_values.get("ingress") or {}
        if isinstance(_ingress, dict) and _ingress.get("hosts"):
            _ingress["enabled"] = True
            sanitized_values["ingress"] = _ingress
        _http_route = sanitized_values.get("httpRoute") or {}
        if isinstance(_http_route, dict) and _http_route.get("hostnames"):
            _http_route["enabled"] = True
            sanitized_values["httpRoute"] = _http_route

        # Create ORM object
        orm_hub = orm.Hub(
            name=name,
            namespace=namespace,
            owner=owner,
            helm_release_name=helm_release_name,
            helm_chart=self.default_helm_chart,
            values=sanitized_values,  # Store sanitized values
            description=description,
            status="pending",
        )

        try:
            self.db.add(orm_hub)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to create hub {name} in database: {e}")
            raise

        # Create HubInstance
        hub = HubInstance(orm_hub)
        self.hubs[name] = hub

        logger.info(f"Created hub {name} for owner {owner}")
        return hub

    async def delete_hub(self, name: str, caller: Optional[str] = None):
        """Delete a hub instance.

        Args:
            name: Hub name to delete.
            caller: Username of the user requesting deletion. Used for permission checks;
                    if None, the check is skipped (e.g., for internal/admin calls).
        """
        if name not in self.hubs:
            raise ValueError(f"Hub {name} not found")

        hub = self.hubs[name]

        # Check namespace deletion permission for non-admin callers
        if caller is not None and not self._can_user_delete_namespace(caller):
            raise ValueError(
                f"User {caller} is not allowed to delete namespaces/hubs. "
                "Contact an administrator to enable this permission."
            )

        namespace = hub.namespace

        # Always uninstall the Helm release regardless of stored status
        # (_delete_helm_release silently ignores "release not found" errors)
        try:
            spawner = hub.get_spawner()
            await spawner._delete_helm_release()
        except Exception as e:
            logger.warning(f"Helm uninstall for {name} failed (continuing): {e}")

        # Optionally delete the Kubernetes namespace
        if self.allow_namespace_deletion:
            try:
                spawner = hub.get_spawner()
                await spawner._delete_namespace(namespace)
                logger.info(f"Deleted namespace {namespace} for hub {name}")
            except Exception as e:
                logger.warning(f"Failed to delete namespace {namespace}: {e}")

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

    # ------------------------------------------------------------------
    # Feature 1: Startup reconciliation (mirrors JupyterHub init_spawners)
    # ------------------------------------------------------------------

    async def reconcile_hubs(self) -> None:
        """Reconcile DB hub states against actual Kubernetes state on startup.

        Mirrors JupyterHub's ``init_spawners()``:
        - Queries every hub whose stored status implies it might be running.
        - Calls ``spawner.poll()`` on each in parallel (asyncio.gather).
        - Updates the DB to reflect reality before serving any requests.

        This guards against stale "running" records left over from a crashed
        or forcibly-restarted JupyterCluster instance.
        """
        active = [h for h in self.hubs.values() if h.status in ("running", "pending", "stopping")]
        if not active:
            logger.info("Startup reconciliation: no active hubs to check.")
            return

        logger.info("Startup reconciliation: checking %d hub(s)…", len(active))

        async def _check(hub):
            try:
                spawner = hub.get_spawner()
                result = await spawner.poll()
                if result is None:
                    # poll() returns None → still running
                    if hub.status != "running":
                        logger.info(
                            "Reconcile %s: status '%s' → 'running' (actually running)",
                            hub.name,
                            hub.status,
                        )
                        hub.status = "running"
                        hub._save_to_orm()
                else:
                    # poll() returned an exit code → not running
                    if hub.status not in ("stopped", "error"):
                        logger.warning(
                            "Reconcile %s: status '%s' → 'stopped' (not running, exit=%s)",
                            hub.name,
                            hub.status,
                            result,
                        )
                        hub.status = "stopped"
                        hub._save_to_orm()
            except Exception:
                logger.exception("Reconcile: failed to poll hub %s", hub.name)

        # Run all polls concurrently — mirrors JupyterHub's asyncio.gather usage
        await asyncio.gather(*[_check(h) for h in active], return_exceptions=True)

        try:
            self.db.commit()
            logger.info("Startup reconciliation complete.")
        except Exception:
            logger.exception("Reconcile: failed to commit status updates")
            self.db.rollback()

    # ------------------------------------------------------------------
    # Feature 4: Background health polling (mirrors JupyterHub PeriodicCallback)
    # ------------------------------------------------------------------

    def start_polling(self) -> None:
        """Register and start the hub health-polling PeriodicCallback.

        Mirrors JupyterHub's spawner ``start_polling()`` method and the
        application-level ``_periodic_callbacks`` dict pattern used for
        token purging, service health checks, and last-activity updates.

        The callback fires every ``poll_interval`` seconds.  Setting
        ``poll_interval = 0`` disables polling (same as JupyterHub's
        ``poll_interval <= 0`` guard).
        """
        if self.poll_interval <= 0:
            logger.info("Hub health polling disabled (poll_interval=0).")
            return

        pc = PeriodicCallback(self.poll_all_hubs, 1e3 * self.poll_interval)
        self._periodic_callbacks["hub_health"] = pc
        pc.start()
        logger.info("Hub health polling started (interval=%ds).", self.poll_interval)

    async def poll_all_hubs(self) -> None:
        """Poll every running/pending hub and update status in the database.

        Called periodically by the ``hub_health`` PeriodicCallback.  Mirrors
        JupyterHub's ``poll_and_notify()`` on individual spawners, lifted to
        the application level because JupyterCluster manages many hubs rather
        than many single-user servers.

        Any hub that fails its poll is marked 'stopped'; the original error is
        logged but does not abort other polls (return_exceptions=True).
        """
        candidates = [h for h in self.hubs.values() if h.status in ("running", "pending")]
        if not candidates:
            return

        changed = False

        async def _poll(hub):
            nonlocal changed
            try:
                spawner = hub.get_spawner()
                result = await spawner.poll()
                if result is None:
                    if hub.status != "running":
                        hub.status = "running"
                        hub._save_to_orm()
                        changed = True
                else:
                    if hub.status != "stopped":
                        logger.warning("Hub %s stopped unexpectedly (exit=%s).", hub.name, result)
                        hub.status = "stopped"
                        hub._save_to_orm()
                        changed = True
            except Exception:
                logger.exception("Error polling hub %s", hub.name)

        await asyncio.gather(*[_poll(h) for h in candidates], return_exceptions=True)

        if changed:
            try:
                self.db.commit()
            except Exception:
                logger.exception("Failed to commit poll status updates")
                self.db.rollback()

    def start(self):
        """Start the JupyterCluster server"""
        logger.info(f"Starting JupyterCluster on {self.ip}:{self.port}")
        self.web_app.listen(self.port, address=self.ip)

        # --- Feature 1: reconcile hub states before accepting traffic ---
        IOLoop.current().run_sync(self.reconcile_hubs)

        # --- Feature 4: start background health polling ---
        self.start_polling()

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
    """Health check endpoint for Kubernetes readiness/liveness probes"""

    def get(self):
        """GET /api/health - Health check"""
        self.write({"status": "ok"})

    def head(self):
        """HEAD /api/health - Health check (for probes that use HEAD)"""
        self.set_status(200)
        self.finish()


def main():
    """Main entry point"""
    app = JupyterCluster()
    app.initialize()
    app.start()


if __name__ == "__main__":
    main()
