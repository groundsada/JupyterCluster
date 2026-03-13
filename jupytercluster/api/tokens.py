"""API token management endpoints.

Mirrors JupyterHub's /api/users/:name/tokens endpoints:
- GET  /api/users/:name/tokens            list tokens (metadata only)
- POST /api/users/:name/tokens            create token (raw value returned once)
- GET  /api/users/:name/tokens/:token_id  get token metadata
- DELETE /api/users/:name/tokens/:token_id revoke token

Security model (same as JupyterHub):
- Raw token returned only in the POST 201 response; never again after that.
- Only the SHA-256 hash is stored; the raw value cannot be recovered.
- A user can manage their own tokens; admins can manage any user's tokens.
"""

import logging
from datetime import datetime, timedelta

from tornado import web

from .. import orm
from ..pagination import paginate_query, pagination_envelope, parse_pagination
from .base import APIHandler

logger = logging.getLogger(__name__)


def _check_user_permission(handler: APIHandler, username: str):
    """Raise 403 unless the current user is *username* or an admin."""
    current = handler.get_current_user()
    if not current:
        raise web.HTTPError(401, "Authentication required")
    if not handler.is_admin() and current != username:
        raise web.HTTPError(403, "Permission denied")


def _get_user_or_404(app, username: str) -> orm.User:
    user = app.db.query(orm.User).filter_by(name=username).first()
    if user is None:
        raise web.HTTPError(404, f"User {username!r} not found")
    return user


class UserTokenListAPIHandler(APIHandler):
    """GET /api/users/:name/tokens  — list tokens
    POST /api/users/:name/tokens — create a new token
    """

    async def get(self, username: str):
        """List all tokens for *username* (metadata only, no raw values)."""
        _check_user_permission(self, username)
        user = _get_user_or_404(self.app, username)

        limit, offset = parse_pagination(self)
        q = (
            self.app.db.query(orm.APIToken)
            .filter_by(user_id=user.id)
            .order_by(orm.APIToken.created.desc())
        )
        tokens, total = paginate_query(q, limit, offset)

        response = {"tokens": [t.to_dict() for t in tokens]}
        response.update(pagination_envelope(total, limit, offset))
        self.write(response)

    async def post(self, username: str):
        """Create a new API token for *username*.

        Request body (all fields optional)::

            {
                "name": "ci-pipeline",
                "expires_in": 86400,
                "scopes": ["hubs:list"],
                "note": "Used by GitHub Actions"
            }

        The raw token value is included in the 201 response **once only**.
        It cannot be retrieved later — store it securely immediately.
        """
        _check_user_permission(self, username)
        user = _get_user_or_404(self.app, username)

        body = self.get_json_body() or {}
        name = body.get("name")
        note = body.get("note")
        scopes = body.get("scopes")
        expires_at = None

        expires_in = body.get("expires_in")
        if expires_in is not None:
            try:
                expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in))
            except (ValueError, TypeError):
                raise web.HTTPError(400, "expires_in must be an integer number of seconds")

        if scopes is not None and not isinstance(scopes, list):
            raise web.HTTPError(400, "scopes must be a list of strings")

        token_orm, raw = orm.APIToken.new(
            user_id=user.id,
            name=name,
            scopes=scopes,
            expires_at=expires_at,
            note=note,
        )
        try:
            self.app.db.add(token_orm)
            self.app.db.commit()
            self.app.db.refresh(token_orm)
        except Exception as e:
            self.app.db.rollback()
            logger.error("Failed to create token for %s: %s", username, e)
            raise web.HTTPError(500, "Failed to create token")

        self.set_status(201)
        # Pass raw once — after this response the value is gone forever
        self.write(token_orm.to_dict(include_token=raw))


class UserTokenAPIHandler(APIHandler):
    """GET /api/users/:name/tokens/:token_id  — get token metadata
    DELETE /api/users/:name/tokens/:token_id — revoke token
    """

    def _get_token_or_404(self, user: orm.User, token_id: str) -> orm.APIToken:
        try:
            tid = int(token_id)
        except ValueError:
            raise web.HTTPError(404, f"Token {token_id!r} not found")
        token = (
            self.app.db.query(orm.APIToken)
            .filter_by(id=tid, user_id=user.id)
            .first()
        )
        if token is None:
            raise web.HTTPError(404, f"Token {token_id!r} not found")
        return token

    async def get(self, username: str, token_id: str):
        """Return token metadata (no raw value)."""
        _check_user_permission(self, username)
        user = _get_user_or_404(self.app, username)
        token = self._get_token_or_404(user, token_id)
        self.write(token.to_dict())

    async def delete(self, username: str, token_id: str):
        """Revoke (permanently delete) a token."""
        _check_user_permission(self, username)
        user = _get_user_or_404(self.app, username)
        token = self._get_token_or_404(user, token_id)
        try:
            self.app.db.delete(token)
            self.app.db.commit()
        except Exception as e:
            self.app.db.rollback()
            logger.error("Failed to revoke token %s for %s: %s", token_id, username, e)
            raise web.HTTPError(500, "Failed to revoke token")
        self.set_status(204)
        self.finish()
