"""Tests for API handlers"""

import pytest
from unittest.mock import Mock, patch
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application
from jupytercluster.app import JupyterCluster, HealthHandler
from jupytercluster.api.hubs import HubListAPIHandler, HubAPIHandler


class TestAPIHandlers(AsyncHTTPTestCase):
    """Test API handlers"""

    def get_app(self):
        """Create test application"""
        # Mock JupyterCluster app
        app = Application(
            [
                (r"/api/hubs", HubListAPIHandler),
                (r"/api/hubs/([^/]+)", HubAPIHandler),
                (r"/api/health", HealthHandler),
            ]
        )

        # Mock jupytercluster settings
        mock_app = Mock()
        mock_app.hubs = {}
        app.settings["jupytercluster"] = mock_app

        return app

    def test_health_endpoint(self):
        """Test health endpoint"""
        response = self.fetch("/api/health")
        assert response.code == 200

    @patch("jupytercluster.api.base.APIHandler.get_current_user")
    def test_list_hubs(self, mock_user):
        """Test listing hubs"""
        mock_user.return_value = "test-user"

        response = self.fetch("/api/hubs", headers={"X-User": "test-user"})
        assert response.code == 200

