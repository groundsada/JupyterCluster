"""API handlers for user management"""

import logging

from tornado import web

from .base import APIHandler

logger = logging.getLogger(__name__)


class UserListAPIHandler(APIHandler):
    """List all users (admin only)"""

    async def get(self):
        """GET /api/users - List all users"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        from .. import orm

        users = app.db.query(orm.User).all()
        user_list = []
        for u in users:
            user_list.append(
                {
                    "name": u.name,
                    "admin": u.admin,
                    "max_hubs": u.max_hubs,
                    "allowed_namespace_prefixes": u.allowed_namespace_prefixes or [],
                    "created": u.created.isoformat() if u.created else None,
                    "last_activity": u.last_activity.isoformat() if u.last_activity else None,
                }
            )

        self.write({"users": user_list})


class UserAPIHandler(APIHandler):
    """Get, create, update, delete a specific user (admin only)"""

    async def get(self, username: str):
        """GET /api/users/:name - Get user details"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        from .. import orm

        user = app.db.query(orm.User).filter_by(name=username).first()
        if not user:
            raise web.HTTPError(404, f"User {username} not found")

        self.write(
            {
                "name": user.name,
                "admin": user.admin,
                "max_hubs": user.max_hubs,
                "allowed_namespace_prefixes": user.allowed_namespace_prefixes or [],
                "created": user.created.isoformat() if user.created else None,
                "last_activity": user.last_activity.isoformat() if user.last_activity else None,
            }
        )

    async def post(self, username: str):
        """POST /api/users/:name - Create a new user"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        # Check if user already exists
        from .. import orm

        existing = app.db.query(orm.User).filter_by(name=username).first()
        if existing:
            raise web.HTTPError(409, f"User {username} already exists")

        # Get configuration from request body
        body = self.get_json_body() or {}
        admin = body.get("admin", False)
        max_hubs = body.get("max_hubs")
        allowed_namespace_prefixes = body.get("allowed_namespace_prefixes")

        try:
            user = orm.User(
                name=username,
                admin=admin,
                max_hubs=max_hubs,
                allowed_namespace_prefixes=allowed_namespace_prefixes,
            )
            app.db.add(user)
            app.db.commit()

            self.set_status(201)
            self.write(
                {
                    "name": user.name,
                    "admin": user.admin,
                    "max_hubs": user.max_hubs,
                    "allowed_namespace_prefixes": user.allowed_namespace_prefixes or [],
                }
            )
        except Exception as e:
            logger.error(f"Failed to create user {username}: {e}")
            app.db.rollback()
            raise web.HTTPError(500, f"Failed to create user: {str(e)}")

    async def put(self, username: str):
        """PUT /api/users/:name - Update a user"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        from .. import orm

        user = app.db.query(orm.User).filter_by(name=username).first()
        if not user:
            raise web.HTTPError(404, f"User {username} not found")

        # Get update data from request body
        body = self.get_json_body() or {}

        try:
            if "admin" in body:
                user.admin = body["admin"]
            if "max_hubs" in body:
                user.max_hubs = body["max_hubs"]
            if "allowed_namespace_prefixes" in body:
                user.allowed_namespace_prefixes = body["allowed_namespace_prefixes"]

            app.db.commit()

            self.write(
                {
                    "name": user.name,
                    "admin": user.admin,
                    "max_hubs": user.max_hubs,
                    "allowed_namespace_prefixes": user.allowed_namespace_prefixes or [],
                }
            )
        except Exception as e:
            logger.error(f"Failed to update user {username}: {e}")
            app.db.rollback()
            raise web.HTTPError(500, f"Failed to update user: {str(e)}")

    async def delete(self, username: str):
        """DELETE /api/users/:name - Delete a user"""
        if not self.is_admin():
            raise web.HTTPError(403, "Admin access required")

        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        from .. import orm

        user = app.db.query(orm.User).filter_by(name=username).first()
        if not user:
            raise web.HTTPError(404, f"User {username} not found")

        # Check if user owns any hubs
        user_hubs = [h for h in app.hubs.values() if h.owner == username]
        if user_hubs:
            raise web.HTTPError(
                400, f"Cannot delete user {username}: user owns {len(user_hubs)} hub(s)"
            )

        try:
            app.db.delete(user)
            app.db.commit()
            self.set_status(204)
        except Exception as e:
            logger.error(f"Failed to delete user {username}: {e}")
            app.db.rollback()
            raise web.HTTPError(500, f"Failed to delete user: {str(e)}")
