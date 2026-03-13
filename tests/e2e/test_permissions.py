"""E2E tests: namespace permission enforcement.

Covers:
- A user with can_create_namespaces=false cannot create hubs (403)
- A user with allowed_namespaces restrictions is denied out-of-allowlist namespaces
- can_create_namespaces=false still allows the admin to create hubs
- Global allowUserNamespaceManagement can be overridden per-user
- can_delete_namespaces=false prevents namespace deletion on hub delete

The CI values seed three users:
  admin  — unrestricted admin
  alice  — regular user, max_hubs=5, no namespace restrictions
  bob    — can_create_namespaces=false, can_delete_namespaces=false
"""

import pytest
import requests

from .conftest import BASE_URL

HUB_PREFIX = "e2e-perm"


@pytest.fixture(autouse=True)
def cleanup_hubs(admin_session):
    created = []
    yield created
    for name in created:
        admin_session.delete(f"{BASE_URL}/api/hubs/{name}")


def _user_session(admin_session, username: str) -> tuple[requests.Session, str]:
    """Create a Bearer-token session for *username*; return (session, token_id)."""
    r = admin_session.post(
        f"{BASE_URL}/api/users/{username}/tokens",
        json={"name": f"e2e-perm-{username}"},
    )
    assert r.status_code == 201, f"Could not create token for {username}: {r.text}"
    token = r.json()["token"]
    tid = r.json()["id"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    s.headers["Content-Type"] = "application/json"
    s.timeout = 30
    return s, tid


# ---------------------------------------------------------------------------
# Bob — can_create_namespaces=false
# ---------------------------------------------------------------------------


class TestBobCannotCreateHubs:
    """bob has can_create_namespaces=false in the CI seed config."""

    def test_bob_create_hub_returns_403(self, admin_session, cleanup_hubs):
        bob_s, bob_tid = _user_session(admin_session, "bob")
        try:
            name = f"{HUB_PREFIX}-bob-create"
            r = bob_s.post(f"{BASE_URL}/api/hubs/{name}", json={})
            assert r.status_code == 403, (
                f"bob should be denied hub creation, got {r.status_code}: {r.text}"
            )
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/bob/tokens/{bob_tid}")

    def test_bob_cannot_delete_namespace(self, admin_session, cleanup_hubs):
        """Even if admin creates a hub for bob, bob cannot trigger namespace deletion."""
        bob_s, bob_tid = _user_session(admin_session, "bob")
        try:
            # Admin creates the hub — bypass bob's restriction
            name = f"{HUB_PREFIX}-bob-del"
            r = admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
            assert r.status_code == 201
            cleanup_hubs.append(name)

            # Bob cannot delete it (403 or 404 — either means denied)
            r2 = bob_s.delete(f"{BASE_URL}/api/hubs/{name}")
            assert r2.status_code in (403, 404), (
                f"bob should not be able to delete the hub, got {r2.status_code}"
            )
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/bob/tokens/{bob_tid}")


# ---------------------------------------------------------------------------
# Alice — allowed_namespaces restriction
# ---------------------------------------------------------------------------


class TestAliceNamespaceRestrictions:
    """Set allowed_namespaces on alice and verify enforcement."""

    def test_alice_blocked_from_unlisted_namespace(self, admin_session, cleanup_hubs):
        # Restrict alice to only "allowed-ns-a"
        admin_session.put(
            f"{BASE_URL}/api/users/alice",
            json={"allowed_namespaces": ["allowed-ns-a"]},
        )

        alice_s, alice_tid = _user_session(admin_session, "alice")
        try:
            name = f"{HUB_PREFIX}-alice-bad-ns"
            r = alice_s.post(
                f"{BASE_URL}/api/hubs/{name}",
                json={"namespace": "not-in-allowlist"},
            )
            assert r.status_code == 403, (
                f"alice should be denied namespace outside allowlist, got {r.status_code}"
            )
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_tid}")
            # Reset alice's allowed_namespaces
            admin_session.put(f"{BASE_URL}/api/users/alice", json={"allowed_namespaces": []})

    def test_alice_allowed_within_allowlist(self, admin_session, cleanup_hubs):
        # Allow alice to use "allowed-ns-a"
        admin_session.put(
            f"{BASE_URL}/api/users/alice",
            json={"allowed_namespaces": ["allowed-ns-a"]},
        )

        alice_s, alice_tid = _user_session(admin_session, "alice")
        try:
            name = f"{HUB_PREFIX}-alice-good-ns"
            r = alice_s.post(
                f"{BASE_URL}/api/hubs/{name}",
                json={"namespace": "allowed-ns-a"},
            )
            assert r.status_code == 201, (
                f"alice should be allowed namespace in allowlist, got {r.status_code}: {r.text}"
            )
            cleanup_hubs.append(name)
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_tid}")
            admin_session.put(f"{BASE_URL}/api/users/alice", json={"allowed_namespaces": []})

    def test_empty_allowlist_means_no_restriction(self, admin_session, cleanup_hubs):
        """An empty allowed_namespaces list means unrestricted — all namespaces ok."""
        admin_session.put(
            f"{BASE_URL}/api/users/alice",
            json={"allowed_namespaces": []},
        )

        alice_s, alice_tid = _user_session(admin_session, "alice")
        try:
            name = f"{HUB_PREFIX}-alice-any-ns"
            r = alice_s.post(
                f"{BASE_URL}/api/hubs/{name}",
                json={"namespace": "any-arbitrary-ns"},
            )
            assert r.status_code == 201, (
                f"alice with empty allowlist should be unrestricted, got {r.status_code}: {r.text}"
            )
            cleanup_hubs.append(name)
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_tid}")


