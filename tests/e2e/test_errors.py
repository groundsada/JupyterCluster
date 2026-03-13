"""E2E tests: HTTP error response consistency.

Every error from the API must use the JSON envelope:

    {"error": {"status_code": <int>, "message": "<str>"}}

and must carry Content-Type: application/json.  Tests here probe the full
range of standard error conditions to prevent any handler from accidentally
returning plain-text or HTML errors.
"""

import pytest
import requests

from .conftest import BASE_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_json_error(r: requests.Response, expected_status: int):
    """Assert response uses the standard error envelope."""
    assert r.status_code == expected_status, (
        f"Expected HTTP {expected_status}, got {r.status_code}: {r.text}"
    )
    assert "application/json" in r.headers.get("Content-Type", ""), (
        f"Error response must be JSON, got Content-Type: {r.headers.get('Content-Type')}"
    )
    body = r.json()
    assert "error" in body, f"Missing 'error' key in response: {body}"
    err = body["error"]
    assert "status_code" in err, f"Missing error.status_code: {body}"
    assert "message" in err, f"Missing error.message: {body}"
    assert err["status_code"] == expected_status, (
        f"error.status_code {err['status_code']} != HTTP {expected_status}"
    )
    assert isinstance(err["message"], str) and err["message"], (
        f"error.message must be a non-empty string: {body}"
    )


# ---------------------------------------------------------------------------
# 404 — not found
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_missing_hub_returns_json_404(self, admin_session):
        _assert_json_error(
            admin_session.get(f"{BASE_URL}/api/hubs/this-hub-does-not-exist-xyz"),
            404,
        )

    def test_missing_user_returns_json_404(self, admin_session):
        _assert_json_error(
            admin_session.get(f"{BASE_URL}/api/users/no-such-user-xyz"),
            404,
        )

    def test_missing_token_returns_json_404(self, admin_session):
        _assert_json_error(
            admin_session.get(f"{BASE_URL}/api/users/admin/tokens/999999"),
            404,
        )

    def test_missing_hub_events_returns_json_404(self, admin_session):
        _assert_json_error(
            admin_session.get(f"{BASE_URL}/api/hubs/ghost-hub/events"),
            404,
        )


# ---------------------------------------------------------------------------
# 401 / 403 — auth / authorisation failures
# ---------------------------------------------------------------------------


class TestUnauthorised:
    def test_no_auth_returns_json_401_or_403(self, anon_session):
        r = anon_session.get(f"{BASE_URL}/api/hubs")
        assert r.status_code in (401, 403)
        assert "application/json" in r.headers.get("Content-Type", "")
        body = r.json()
        assert "error" in body

    def test_bad_bearer_returns_json_401_or_403(self):
        s = requests.Session()
        s.headers["Authorization"] = "Bearer totally-bogus-token"
        r = s.get(f"{BASE_URL}/api/hubs", timeout=10)
        assert r.status_code in (401, 403)
        assert "application/json" in r.headers.get("Content-Type", "")
        body = r.json()
        assert "error" in body

    def test_wrong_user_hub_access_returns_json_403(self, admin_session):
        """Bob tries to GET a hub owned by alice — must get JSON 403/404."""
        # Create alice token and hub
        alice_tok_r = admin_session.post(
            f"{BASE_URL}/api/users/alice/tokens", json={"name": "err-alice"}
        )
        alice_tok = alice_tok_r.json()["token"]
        alice_tid = alice_tok_r.json()["id"]

        alice_s = requests.Session()
        alice_s.headers["Authorization"] = f"Bearer {alice_tok}"
        alice_s.headers["Content-Type"] = "application/json"

        hub_name = "e2e-err-alice-hub"
        alice_s.post(f"{BASE_URL}/api/hubs/{hub_name}", json={})

        bob_tok_r = admin_session.post(
            f"{BASE_URL}/api/users/bob/tokens", json={"name": "err-bob"}
        )
        bob_tok = bob_tok_r.json()["token"]
        bob_tid = bob_tok_r.json()["id"]

        bob_s = requests.Session()
        bob_s.headers["Authorization"] = f"Bearer {bob_tok}"

        r = bob_s.get(f"{BASE_URL}/api/hubs/{hub_name}", timeout=10)
        assert r.status_code in (403, 404)
        assert "application/json" in r.headers.get("Content-Type", "")
        assert "error" in r.json()

        # Cleanup
        admin_session.delete(f"{BASE_URL}/api/hubs/{hub_name}")
        admin_session.delete(f"{BASE_URL}/api/users/alice/tokens/{alice_tid}")
        admin_session.delete(f"{BASE_URL}/api/users/bob/tokens/{bob_tid}")


