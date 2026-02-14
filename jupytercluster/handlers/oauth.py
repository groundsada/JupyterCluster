"""OAuth callback handlers for OAuthenticator integration"""

import logging
import secrets
from urllib.parse import urlencode, urlparse

from tornado import web
from tornado.httputil import url_concat

from .base import BaseHandler

logger = logging.getLogger(__name__)


class OAuthLoginHandler(BaseHandler):
    """Handle OAuth login initiation"""

    async def get(self):
        """Initiate OAuth flow"""
        authenticator = self.jupytercluster.authenticator

        # Check if using OAuth authenticator
        if not hasattr(authenticator, "oauthenticator"):
            raise web.HTTPError(400, "OAuth not configured")

        oa = authenticator.oauthenticator

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)
        self.set_secure_cookie("oauth_state", state, expires_days=1)

        # Store next URL if provided
        next_url = self.get_argument("next", "/")
        self.set_secure_cookie("oauth_next", next_url, expires_days=1)

        # Get OAuth authorization URL
        # OAuthenticator typically provides this via get_handlers
        # For now, construct it manually based on OAuthenticator pattern
        if hasattr(oa, "authorize_url"):
            auth_url = oa.authorize_url
        elif hasattr(oa, "get_authorize_url"):
            auth_url = oa.get_authorize_url()
        else:
            # Try to get from OAuthenticator's handler
            # OAuthenticator typically registers at /oauth_login
            auth_url = "/oauth_login"

        # Add state and redirect_uri to auth URL
        redirect_uri = f"{self.request.protocol}://{self.request.host}/oauth_callback"
        auth_url = url_concat(
            auth_url,
            {
                "state": state,
                "redirect_uri": redirect_uri,
            },
        )

        self.redirect(auth_url)


class OAuthCallbackHandler(BaseHandler):
    """Handle OAuth callback from OAuth provider"""

    async def get(self):
        """Process OAuth callback"""
        authenticator = self.jupytercluster.authenticator

        # Check if using OAuth authenticator
        if not hasattr(authenticator, "oauthenticator"):
            raise web.HTTPError(400, "OAuth not configured")

        oa = authenticator.oauthenticator

        # Verify state (CSRF protection)
        state = self.get_argument("state", None)
        cookie_state = self.get_secure_cookie("oauth_state")
        if not state or not cookie_state:
            raise web.HTTPError(400, "Missing OAuth state")

        cookie_state = cookie_state.decode("utf-8")
        if state != cookie_state:
            logger.warning(f"OAuth state mismatch: {state} != {cookie_state}")
            raise web.HTTPError(403, "OAuth state mismatch")

        # Clear state cookie
        self.clear_cookie("oauth_state")

        # Get authorization code
        code = self.get_argument("code", None)
        error = self.get_argument("error", None)

        if error:
            error_description = self.get_argument("error_description", error)
            logger.error(f"OAuth error: {error_description}")
            raise web.HTTPError(400, f"OAuth error: {error_description}")

        if not code:
            raise web.HTTPError(400, "Missing authorization code")

        try:
            # Exchange code for token and get user info
            # OAuthenticator handles this differently, we need to adapt
            username = await self._authenticate_with_oauth(oa, code)

            if not username:
                raise web.HTTPError(500, "Failed to authenticate user")

            # Check if user is allowed
            if hasattr(oa, "allowed_users") and oa.allowed_users:
                if username not in oa.allowed_users:
                    raise web.HTTPError(403, f"User {username} is not allowed")

            # Set user cookie
            self.set_secure_cookie("jupytercluster_user", username)

            # Get next URL
            next_url = self.get_secure_cookie("oauth_next")
            if next_url:
                next_url = next_url.decode("utf-8")
                self.clear_cookie("oauth_next")
            else:
                next_url = "/"

            logger.info(f"User {username} logged in via OAuth")
            self.redirect(next_url)

        except Exception as e:
            logger.error(f"OAuth callback error: {e}", exc_info=True)
            raise web.HTTPError(500, f"OAuth authentication failed: {str(e)}")

    async def _authenticate_with_oauth(self, oa, code):
        """Authenticate using OAuthenticator"""
        # OAuthenticator's authenticate method expects a handler with specific attributes
        # We need to create a mock handler or adapt the flow

        # Try to use OAuthenticator's token exchange
        # Most OAuthenticators have a method to exchange code for token
        if hasattr(oa, "token_for_code"):
            # This is async in newer versions
            token = await oa.token_for_code(code)
        elif hasattr(oa, "_token_for_code"):
            token = await oa._token_for_code(code)
        else:
            # Fallback: try to call authenticate directly
            # OAuthenticator.authenticate expects (handler, data) where data has 'code'
            class MockHandler:
                def __init__(self, request):
                    self.request = request
                    self.settings = {}

            data = {"code": code}
            result = await oa.authenticate(MockHandler(self.request), data)
            if result:
                return result.get("name") or result
            return None

        # Get user info from token
        if hasattr(oa, "user_for_token"):
            user_info = await oa.user_for_token(token)
        elif hasattr(oa, "_user_for_token"):
            user_info = await oa._user_for_token(token)
        else:
            # Try authenticate with token
            class MockHandler:
                def __init__(self, request):
                    self.request = request
                    self.settings = {}

            data = {"access_token": token}
            result = await oa.authenticate(MockHandler(self.request), data)
            if result:
                return result.get("name") or result
            return None

        if user_info:
            # Extract username from user_info
            if isinstance(user_info, dict):
                return user_info.get("name") or user_info.get("username") or user_info.get("login")
            return str(user_info)

        return None
