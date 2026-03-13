"""API handlers for user management"""

import logging

from tornado import web

from .. import orm
from ..pagination import paginate_query, pagination_envelope, parse_pagination
from .base import APIHandler

logger = logging.getLogger(__name__)


def _user_dict(user: orm.User) -> dict:
    """Serialise a User ORM object to a JSON-safe dict."""
    return {
        "name": user.name,
        "admin": user.admin,
        "max_hubs": user.max_hubs,
        "allowed_namespaces": user.allowed_namespaces or [],
        "can_create_namespaces": user.can_create_namespaces,
        "can_delete_namespaces": user.can_delete_namespaces,
        "created": user.created.isoformat() if user.created else None,
        "last_activity": user.last_activity.isoformat() if user.last_activity else None,
    }


def _get_user_or_404(app, username: str) -> orm.User:
    user = app.db.query(orm.User).filter_by(name=username).first()
    if not user:
        raise web.HTTPError(404, f"User {username!r} not found")
    return user


class UserListAPIHandler(APIHandler):
    """List all users (admin only)"""

    async def get(self):
        """GET /api/users — list all users (admin only).

        Supports ``?limit=`` and ``?offset=`` for pagination.
        """
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        limit, offset = parse_pagination(self)
        q = self.app.db.query(orm.User).order_by(orm.User.name)
        users, total = paginate_query(q, limit, offset)

        response = {"users": [_user_dict(u) for u in users]}
        response.update(pagination_envelope(total, limit, offset))
        self.write(response)


class UserAPIHandler(APIHandler):
    """Get, create, update, delete a specific user (admin only)"""

    async def get(self, username: str):
        """GET /api/users/:name — get user details"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")
        user = _get_user_or_404(self.app, username)
        self.write(_user_dict(user))

    async def post(self, username: str):
        """POST /api/users/:name — create a new user"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        if self.app.db.query(orm.User).filter_by(name=username).first():
            raise web.HTTPError(409, f"User {username!r} already exists")

        body = self.get_json_body() or {}
        try:
            user = orm.User(
                name=username,
                admin=body.get("admin", False),
                max_hubs=body.get("max_hubs"),
                allowed_namespaces=body.get("allowed_namespaces"),
                can_create_namespaces=body.get("can_create_namespaces"),
                can_delete_namespaces=body.get("can_delete_namespaces"),
            )
            self.app.db.add(user)
            self.app.db.commit()
            self.set_status(201)
            self.write(_user_dict(user))
        except Exception as e:
            logger.error("Failed to create user %s: %s", username, e)
            self.app.db.rollback()
            raise web.HTTPError(500, f"Failed to create user: {e}")

    async def put(self, username: str):
        """PUT /api/users/:name — update a user"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        user = _get_user_or_404(self.app, username)
        body = self.get_json_body() or {}
        try:
            if "admin" in body:
                user.admin = body["admin"]
            if "max_hubs" in body:
                user.max_hubs = body["max_hubs"]
            if "allowed_namespaces" in body:
                user.allowed_namespaces = body["allowed_namespaces"]
            if "can_create_namespaces" in body:
                user.can_create_namespaces = body["can_create_namespaces"]
            if "can_delete_namespaces" in body:
                user.can_delete_namespaces = body["can_delete_namespaces"]
            self.app.db.commit()
            self.write(_user_dict(user))
        except Exception as e:
            logger.error("Failed to update user %s: %s", username, e)
            self.app.db.rollback()
            raise web.HTTPError(500, f"Failed to update user: {e}")

    async def delete(self, username: str):
        """DELETE /api/users/:name — delete a user"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        user = _get_user_or_404(self.app, username)
        user_hubs = [h for h in self.app.hubs.values() if h.owner == username]
        if user_hubs:
            raise web.HTTPError(
                400, f"Cannot delete {username!r}: user owns {len(user_hubs)} hub(s)"
            )
        try:
            self.app.db.delete(user)
            self.app.db.commit()
            self.set_status(204)
        except Exception as e:
            logger.error("Failed to delete user %s: %s", username, e)
            self.app.db.rollback()
            raise web.HTTPError(500, f"Failed to delete user: {e}")
