"""Authentication and authorization for JupyterCluster"""

import logging
from typing import List, Optional, Tuple

from traitlets import Bool
from traitlets import Dict as TraitDict
from traitlets import Unicode, default
from traitlets.config import LoggingConfigurable

logger = logging.getLogger(__name__)


class Authenticator(LoggingConfigurable):
    """Base authenticator class for JupyterCluster"""

    admin_users = TraitDict(
        {},
        help="Dictionary of admin users {username: True}",
    ).tag(config=True)

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """Authenticate a user

        Args:
            username: Username
            password: Password

        Returns:
            Username if authenticated, None otherwise
        """
        raise NotImplementedError("Subclasses must implement authenticate()")

    def is_admin(self, username: str) -> bool:
        """Check if user is an admin"""
        return self.admin_users.get(username, False)

    def get_handlers(self, app) -> List[Tuple[str, type]]:
        """Get OAuth handlers if this is an OAuth authenticator

        Returns:
            List of (path, handler_class) tuples
        """
        return []


class SimpleAuthenticator(Authenticator):
    """Simple authenticator for development/testing

    In production, use OAuth, LDAP, or other authenticators
    """

    users = TraitDict(
        {},
        help="Dictionary of users {username: password}",
    ).tag(config=True)

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """Simple password-based authentication"""
        if username in self.users and self.users[username] == password:
            return username
        return None


class OAuthenticatorWrapper(Authenticator):
    """Wrapper for OAuthenticator from oauthenticator package

    This allows JupyterCluster to use any OAuth provider supported by OAuthenticator
    """

    oauthenticator_class = Unicode(
        "oauthenticator.github.GitHubOAuthenticator",
        help="OAuthenticator class to use (e.g., oauthenticator.github.GitHubOAuthenticator)",
    ).tag(config=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._oa = None

    @property
    def oauthenticator(self):
        """Get or create OAuthenticator instance"""
        if self._oa is None:
            # Dynamically import and instantiate OAuthenticator
            module_path, class_name = self.oauthenticator_class.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            oa_class = getattr(module, class_name)

            # Create instance with config
            self._oa = oa_class(parent=self)
        return self._oa

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """OAuth doesn't use username/password"""
        # OAuth flow doesn't use this method
        return None

    def is_admin(self, username: str) -> bool:
        """Check if user is admin"""
        # Check both our admin_users and OAuthenticator's admin_users
        if super().is_admin(username):
            return True

        # Check OAuthenticator's admin_users if it has that attribute
        if hasattr(self.oauthenticator, "admin_users"):
            return username in getattr(self.oauthenticator, "admin_users", set())

        return False

    def get_handlers(self, app) -> List[Tuple[str, type]]:
        """Get OAuth handlers from OAuthenticator"""
        # OAuthenticator provides its own handlers
        # We need to wrap them to work with JupyterCluster
        handlers = []

        # OAuthenticator typically provides login and oauth_callback handlers
        # We'll need to create wrapper handlers that integrate with JupyterCluster
        # For now, return empty - this will be implemented in the handlers

        return handlers


class Scope:
    """Authorization scopes for JupyterCluster"""

    # Hub management scopes
    HUBS_LIST = "hubs:list"
    HUBS_READ = "hubs:read"
    HUBS_CREATE = "hubs:create"
    HUBS_UPDATE = "hubs:update"
    HUBS_DELETE = "hubs:delete"

    # Admin scopes
    ADMIN_ALL = "admin"
    ADMIN_HUBS = "admin:hubs"
    ADMIN_USERS = "admin:users"

    # User scopes
    SELF = "self"  # Access to own resources

    @staticmethod
    def get_user_scopes(username: str, is_admin: bool) -> set:
        """Get scopes for a user"""
        if is_admin:
            return {
                Scope.ADMIN_ALL,
                Scope.HUBS_LIST,
                Scope.HUBS_READ,
                Scope.HUBS_CREATE,
                Scope.HUBS_UPDATE,
                Scope.HUBS_DELETE,
                Scope.SELF,
            }
        else:
            return {
                Scope.SELF,
                Scope.HUBS_LIST,
                Scope.HUBS_READ,
                Scope.HUBS_CREATE,
                Scope.HUBS_UPDATE,
                Scope.HUBS_DELETE,
            }

    @staticmethod
    def can_manage_hub(scopes: set, hub_owner: str, username: str) -> bool:
        """Check if user can manage a specific hub"""
        if Scope.ADMIN_ALL in scopes:
            return True
        if Scope.SELF in scopes and hub_owner == username:
            return True
        return False
