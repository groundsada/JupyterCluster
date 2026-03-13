"""E2E tests: core API endpoints.

Covers:
- Health check (liveness / readiness probe parity)
- /api/info capability discovery
- Bearer token authentication (valid / invalid / expired)
- Token lifecycle: create → use → revoke → reject
- Pagination on /api/hubs and /api/users
- XSRF exemption (POST without X-XSRFToken header must succeed)
- CORS headers when Origin is sent
"""

import pytest

from .conftest import BASE_URL


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_get_returns_ok(self, anon_session):
        r = anon_session.get(f"{BASE_URL}/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_head_returns_200(self, anon_session):
        """Kubernetes liveness probes often use HEAD."""
        r = anon_session.request("HEAD", f"{BASE_URL}/api/health")
        assert r.status_code == 200

    def test_content_type_is_json(self, anon_session):
        r = anon_session.get(f"{BASE_URL}/api/health")
        assert "application/json" in r.headers.get("Content-Type", "")


# ---------------------------------------------------------------------------
# /api/info
# ---------------------------------------------------------------------------


class TestInfo:
    def test_unauthenticated_allowed(self, anon_session):
        """Info is publicly readable — no credentials required."""
        r = anon_session.get(f"{BASE_URL}/api/info")
        assert r.status_code == 200

    def test_returns_version(self, anon_session):
        data = anon_session.get(f"{BASE_URL}/api/info").json()
        assert "version" in data
        assert data["version"]  # non-empty string

    def test_features_dict_present(self, anon_session):
        data = anon_session.get(f"{BASE_URL}/api/info").json()
        features = data.get("features", {})
        assert features.get("token_auth") is True
        assert features.get("pagination") is True
        assert features.get("hub_events") is True

    def test_auth_type_present(self, anon_session):
        data = anon_session.get(f"{BASE_URL}/api/info").json()
        assert data.get("auth_type") in ("password", "oauth")


# ---------------------------------------------------------------------------
# Token authentication
# ---------------------------------------------------------------------------


class TestTokenAuth:
    def test_valid_token_authenticates(self, admin_session):
        """Admin Bearer token can list hubs."""
        r = admin_session.get(f"{BASE_URL}/api/hubs")
        assert r.status_code == 200

    def test_invalid_token_returns_401_not_cookie_fallback(self, anon_session):
        """An invalid Bearer token must fail — no silent cookie fallback."""
        import requests

        s = requests.Session()
        s.headers["Authorization"] = "Bearer totallyinvalidtoken"
        r = s.get(f"{BASE_URL}/api/hubs", timeout=10)
        # Without the valid token the request must be rejected
        assert r.status_code in (401, 403)

    def test_no_auth_returns_401_or_403(self, anon_session):
        """Endpoints that require auth must reject unauthenticated requests."""
        r = anon_session.get(f"{BASE_URL}/api/users")
        assert r.status_code in (401, 403)


class TestTokenLifecycle:
    """Create → verify → use → revoke → reject."""

    def test_full_lifecycle(self, admin_session):
        import requests

        # 1. Create token for admin
        r = admin_session.post(
            f"{BASE_URL}/api/users/admin/tokens",
            json={"name": "lifecycle-test", "note": "e2e lifecycle test"},
        )
        assert r.status_code == 201, r.text
        payload = r.json()
        assert "token" in payload, "raw token must appear in POST 201 response"
        raw_token = payload["token"]
        token_id = payload["id"]
        assert payload["name"] == "lifecycle-test"
        assert payload["note"] == "e2e lifecycle test"

        # 2. Use the new token to hit the API
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {raw_token}"
        r2 = s.get(f"{BASE_URL}/api/hubs", timeout=10)
        assert r2.status_code == 200, "Newly-created token must authenticate"

        # 3. GET token metadata — raw value must NOT appear
        r3 = admin_session.get(f"{BASE_URL}/api/users/admin/tokens/{token_id}")
        assert r3.status_code == 200
        meta = r3.json()
        assert "token" not in meta, "Raw token must not be retrievable after creation"
        assert meta["id"] == token_id

        # 4. List tokens — our token appears
        r4 = admin_session.get(f"{BASE_URL}/api/users/admin/tokens")
        assert r4.status_code == 200
        ids = [t["id"] for t in r4.json()["tokens"]]
        assert token_id in ids

        # 5. Revoke
        r5 = admin_session.delete(f"{BASE_URL}/api/users/admin/tokens/{token_id}")
        assert r5.status_code == 204

        # 6. Revoked token must be rejected
        r6 = s.get(f"{BASE_URL}/api/hubs", timeout=10)
        assert r6.status_code in (401, 403), "Revoked token must be rejected"

    def test_token_expiry(self, admin_session):
        """A token with expires_in=1 should be rejected after a brief wait."""
        import time
        import requests

        r = admin_session.post(
            f"{BASE_URL}/api/users/admin/tokens",
            json={"name": "expiry-test", "expires_in": 1},
        )
        assert r.status_code == 201
        raw = r.json()["token"]
        token_id = r.json()["id"]

        # Token should work immediately
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {raw}"
        assert s.get(f"{BASE_URL}/api/hubs", timeout=10).status_code == 200

        # Wait for expiry
        time.sleep(3)
        r2 = s.get(f"{BASE_URL}/api/hubs", timeout=10)
        assert r2.status_code in (401, 403), "Expired token must be rejected"

        # Cleanup
        admin_session.delete(f"{BASE_URL}/api/users/admin/tokens/{token_id}")

    def test_create_token_invalid_scopes(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/users/admin/tokens",
            json={"name": "bad-scopes", "scopes": "not-a-list"},
        )
        assert r.status_code == 400

    def test_non_admin_cannot_access_other_users_tokens(self, admin_session):
        """Alice cannot list admin's tokens."""
        import requests

        # Create a token for alice
        r = admin_session.post(
            f"{BASE_URL}/api/users/alice/tokens",
            json={"name": "alice-tok"},
        )
        assert r.status_code == 201
        alice_raw = r.json()["token"]
        alice_token_id = r.json()["id"]

        try:
            s = requests.Session()
            s.headers["Authorization"] = f"Bearer {alice_raw}"
            # Alice cannot see admin's tokens
            r2 = s.get(f"{BASE_URL}/api/users/admin/tokens", timeout=10)
            assert r2.status_code in (401, 403)
        finally:
            admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_token_id}")


