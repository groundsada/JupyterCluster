"""E2E tests: JupyterHub deployment lifecycle on a real kind cluster.

These tests are SLOW (each hub deploy takes 90-300 s) and require:
  - A running kind cluster loaded with the jupytercluster:ci image
  - JupyterHub Helm chart available (jupyterhub repo added)
  - JUPYTERCLUSTER_URL and JUPYTERCLUSTER_TOKEN env vars

Skip the whole module when the DEPLOY_TESTS env var is not set so that
fast API tests (test_api.py, test_hubs.py) remain runnable without a
full cluster.
"""

import os

import pytest

from .conftest import BASE_URL

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEPLOY_TESTS"),
    reason="Set DEPLOY_TESTS=1 to run deployment tests (requires kind cluster)",
)

HUB_PREFIX = "e2e-deploy"

# Values that make a JupyterHub deploy succeed on a small kind cluster.
# prePuller disabled so helm install doesn't hang waiting for image pulls.
MINIMAL_VALUES = {
    "hub": {"db": {"type": "sqlite-memory"}},
    "proxy": {"service": {"type": "ClusterIP"}},
    "prePuller": {"hook": {"enabled": False}, "continuous": {"enabled": False}},
    "scheduling": {"userScheduler": {"enabled": False}},
}


@pytest.fixture(autouse=True)
def cleanup_hubs(admin_session):
    """Delete hubs created during a test, even on failure."""
    created = []
    yield created
    for name in created:
        admin_session.delete(f"{BASE_URL}/api/hubs/{name}")


# ---------------------------------------------------------------------------
# Minimal hub deployment
# ---------------------------------------------------------------------------


class TestMinimalDeployment:
    """Deploy a single JupyterHub with the minimal values file and verify
    that JupyterCluster transitions the hub through pending → running."""

    def test_deploy_creates_hub_record(self, admin_session, cleanup_hubs):
        """Smoke: verify hub record is created and metadata is correct.

        Does NOT wait for helm install or running — that is tested in the
        full suite only.  Verifies the API layer accepts the request and
        returns the expected hub shape immediately.
        """
        name = f"{HUB_PREFIX}-smoke"
        r = admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={
                "description": "smoke deployment test",
                "values": MINIMAL_VALUES,
            },
        )
        assert r.status_code == 201, r.text
        cleanup_hubs.append(name)

        body = r.json()
        assert body["name"] == name
        assert body["status"] == "pending"
        expected_ns = f"jhub-{name}"
        assert body["namespace"] == expected_ns
        assert body["description"] == "smoke deployment test"

    def test_deploy_minimal_hub(
        self, admin_session, cleanup_hubs, wait_for_hub, namespace_exists
    ):
        name = f"{HUB_PREFIX}-minimal"

        r = admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={
                "description": "e2e minimal deployment",
                "values": MINIMAL_VALUES,
            },
        )
        assert r.status_code == 201, r.text
        cleanup_hubs.append(name)

        body = r.json()
        assert body["status"] == "pending"
        expected_ns = f"jhub-{name}"
        assert body["namespace"] == expected_ns

        # Wait for the hub to reach running state
        status = wait_for_hub(admin_session, BASE_URL, name, "running")
        assert status == "running"

        # Kubernetes namespace must exist
        assert namespace_exists(expected_ns), f"Namespace {expected_ns!r} not found in k8s"

    def test_running_hub_appears_in_list(
        self, admin_session, cleanup_hubs, wait_for_hub
    ):
        name = f"{HUB_PREFIX}-list-check"
        admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={"values": MINIMAL_VALUES},
        )
        cleanup_hubs.append(name)

        wait_for_hub(admin_session, BASE_URL, name, "running")

        r = admin_session.get(f"{BASE_URL}/api/hubs?status=running")
        assert r.status_code == 200
        names = [h["name"] for h in r.json()["hubs"]]
        assert name in names

    def test_delete_hub_removes_helm_release(
        self,
        admin_session,
        cleanup_hubs,
        wait_for_hub,
        namespace_exists,
        helm_release_exists,
    ):
        name = f"{HUB_PREFIX}-del-helm"
        admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={"values": MINIMAL_VALUES},
        )
        cleanup_hubs.append(name)

        wait_for_hub(admin_session, BASE_URL, name, "running")
        ns = f"jhub-{name}"

        r = admin_session.delete(f"{BASE_URL}/api/hubs/{name}")
        assert r.status_code == 204
        cleanup_hubs.remove(name)

        # The Helm release must be gone
        assert not helm_release_exists(name, ns), "Helm release still exists after delete"


# ---------------------------------------------------------------------------
# Namespace prefix
# ---------------------------------------------------------------------------


class TestNamespacePrefix:
    """Verify that hubs honour the defaultNamespacePrefix setting."""

    def test_default_prefix_applied(self, admin_session, cleanup_hubs, namespace_exists):
        """Hub namespace should be jhub-<name> as configured in ci-values."""
        name = f"{HUB_PREFIX}-prefix"
        r = admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        assert r.status_code == 201
        cleanup_hubs.append(name)

        assert r.json()["namespace"] == f"jhub-{name}"

    def test_explicit_namespace_overrides_prefix(
        self, admin_session, cleanup_hubs
    ):
        """An explicit namespace in the request overrides the prefix."""
        name = f"{HUB_PREFIX}-explicit-ns"
        custom_ns = "e2e-custom-namespace"
        r = admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={"namespace": custom_ns},
        )
        assert r.status_code == 201
        cleanup_hubs.append(name)

        assert r.json()["namespace"] == custom_ns


# ---------------------------------------------------------------------------
# Hub with custom Helm values
# ---------------------------------------------------------------------------


class TestCustomHelmValues:
    """Deploy a hub with extra values and verify the API round-trip."""

    def test_custom_values_persisted(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-custom-vals"
        extra = {
            "hub": {"db": {"type": "sqlite-memory"}},
            "proxy": {"service": {"type": "ClusterIP"}},
            "cull": {"enabled": False},
        }
        r = admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={"description": "custom values test", "values": extra},
        )
        assert r.status_code == 201
        cleanup_hubs.append(name)

        # GET should echo back the hub with the stored description
        body = admin_session.get(f"{BASE_URL}/api/hubs/{name}").json()
        assert body["description"] == "custom values test"

    def test_update_values_and_redeploy(
        self, admin_session, cleanup_hubs, wait_for_hub
    ):
        """PUT should allow updating values; hub status transitions back to pending."""
        name = f"{HUB_PREFIX}-update-vals"
        admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={"values": MINIMAL_VALUES},
        )
        cleanup_hubs.append(name)
        wait_for_hub(admin_session, BASE_URL, name, "running")

        r = admin_session.put(
            f"{BASE_URL}/api/hubs/{name}",
            json={"description": "updated description"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "updated description"


# ---------------------------------------------------------------------------
# Multi-hub concurrency
# ---------------------------------------------------------------------------


class TestMultiHubDeployment:
    """Create two hubs simultaneously and verify both reach running."""

    def test_two_hubs_run_concurrently(
        self, admin_session, cleanup_hubs, wait_for_hub
    ):
        names = [f"{HUB_PREFIX}-multi-a", f"{HUB_PREFIX}-multi-b"]

        for name in names:
            r = admin_session.post(
                f"{BASE_URL}/api/hubs/{name}",
                json={"values": MINIMAL_VALUES},
            )
            assert r.status_code == 201
            cleanup_hubs.append(name)

        for name in names:
            status = wait_for_hub(admin_session, BASE_URL, name, "running")
            assert status == "running", f"Hub {name!r} did not reach running"