# ---------------------------------------------------------------------------
# 400 — bad request / validation
# ---------------------------------------------------------------------------


class TestBadRequest:
    def test_malformed_json_body_returns_json_error(self, admin_session):
        """Sending broken JSON to a POST endpoint must not produce a 500."""
        import requests as req

        s = req.Session()
        s.headers.update(
            {
                "Authorization": admin_session.headers["Authorization"],
                "Content-Type": "application/json",
            }
        )
        r = s.post(
            f"{BASE_URL}/api/users/admin/tokens",
            data=b"{not valid json",  # raw bytes, not json=
            timeout=10,
        )
        # Handler should reject gracefully — 400 or 422
        assert r.status_code in (400, 422), (
            f"Expected 400/422 for malformed JSON, got {r.status_code}: {r.text}"
        )
        assert "application/json" in r.headers.get("Content-Type", "")

    def test_events_bad_since_param_returns_json_400(self, admin_session):
        # Create a hub to target
        admin_session.post(f"{BASE_URL}/api/hubs/e2e-err-since", json={})
        r = admin_session.get(
            f"{BASE_URL}/api/hubs/e2e-err-since/events?since=not-a-date"
        )
        _assert_json_error(r, 400)
        admin_session.delete(f"{BASE_URL}/api/hubs/e2e-err-since")

    def test_token_with_invalid_scopes_type_returns_json_400(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/users/admin/tokens",
            json={"name": "bad-scopes", "scopes": "should-be-list"},
        )
        _assert_json_error(r, 400)

    def test_duplicate_hub_returns_json_409(self, admin_session):
        name = "e2e-err-dup"
        admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        r = admin_session.post(f"{BASE_URL}/api/hubs/{name}", json={})
        _assert_json_error(r, 409)
        admin_session.delete(f"{BASE_URL}/api/hubs/{name}")


# ---------------------------------------------------------------------------
# 405 — method not allowed
# ---------------------------------------------------------------------------


class TestMethodNotAllowed:
    def test_patch_on_hub_list_returns_405_or_404(self, admin_session):
        """PATCH /api/hubs is not defined — expect 404 or 405, not 500."""
        r = admin_session.patch(f"{BASE_URL}/api/hubs", timeout=10)
        assert r.status_code in (404, 405), (
            f"Expected 404/405, got {r.status_code}: {r.text}"
        )
        assert "application/json" in r.headers.get("Content-Type", "")

    def test_post_on_hub_list_endpoint_handled(self, admin_session):
        """POST /api/hubs without a name is not a valid route."""
        # Tornado will return 404 for unmatched routes
        r = admin_session.post(f"{BASE_URL}/api/hubs", json={})
        assert r.status_code in (404, 405, 400)


# ---------------------------------------------------------------------------
# Error envelope consistency across all endpoints
# ---------------------------------------------------------------------------


class TestErrorEnvelopeConsistency:
    """Probe every major endpoint category for consistent error shape."""

    ENDPOINTS_404 = [
        "/api/hubs/nope-xyz",
        "/api/users/nope-xyz",
        "/api/hubs/nope-xyz/events",
    ]

    @pytest.mark.parametrize("path", ENDPOINTS_404)
    def test_404_envelope_shape(self, admin_session, path):
        r = admin_session.get(f"{BASE_URL}{path}")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["status_code"] == 404
        assert isinstance(body["error"]["message"], str)

    def test_health_still_200_after_error(self, anon_session):
        """A 404 must not crash the server — health must still pass after."""
        anon_session.get(f"{BASE_URL}/api/hubs/ghost")
        r = anon_session.get(f"{BASE_URL}/api/health")
        assert r.status_code == 200
