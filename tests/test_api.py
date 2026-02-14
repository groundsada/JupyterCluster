"""Tests for API handlers"""

import json
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
        data = json.loads(response.body)
        assert data["status"] == "ok"

    def test_health_endpoint_readiness(self):
        """Test health endpoint for Kubernetes readiness probe (GET and HEAD)"""
        # Test GET request
        response = self.fetch("/api/health", method="GET")
        assert response.code == 200, f"Health endpoint returned {response.code}, expected 200"
        data = json.loads(response.body)
        assert data["status"] == "ok", f"Health status is {data.get('status')}, expected 'ok'"

        # Test HEAD request (common for readiness probes)
        response = self.fetch("/api/health", method="HEAD", raise_error=False)
        assert response.code == 200, f"Health HEAD returned {response.code}, expected 200"

    def test_health_endpoint_connection(self):
        """Test that health endpoint is reachable and responds quickly"""
        import time

        start = time.time()
        response = self.fetch("/api/health", connect_timeout=5, request_timeout=5)
        elapsed = time.time() - start

        assert response.code == 200
        assert elapsed < 1.0, f"Health endpoint took {elapsed}s, expected < 1s"
        data = json.loads(response.body)
        assert "status" in data

    @patch("jupytercluster.api.base.APIHandler.get_current_user")
    def test_list_hubs(self, mock_user):
        """Test listing hubs"""
        mock_user.return_value = "test-user"

        response = self.fetch("/api/hubs", headers={"X-User": "test-user"})
        assert response.code == 200
