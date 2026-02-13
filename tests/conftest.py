"""Pytest configuration and fixtures"""

import pytest
from unittest.mock import Mock, MagicMock


@pytest.fixture
def mock_k8s_client():
    """Mock Kubernetes client"""
    mock = MagicMock()
    return mock


@pytest.fixture
def mock_db():
    """Mock database session"""
    mock = MagicMock()
    return mock


@pytest.fixture
def sample_hub_data():
    """Sample hub data for testing"""
    return {
        "name": "test-hub",
        "namespace": "jupyterhub-test-hub",
        "owner": "test-user",
        "helm_release_name": "jupyterhub-test-hub",
        "status": "pending",
    }

