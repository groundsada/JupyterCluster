"""Base API handler for JupyterCluster.

Follows JupyterHub's API handler conventions:
- Bearer token auth checked before cookie (programmatic clients first)
- Invalid/expired Bearer token does NOT fall back to cookie (prevents confusion)
- XSRF exempted for all /api/ routes — non-browser clients can't set cookies
  cross-origin anyway, and Bearer tokens are not XSRF-vulnerable
- CORS headers configurable via app.cors_allow_origins
- Consistent JSON error envelope matching JupyterHub's format
"""

import json
import logging
from datetime import datetime
from typing import Optional

from tornado import web

logger = logging.getLogger(__name__)


class APIHandler(web.RequestHandler):
    """Base class for all JupyterCluster API handlers."""

    # ------------------------------------------------------------------
    # Convenience property — eliminates boilerplate in every handler
    # ------------------------------------------------------------------

    @property
    def app(self):
        """The JupyterCluster application instance."""
        a = self.application.settings.get("jupytercluster")
        if a is None:
            raise web.HTTPError(500, "JupyterCluster application not initialised")
        return a

    # ------------------------------------------------------------------
    # Headers — Content-Type + optional CORS
    # ------------------------------------------------------------------

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self._set_cors_headers()

    def _set_cors_headers(self):
        """Emit CORS headers if the request origin is in the allow-list.

        Mirrors JupyterHub: origins are opt-in via ``cors_allow_origins``.
        An allow-list of ``["*"]`` permits all origins (not recommended in
        production).
        """
        origin = self.request.headers.get("Origin", "")
        if not origin:
            return
        allowed = (
            getattr(self.app, "cors_allow_origins", [])
            if self.application.settings.get("jupytercluster")
            else []
        )
        if not allowed:
            return
        if "*" in allowed or origin in allowed:
            self.set_header("Access-Control-Allow-Origin", origin)
            self.set_header("Access-Control-Allow-Credentials", "true")
            self.set_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-Requested-With",
            )
            self.set_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, DELETE, PATCH, OPTIONS",
            )

    def options(self, *args, **kwargs):
        """Handle CORS preflight requests (OPTIONS)."""
        self.set_status(204)
        self.finish()

    # ------------------------------------------------------------------
    # XSRF — exempt all API handlers (mirrors JupyterHub)
    # ------------------------------------------------------------------

    def check_xsrf_cookie(self):
        """API handlers are exempt from XSRF checking.

        Rationale (same as JupyterHub):
        - Bearer token requests are not XSRF-vulnerable by construction.
        - Cookie-authenticated API calls come from the same origin (enforced
          by the browser's same-origin policy for cookies), so XSRF is not
          an additional risk here either.
        - Exempting /api/ routes allows curl, SDKs, and CI pipelines to POST
          without needing to fetch and forward the XSRF token.
        """
        pass

    # ------------------------------------------------------------------
    # Authentication — Bearer token then cookie
    # ------------------------------------------------------------------

    def _find_token(self, raw: str):
        """Look up a valid, non-expired APIToken by its raw value.

        Returns the ORM object or None.  The lookup uses the SHA-256 digest
        so the raw value is never compared directly (mirrors JupyterHub).
        """
        from .. import orm

        hashed = orm.APIToken.hash(raw)
        try:
            token = self.app.db.query(orm.APIToken).filter_by(hashed_token=hashed).first()
        except Exception:
            return None
        if token is None:
            return None
        if token.is_expired():
            return None
        return token

    def get_current_user(self) -> Optional[str]:
        """Resolve the current user from Bearer token or session cookie.

        Resolution order (mirrors JupyterHub):
        1. ``Authorization: Bearer <token>`` header — for API clients.
           If the header is present but the token is invalid/expired,
           authentication fails immediately (no cookie fallback).
        2. ``jupytercluster_user`` secure cookie — for browser sessions.

        Stores the resolved token ORM object as ``self._api_token`` so that
        ``check_token_scopes()`` can inspect it without a second DB query.
        """
        self._api_token = None

        auth_header = self.request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            raw = auth_header[7:].strip()
            token = self._find_token(raw)
            if token is None:
                # Header present but invalid — do NOT fall back to cookie
                return None
            self._api_token = token
            # Lazily update last_activity; don't block the response on this
            token.last_activity = datetime.utcnow()
            try:
                self.app.db.commit()
            except Exception:
                self.app.db.rollback()
            return token.user.name

        # Cookie fallback for browser sessions
        raw_cookie = self.get_secure_cookie("jupytercluster_user")
        if raw_cookie:
            return raw_cookie.decode("utf-8")
        return None

    # ------------------------------------------------------------------
    # Authorisation helpers
    # ------------------------------------------------------------------

    def is_admin(self) -> bool:
        """Return True if the current user has admin privileges."""
        user = self.get_current_user()
        if not user:
            return False
        try:
            return self.app.authenticator.is_admin(user)
        except Exception:
            return False

    def check_hub_permission(self, hub_owner: str) -> bool:
        """Return True if the current user may manage *hub_owner*'s hub."""
        user = self.get_current_user()
        if not user:
            return False
        if self.is_admin():
            return True
        return user == hub_owner

    def require_hub_permission(self, hub_owner: str):
        """Raise HTTP 403 if the current user cannot manage this hub."""
        if not self.check_hub_permission(hub_owner):
            raise web.HTTPError(403, "Permission denied")

    def check_token_scopes(self, required_scope: str) -> bool:
        """Return True if the current request is authorised for *required_scope*.

        Scope resolution (mirrors JupyterHub):
        - Cookie-authenticated requests are not scope-constrained.
        - Tokens with an empty scopes list inherit all user permissions.
        - Tokens with an explicit scopes list must include *required_scope*.
        """
        if self._api_token is None:
            # Cookie auth — not scope-restricted
            return True
        scopes = self._api_token.scopes or []
        if not scopes:
            # Empty scope list → full user permissions
            return True
        return required_scope in scopes

    def require_scope(self, scope: str):
        """Raise HTTP 403 if the token does not have *scope*."""
        if not self.check_token_scopes(scope):
            raise web.HTTPError(403, f"Token does not have required scope: {scope!r}")

    # ------------------------------------------------------------------
    # Request / response helpers
    # ------------------------------------------------------------------

    def get_json_body(self) -> Optional[dict]:
        """Decode and return the JSON request body, or None on failure.

        Raises HTTP 400 when the client sends Content-Type: application/json
        with a non-empty, unparseable body.  Empty bodies return None (treated
        as an absent body by callers).
        """
        body = self.request.body
        if not body:
            return None
        content_type = self.request.headers.get("Content-Type", "")
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            if "application/json" in content_type:
                raise web.HTTPError(400, f"Invalid JSON body: {exc}") from exc
            return None

    def write_error(self, status_code: int, **kwargs):
        """Emit a consistent JSON error envelope.

        Format mirrors JupyterHub::

            {"error": {"status_code": 404, "message": "Hub foo not found"}}
        """
        self.set_header("Content-Type", "application/json")
        if "exc_info" in kwargs:
            message = str(kwargs["exc_info"][1])
        else:
            message = self._reason
        self.write(json.dumps({"error": {"status_code": status_code, "message": message}}))
