"""Base handler for JupyterCluster web interface"""

import logging
from typing import Any, Dict, Optional

from tornado import web
from tornado.log import access_log

logger = logging.getLogger(__name__)


class DictObject:
    """Dict-like object that supports attribute access for Tornado templates"""

    def __init__(self, d: Dict[str, Any]):
        # Set all dict items as attributes
        for k, v in d.items():
            setattr(self, k, v)
        # Also store as __dict__ for compatibility
        self.__dict__.update(d)

    def __getitem__(self, key):
        """Support dict-style access"""
        return getattr(self, key)

    def get(self, key, default=None):
        """Support dict-style get"""
        return getattr(self, key, default)


class BaseHandler(web.RequestHandler):
    """Base handler with common functionality"""

    @property
    def log(self):
        """Convenience property for logging"""
        return logger

    @property
    def jupytercluster(self):
        """Get JupyterCluster application instance"""
        return self.application.settings.get("jupytercluster")

    @property
    def current_user(self) -> Optional[str]:
        """Get current authenticated user"""
        # Check for user in cookie/session
        user = self.get_secure_cookie("jupytercluster_user")
        if user:
            return user.decode("utf-8")
        return None

    @property
    def is_admin(self) -> bool:
        """Check if current user is admin"""
        user = self.current_user
        if not user:
            return False
        return self.jupytercluster.authenticator.is_admin(user)

    def get_user_or_redirect(self):
        """Get current user or redirect to login"""
        user = self.current_user
        if not user:
            self.redirect("/login")
            return None
        return user

    def render_template(self, name, **kwargs):
        """Render a template with common context"""
        # Add common template variables
        kwargs.setdefault("base_url", "/")
        kwargs.setdefault("user", self.current_user)
        kwargs.setdefault("is_admin", self.is_admin)
        kwargs.setdefault("login_url", "/login")
        kwargs.setdefault("logout_url", "/logout")
        kwargs.setdefault("announcement", None)

        # Login template specific variables
        if name == "login.html":
            kwargs.setdefault("login_service", None)
            kwargs.setdefault("authenticator_login_url", None)
            kwargs.setdefault("login_error", None)
            kwargs.setdefault("username", None)

        # Hub create template specific variables
        if name == "hub_create.html":
            kwargs.setdefault("error", None)
            kwargs.setdefault("allowed_namespace_prefixes", [])
            kwargs.setdefault("max_hubs", None)
            kwargs.setdefault("current_hub_count", 0)
            kwargs.setdefault(
                "default_namespace_prefix",
                (
                    self.jupytercluster.default_namespace_prefix
                    if self.jupytercluster
                    else "jupyterhub-"
                ),
            )

        # Hub detail template specific variables
        if name == "hub_detail.html":
            kwargs.setdefault("error", None)

        # XSRF token for forms
        try:
            kwargs.setdefault("xsrf", self.xsrf_token.decode("utf-8") if self.xsrf_token else "")
        except AttributeError:
            # xsrf_token might not be available in all contexts
            kwargs.setdefault("xsrf", "")

        # Render template
        return self.render(name, **kwargs)
