"""Tests for authentication"""

import pytest
from jupytercluster.auth import SimpleAuthenticator, OAuthenticatorWrapper, Scope


class TestSimpleAuthenticator:
    """Test SimpleAuthenticator"""

    def test_authenticate_success(self):
        """Test successful authentication"""
        auth = SimpleAuthenticator(users={"testuser": "password123"}, admin_users={"admin": True})
        result = auth.authenticate("testuser", "password123")
        assert result == "testuser"

    def test_authenticate_failure(self):
        """Test failed authentication"""
        auth = SimpleAuthenticator(users={"testuser": "password123"})
        result = auth.authenticate("testuser", "wrongpassword")
        assert result is None

    def test_is_admin(self):
        """Test admin check"""
        auth = SimpleAuthenticator(admin_users={"admin": True})
        assert auth.is_admin("admin") is True
        assert auth.is_admin("user") is False


class TestScope:
    """Test Scope class"""

    def test_get_user_scopes_admin(self):
        """Test admin scopes"""
        scopes = Scope.get_user_scopes("admin", is_admin=True)
        assert Scope.ADMIN_ALL in scopes
        assert Scope.HUBS_CREATE in scopes

    def test_get_user_scopes_user(self):
        """Test regular user scopes"""
        scopes = Scope.get_user_scopes("user", is_admin=False)
        assert Scope.SELF in scopes
        assert Scope.ADMIN_ALL not in scopes

    def test_can_manage_hub(self):
        """Test hub management permissions"""
        admin_scopes = Scope.get_user_scopes("admin", is_admin=True)
        user_scopes = Scope.get_user_scopes("user", is_admin=False)

        # Admin can manage any hub
        assert Scope.can_manage_hub(admin_scopes, "other-user", "admin") is True

        # User can manage own hub
        assert Scope.can_manage_hub(user_scopes, "user", "user") is True

        # User cannot manage other's hub
        assert Scope.can_manage_hub(user_scopes, "other-user", "user") is False
