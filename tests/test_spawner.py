"""Tests for HubSpawner"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from jupytercluster.spawner import HubSpawner


class TestHubSpawner:
    """Test HubSpawner"""

    @pytest.fixture
    def spawner(self):
        """Create a HubSpawner instance"""
        with patch("jupytercluster.spawner.config.load_incluster_config"):
            spawner = HubSpawner(
                hub_name="test-hub", namespace="jupyterhub-test-hub", owner="test-user"
            )
            return spawner

    def test_validate_helm_values_whitelist(self, spawner):
        """Test Helm values whitelist validation"""
        values = {
            "hub": {"config": {}},
            "proxy": {"service": {}},
            "forbidden": {"key": "value"},  # Should be removed
        }

        sanitized = spawner._validate_helm_values(values)

        assert "hub" in sanitized
        assert "proxy" in sanitized
        assert "forbidden" not in sanitized

    def test_validate_helm_values_namespace_removal(self, spawner):
        """Test namespace override removal"""
        values = {"namespace": "malicious-namespace", "hub": {"config": {}}}

        sanitized = spawner._validate_helm_values(values)

        assert "namespace" not in sanitized
        assert "hub" in sanitized

    def test_validate_helm_values_rbac_protection(self, spawner):
        """Test RBAC protection"""
        values = {"rbac": {"clusterRoleBindings": [{"name": "hack"}], "enabled": True}}

        sanitized = spawner._validate_helm_values(values)

        if "rbac" in sanitized:
            assert "clusterRoleBindings" not in sanitized["rbac"]

    @pytest.mark.asyncio
    async def test_poll_running(self, spawner):
        """Test poll when hub is running"""
        with patch.object(spawner.core_v1, "read_namespace") as mock_ns, patch.object(
            spawner.core_v1, "list_namespaced_pod"
        ) as mock_pods:

            mock_ns.return_value = Mock()
            mock_pod = Mock()
            mock_pod.metadata.name = "jupyterhub-hub"
            mock_pod.status.phase = "Running"
            mock_pods.return_value.items = [mock_pod]

            result = await spawner.poll()
            assert result is None  # Running

    @pytest.mark.asyncio
    async def test_poll_stopped(self, spawner):
        """Test poll when hub is stopped"""
        from kubernetes.client.rest import ApiException

        with patch.object(spawner.core_v1, "read_namespace") as mock_ns:
            mock_ns.side_effect = ApiException(status=404)

            result = await spawner.poll()
            assert result == 1  # Stopped
