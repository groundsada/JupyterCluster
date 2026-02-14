"""Login handlers for JupyterCluster"""

import logging
from urllib.parse import urlparse, urlunparse

from tornado import web
from tornado.auth import OAuth2Mixin
from tornado.httputil import url_concat

from .base import BaseHandler

logger = logging.getLogger(__name__)


class LoginHandler(BaseHandler):
    """Handle login page and OAuth flow"""

    async def get(self):
        """Render login page or redirect to OAuth"""
        # If already logged in, redirect to home
        if self.current_user:
            self.redirect("/")
            return

        # Check if using OAuth authenticator
        authenticator = self.jupytercluster.authenticator
        if hasattr(authenticator, "oauthenticator"):
            # Redirect to OAuth login handler
            self.redirect("/oauth_login")
            return

        # Render login form for non-OAuth authenticators
        # Pass login_service and authenticator_login_url as None for simple auth
        self.render_template(
            "login.html",
            login_service=None,
            authenticator_login_url=None,
        )

    async def post(self):
        """Handle form-based login"""
        username = self.get_argument("username", "")
        password = self.get_argument("password", "")

        if not username or not password:
            self.render_template(
                "login.html",
                login_error="Username and password required",
                login_service=None,
                authenticator_login_url=None,
            )
            return

        # Authenticate
        authenticated_user = self.jupytercluster.authenticator.authenticate(username, password)

        if authenticated_user:
            # Set secure cookie
            self.set_secure_cookie("jupytercluster_user", authenticated_user)
            # Redirect to home
            next_url = self.get_argument("next", "/")
            self.redirect(next_url)
        else:
            self.render_template(
                "login.html",
                login_error="Invalid username or password",
                login_service=None,
                authenticator_login_url=None,
            )


class LogoutHandler(BaseHandler):
    """Handle logout"""

    async def get(self):
        """Log out user"""
        if self.current_user:
            self.log.info(f"User logged out: {self.current_user}")

        # Clear cookie
        self.clear_cookie("jupytercluster_user")

        # Redirect to login
        self.redirect("/login")
