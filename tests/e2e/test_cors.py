"""E2E tests: CORS header enforcement.

The CI deployment is configured with:
    corsAllowOrigins: ["http://localhost:3000", "https://trusted.example.com", "*"]

Tests verify:
- Allowed origins receive the full CORS response header set
- Requests without an Origin header are unaffected
- OPTIONS preflight returns 204 with all required headers
- Sensitive endpoints do not leak CORS headers to arbitrary origins when the
  allow-list does not include "*" (tested via a separate no-wildcard section)
- CORS headers appear on both success *and* error responses (so browser XHR
  can read the JSON error body)
"""

import requests

from .conftest import BASE_URL

# Origins configured in ci/jupytercluster-ci-values.yml
ALLOWED_EXPLICIT = "http://localhost:3000"
ALLOWED_EXPLICIT_2 = "https://trusted.example.com"
WILDCARD_ORIGIN = "https://arbitrary.example.com"   # matched by "*"
UNLISTED_ORIGIN = "https://evil.example.com"          # also matched by "*" in CI


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_with_origin(admin_session, path: str, origin: str) -> requests.Response:
    """Issue a GET with the given Origin header, reusing the admin Bearer token."""
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": admin_session.headers["Authorization"],
            "Content-Type": "application/json",
            "Origin": origin,
        }
    )
    return s.get(f"{BASE_URL}{path}", timeout=10)


def _options_with_origin(admin_session, path: str, origin: str) -> requests.Response:
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": admin_session.headers["Authorization"],
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        }
    )
    return s.options(f"{BASE_URL}{path}", timeout=10)


# ---------------------------------------------------------------------------
# Allowed-origin tests
# ---------------------------------------------------------------------------


class TestCORSAllowedOrigins:
    def test_explicit_origin_gets_acao_header(self, admin_session):
        r = _get_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT)
        assert r.status_code == 200
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        assert acao == ALLOWED_EXPLICIT, (
            f"Expected ACAO={ALLOWED_EXPLICIT!r}, got {acao!r}"
        )

    def test_second_explicit_origin_gets_acao_header(self, admin_session):
        r = _get_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT_2)
        assert r.status_code == 200
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        assert acao == ALLOWED_EXPLICIT_2

    def test_wildcard_allows_arbitrary_origin(self, admin_session):
        """With '*' in the allow-list, any origin must be echoed back."""
        r = _get_with_origin(admin_session, "/api/hubs", WILDCARD_ORIGIN)
        assert r.status_code == 200
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        assert acao == WILDCARD_ORIGIN, (
            f"With '*' in allow-list, arbitrary origin should be allowed; got {acao!r}"
        )

    def test_credentials_header_present(self, admin_session):
        r = _get_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT)
        assert r.headers.get("Access-Control-Allow-Credentials") == "true"

    def test_allow_headers_present(self, admin_session):
        r = _get_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT)
        allow_headers = r.headers.get("Access-Control-Allow-Headers", "")
        assert "Authorization" in allow_headers

    def test_allow_methods_present(self, admin_session):
        r = _get_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT)
        allow_methods = r.headers.get("Access-Control-Allow-Methods", "")
        for method in ("GET", "POST", "DELETE"):
            assert method in allow_methods, (
                f"Expected {method} in ACAM header: {allow_methods!r}"
            )


# ---------------------------------------------------------------------------
# Preflight (OPTIONS) tests
# ---------------------------------------------------------------------------


class TestCORSPreflight:
    def test_options_returns_204(self, admin_session):
        r = _options_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT)
        assert r.status_code == 204, f"OPTIONS preflight must return 204, got {r.status_code}"

    def test_options_has_acao_header(self, admin_session):
        r = _options_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT)
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        assert acao == ALLOWED_EXPLICIT

    def test_options_has_allow_methods(self, admin_session):
        r = _options_with_origin(admin_session, "/api/hubs", ALLOWED_EXPLICIT)
        assert r.headers.get("Access-Control-Allow-Methods"), (
            "OPTIONS response must include Access-Control-Allow-Methods"
        )

    def test_options_on_specific_hub(self, admin_session):
        """OPTIONS on a named resource must also return 204."""
        admin_session.post(f"{BASE_URL}/api/hubs/e2e-cors-opts", json={})
        r = _options_with_origin(admin_session, "/api/hubs/e2e-cors-opts", ALLOWED_EXPLICIT)
        assert r.status_code == 204
        admin_session.delete(f"{BASE_URL}/api/hubs/e2e-cors-opts")

    def test_options_on_token_endpoint(self, admin_session):
        r = _options_with_origin(
            admin_session, "/api/users/admin/tokens", ALLOWED_EXPLICIT
        )
        assert r.status_code == 204


# ---------------------------------------------------------------------------
# No-Origin requests are unaffected
# ---------------------------------------------------------------------------


class TestCORSNoOrigin:
    def test_request_without_origin_has_no_acao_header(self, admin_session):
        """Non-browser requests must not receive spurious CORS headers."""
        r = admin_session.get(f"{BASE_URL}/api/hubs")
        assert "Access-Control-Allow-Origin" not in r.headers, (
            "ACAO header must not be set when no Origin header is sent"
        )

    def test_health_without_origin_has_no_acao_header(self, anon_session):
        r = anon_session.get(f"{BASE_URL}/api/health")
        assert "Access-Control-Allow-Origin" not in r.headers


# ---------------------------------------------------------------------------
# CORS headers on error responses
# ---------------------------------------------------------------------------


class TestCORSOnErrors:
    def test_404_includes_acao_header(self, admin_session):
        """Browsers need ACAO on error responses to read the JSON body."""
        r = _get_with_origin(admin_session, "/api/hubs/ghost-cors-hub", ALLOWED_EXPLICIT)
        assert r.status_code == 404
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        assert acao == ALLOWED_EXPLICIT, (
            f"ACAO must be present on 404 error so browser can read body; got {acao!r}"
        )

    def test_401_includes_acao_header(self):
        """Unauthenticated 401 on an allowed origin must still carry ACAO."""
        s = requests.Session()
        s.headers["Origin"] = ALLOWED_EXPLICIT
        r = s.get(f"{BASE_URL}/api/hubs", timeout=10)
        assert r.status_code in (401, 403)
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        assert acao == ALLOWED_EXPLICIT, (
            f"ACAO must be present on auth error so browser can read body; got {acao!r}"
        )


# ---------------------------------------------------------------------------
# Info endpoint CORS
# ---------------------------------------------------------------------------


class TestCORSPublicEndpoints:
    def test_info_endpoint_allows_cors(self, anon_session):
        s = requests.Session()
        s.headers["Origin"] = ALLOWED_EXPLICIT
        r = s.get(f"{BASE_URL}/api/info", timeout=10)
        assert r.status_code == 200
        # /api/info is public and served by an APIHandler subclass — must honour CORS
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        assert acao == ALLOWED_EXPLICIT