# ---------------------------------------------------------------------------
# XSRF exemption
# ---------------------------------------------------------------------------


class TestXSRFExemption:
    def test_post_api_without_xsrf_token_succeeds(self, admin_session):
        """API handlers must not require the XSRF cookie/header."""
        import requests

        s = requests.Session()
        s.headers.update(
            {
                "Authorization": admin_session.headers["Authorization"],
                "Content-Type": "application/json",
                # Deliberately omit X-XSRFToken
            }
        )
        r = s.post(
            f"{BASE_URL}/api/users/xsrf-test-user",
            json={"admin": False},
            timeout=10,
        )
        # 201 Created or 409 Conflict both mean the handler processed it without XSRF
        assert r.status_code in (201, 409), f"Expected 201/409, got {r.status_code}: {r.text}"

        # Cleanup
        admin_session.delete(f"{BASE_URL}/api/users/xsrf-test-user")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def test_hub_list_pagination_envelope(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/hubs?limit=5&offset=0")
        assert r.status_code == 200
        body = r.json()
        assert "hubs" in body
        assert "_pagination" in body
        p = body["_pagination"]
        assert p["limit"] == 5
        assert p["offset"] == 0
        assert "total" in p
        assert "next_offset" in p

    def test_hub_list_status_filter(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/hubs?status=running")
        assert r.status_code == 200
        # All returned hubs must have status=running
        for hub in r.json()["hubs"]:
            assert hub["status"] == "running"

    def test_user_list_pagination_envelope(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/users?limit=2&offset=0")
        assert r.status_code == 200
        body = r.json()
        assert "_pagination" in body
        assert len(body["users"]) <= 2

    def test_invalid_limit_uses_default(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/hubs?limit=notanumber")
        assert r.status_code == 200  # should not crash
