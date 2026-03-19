"""E2E tests: hub CRUD, audit events, and pagination.

These tests create, inspect, and delete hubs via the API.  They do NOT
start JupyterHub deployments (no Helm installs); that is covered in
test_deployments.py.  This keeps the suite fast enough to run on every PR.
"""

import pytest

from .conftest import BASE_URL

# Unique prefix so parallel runs do not collide
HUB_PREFIX = "e2e-crud"


@pytest.fixture(autouse=True)
def cleanup_hubs(admin_session):
    """Delete any hubs created during a test, even on failure."""
    created = []
    yield created
    for name in created:
        admin_session.delete(f"{BASE_URL}/api/hubs/{name}")


# ---------------------------------------------------------------------------
# Hub CRUD
# ---------------------------------------------------------------------------


class TestHubCRUD:
    def test_create_hub_returns_201(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-create"
        r = admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={"description": "e2e create test"},
        )
        assert r.status_code == 201, r.text
        cleanup_hubs.append(name)

        body = r.json()
        assert body["name"] == name
        assert body["status"] == "pending"
        assert body["owner"] == "admin"
        assert body["description"] == "e2e create test"
        # Namespace derived from prefix + name
        assert body["namespace"] == f"jhub-{name}"

    def test_create_duplicate_returns_409(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-dup"
        r1 = admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        assert r1.status_code == 201
        cleanup_hubs.append(name)

        r2 = admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        assert r2.status_code == 409

    def test_get_hub_returns_correct_data(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-get"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={"description": "get test"})
        cleanup_hubs.append(name)

        r = admin_session.get(f"{BASE_URL}/api/hubs/{name}")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == name
        assert body["description"] == "get test"

    def test_get_nonexistent_hub_returns_404(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/hubs/does-not-exist-xyz")
        assert r.status_code == 404

    def test_update_hub_description(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-update"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={"description": "original"})
        cleanup_hubs.append(name)

        r = admin_session.put(f"{BASE_URL}/api/hubs/{name}", json={"description": "updated"})
        assert r.status_code == 200
        assert r.json()["description"] == "updated"

    def test_delete_hub_returns_204(self, admin_session):
        name = f"{HUB_PREFIX}-delete"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})

        r = admin_session.delete(f"{BASE_URL}/api/hubs/{name}")
        assert r.status_code == 204

        # Confirm it's gone
        r2 = admin_session.get(f"{BASE_URL}/api/hubs/{name}")
        assert r2.status_code == 404

    def test_create_hub_with_explicit_namespace(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-ns"
        r = admin_session.post(
            f"{BASE_URL}/api/hubs/{name}",
            json={"namespace": "custom-ns-e2e"},
        )
        assert r.status_code == 201
        cleanup_hubs.append(name)
        assert r.json()["namespace"] == "custom-ns-e2e"

    def test_hub_appears_in_list(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-list"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        cleanup_hubs.append(name)

        r = admin_session.get(f"{BASE_URL}/api/hubs")
        names = [h["name"] for h in r.json()["hubs"]]
        assert name in names

    def test_non_owner_cannot_get_hub(self, admin_session, cleanup_hubs):
        """Alice creates a hub; it must not be visible to Bob via the API."""
        import requests

        # Create token for alice
        tr = admin_session.post(
            f"{BASE_URL}/api/users/alice/tokens", json={"name": "alice-hub-tok"}
        )
        alice_token = tr.json()["token"]
        alice_tid = tr.json()["id"]

        alice_s = requests.Session()
        alice_s.headers["Authorization"] = f"Bearer {alice_token}"
        alice_s.headers["Content-Type"] = "application/json"

        name = f"{HUB_PREFIX}-alice-private"
        alice_s.post(f"{BASE_URL}/api/hubs/{name}", json={})
        cleanup_hubs.append(name)

        # Bob should get 403 on alice's hub
        bob_tr = admin_session.post(
            f"{BASE_URL}/api/users/bob/tokens", json={"name": "bob-tok"}
        )
        bob_token = bob_tr.json()["token"]
        bob_tid = bob_tr.json()["id"]

        bob_s = requests.Session()
        bob_s.headers["Authorization"] = f"Bearer {bob_token}"
        r = bob_s.get(f"{BASE_URL}/api/hubs/{name}", timeout=10)
        assert r.status_code in (403, 404)

        # Cleanup tokens
        admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_tid}")
        admin_session.delete(f"{BASE_URL}/api/users/bob/tokens/{bob_tid}")


# ---------------------------------------------------------------------------
# Hub events / audit log
# ---------------------------------------------------------------------------


class TestHubEvents:
    def test_events_endpoint_exists(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-events"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        cleanup_hubs.append(name)

        r = admin_session.get(f"{BASE_URL}/api/hubs/{name}/events")
        assert r.status_code == 200
        body = r.json()
        assert "events" in body
        assert "_pagination" in body
        assert body["hub"] == name

    def test_events_pagination(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-evpag"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        cleanup_hubs.append(name)

        r = admin_session.get(f"{BASE_URL}/api/hubs/{name}/events?limit=1&offset=0")
        assert r.status_code == 200
        p = r.json()["_pagination"]
        assert p["limit"] == 1

    def test_events_since_filter(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-evsince"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        cleanup_hubs.append(name)

        r = admin_session.get(
            f"{BASE_URL}/api/hubs/{name}/events?since=2000-01-01T00:00:00"
        )
        assert r.status_code == 200

    def test_events_bad_since_returns_400(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-evbad"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        cleanup_hubs.append(name)

        r = admin_session.get(f"{BASE_URL}/api/hubs/{name}/events?since=not-a-date")
        assert r.status_code == 400

    def test_events_nonexistent_hub_returns_404(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/hubs/no-such-hub/events")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


class TestUserManagement:
    def test_create_and_delete_user(self, admin_session):
        name = "e2e-tmp-user"
        r = admin_session.post(f"{BASE_URL}/api/users/{name}", json={"admin": False})
        assert r.status_code in (201, 409)

        r2 = admin_session.get(f"{BASE_URL}/api/users/{name}")
        assert r2.status_code == 200
        assert r2.json()["name"] == name

        admin_session.delete(f"{BASE_URL}/api/users/{name}")
        assert admin_session.get(f"{BASE_URL}/api/users/{name}").status_code == 404

    def test_update_user_fields(self, admin_session):
        name = "e2e-update-user"
        admin_session.post(f"{BASE_URL}/api/users/{name}", json={})

        r = admin_session.put(
            f"{BASE_URL}/api/users/{name}",
            json={
                "max_hubs": 3,
                "allowed_namespaces": ["ns-a", "ns-b"],
                "can_create_namespaces": True,
                "can_delete_namespaces": False,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["max_hubs"] == 3
        assert body["allowed_namespaces"] == ["ns-a", "ns-b"]
        assert body["can_create_namespaces"] is True
        assert body["can_delete_namespaces"] is False

        admin_session.delete(f"{BASE_URL}/api/users/{name}")

    def test_delete_user_with_hubs_returns_400(self, admin_session, cleanup_hubs):
        name = "e2e-owned-user"
        admin_session.post(f"{BASE_URL}/api/users/{name}", json={})

        import requests

        tok_r = admin_session.post(
            f"{BASE_URL}/api/users/{name}/tokens", json={"name": "tok"}
        )
        tok = tok_r.json()["token"]
        tok_id = tok_r.json()["id"]

        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {tok}"
        s.headers["Content-Type"] = "application/json"

        hub_name = f"e2e-owned-hub"
        s.post(f"{BASE_URL}/api/hubs/{hub_name}", json={})
        cleanup_hubs.append(hub_name)

        # Deleting the user while they own a hub must fail
        r = admin_session.delete(f"{BASE_URL}/api/users/{name}")
        assert r.status_code == 400

        admin_session.delete(f"{BASE_URL}/api/users/{name}/tokens/{tok_id}")
        # Hub cleanup handled by the fixture
