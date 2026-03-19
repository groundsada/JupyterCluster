"""Shared fixtures for the JupyterCluster E2E test suite.

The suite expects two environment variables set by the CI workflow:

    JUPYTERCLUSTER_URL    Base URL of the deployed instance, e.g. http://localhost:8080
    JUPYTERCLUSTER_TOKEN  Bearer token for the pre-seeded admin user

All fixtures use ``requests.Session`` so HTTP overhead is minimised and
connection-reuse happens automatically across tests in the same process.
"""

import os
import subprocess
import time

import pytest
import requests

# ---------------------------------------------------------------------------
# Environment / configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("JUPYTERCLUSTER_URL", "http://localhost:8080").rstrip("/")
ADMIN_TOKEN = os.environ.get("JUPYTERCLUSTER_TOKEN", "")

# How long to wait (seconds) for a hub to reach a target status.
# Deploying a minimal JupyterHub on kind takes roughly 90–180 s.
HUB_READY_TIMEOUT = int(os.environ.get("HUB_READY_TIMEOUT", "300"))
HUB_POLL_INTERVAL = 10


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def api_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def admin_session() -> requests.Session:
    """requests.Session pre-configured with the admin Bearer token."""
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Content-Type": "application/json",
        }
    )
    s.timeout = 30
    return s


@pytest.fixture(scope="session")
def anon_session() -> requests.Session:
    """Unauthenticated session — used to verify public endpoints."""
    s = requests.Session()
    s.timeout = 10
    return s


# ---------------------------------------------------------------------------
# Helpers surfaced as fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def wait_for_hub():
    """Return a callable that polls hub status until it matches *target_status*."""

    def _wait(session: requests.Session, base_url: str, hub_name: str, target_status: str):
        deadline = time.time() + HUB_READY_TIMEOUT
        last_status = None
        while time.time() < deadline:
            r = session.get(f"{base_url}/api/hubs/{hub_name}", timeout=10)
            if r.status_code == 200:
                last_status = r.json().get("status")
                if last_status == target_status:
                    return last_status
            time.sleep(HUB_POLL_INTERVAL)
        raise TimeoutError(
            f"Hub {hub_name!r} did not reach status {target_status!r} "
            f"within {HUB_READY_TIMEOUT}s (last: {last_status!r})"
        )

    return _wait


@pytest.fixture(scope="session")
def kubectl():
    """Return a callable that runs kubectl and returns (stdout, returncode)."""

    def _kubectl(*args: str, check: bool = False) -> tuple:
        result = subprocess.run(
            ["kubectl", *args],
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"kubectl {' '.join(args)} failed:\n{result.stderr}"
            )
        return result.stdout.strip(), result.returncode

    return _kubectl


@pytest.fixture(scope="session")
def namespace_exists(kubectl):
    """Return a callable that checks whether a K8s namespace exists."""

    def _exists(name: str) -> bool:
        _, rc = kubectl("get", "namespace", name)
        return rc == 0

    return _exists


@pytest.fixture(scope="session")
def helm_release_exists():
    """Return a callable that checks whether a Helm release exists in a namespace."""

    def _exists(release: str, namespace: str) -> bool:
        result = subprocess.run(
            ["helm", "status", release, "--namespace", namespace],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    return _exists
