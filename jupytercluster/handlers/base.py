"""Base handler for JupyterCluster web interface"""

import logging
from typing import Optional

from tornado import web
from tornado.log import access_log

logger = logging.getLogger(__name__)


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

        # Render template
        return self.render(name, **kwargs)
