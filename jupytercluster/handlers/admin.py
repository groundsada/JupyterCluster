"""Admin page handler"""

import logging

from tornado import web

from .base import BaseHandler

logger = logging.getLogger(__name__)


class AdminHandler(BaseHandler):
    """Admin page for managing users and hubs"""

    async def get(self):
        """Render admin page"""
        user = self.get_user_or_redirect()
        if not user:
            return

        # Check if user is admin
        if not self.is_admin:
            raise web.HTTPError(403, "Admin access required")

        app = self.jupytercluster

        # Get all users
        users = app.db.query(app.orm.User).all()
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

        # Get all hubs
        all_hubs = []
        for hub_name, hub in app.hubs.items():
            all_hubs.append(hub.to_dict())

        self.render_template(
            "admin.html",
            users=user_list,
            hubs=all_hubs,
        )