# ---------------------------------------------------------------------------
# Admin is never restricted
# ---------------------------------------------------------------------------


class TestAdminBypassesPermissions:
    def test_admin_can_always_create_hubs(self, admin_session, cleanup_hubs):
        name = f"{HUB_PREFIX}-admin-create"
        r = admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        assert r.status_code == 201
        cleanup_hubs.append(name)

    def test_admin_can_delete_any_hub(self, admin_session, cleanup_hubs):
        alice_s, alice_tid = _user_session(admin_session, "alice")
        try:
            name = f"{HUB_PREFIX}-admin-del-alice"
            alice_s.post(f"{BASE_URL}/api/hubs/{name}", json={})
            cleanup_hubs.append(name)

            r = admin_session.delete(f"{BASE_URL}/api/hubs/{name}")
            assert r.status_code == 204
            cleanup_hubs.remove(name)
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_tid}")


# ---------------------------------------------------------------------------
# Max-hubs quota
# ---------------------------------------------------------------------------


class TestMaxHubsQuota:
    def test_alice_max_hubs_enforced(self, admin_session, cleanup_hubs):
        """Set alice's max_hubs=2 and verify the third create is rejected."""
        admin_session.put(f"{BASE_URL}/api/users/alice", json={"max_hubs": 2})

        alice_s, alice_tid = _user_session(admin_session, "alice")
        created = []
        try:
            for i in range(2):
                name = f"{HUB_PREFIX}-alice-quota-{i}"
                r = alice_s.post(f"{BASE_URL}/api/hubs/{name}", json={})
                assert r.status_code == 201, f"Hub {i} should succeed: {r.text}"
                created.append(name)
                cleanup_hubs.append(name)

            name_over = f"{HUB_PREFIX}-alice-quota-over"
            r_over = alice_s.post(f"{BASE_URL}/api/hubs/{name_over}", json={})
            assert r_over.status_code in (403, 429), (
                f"Third hub should be rejected by quota, got {r_over.status_code}: {r_over.text}"
            )
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_tid}")
            # Reset quota
            admin_session.put(f"{BASE_URL}/api/users/alice", json={"max_hubs": 5})


# ---------------------------------------------------------------------------
# Per-user namespace management override
# ---------------------------------------------------------------------------


class TestPerUserNamespaceOverride:
    def test_grant_create_to_bob(self, admin_session, cleanup_hubs):
        """Explicitly grant can_create_namespaces=true to bob; he can now create."""
        admin_session.put(
            f"{BASE_URL}/api/users/bob",
            json={"can_create_namespaces": True},
        )

        bob_s, bob_tid = _user_session(admin_session, "bob")
        try:
            name = f"{HUB_PREFIX}-bob-granted"
            r = bob_s.post(f"{BASE_URL}/api/hubs/{name}", json={})
            assert r.status_code == 201, (
                f"bob with can_create_namespaces=true should succeed, got {r.status_code}: {r.text}"
            )
            cleanup_hubs.append(name)
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/bob/tokens/{bob_tid}")
            # Restore bob's restriction
            admin_session.put(
                f"{BASE_URL}/api/users/bob",
                json={"can_create_namespaces": False, "can_delete_namespaces": False},
            )
