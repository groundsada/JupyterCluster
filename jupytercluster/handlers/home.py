"""Home page handler"""

import logging

from .base import BaseHandler, DictObject

logger = logging.getLogger(__name__)


class HomeHandler(BaseHandler):
    """Home page showing user's hubs"""

    async def get(self):
        """Render home page with user's hubs"""
        user = self.get_user_or_redirect()
        if not user:
            return

        # Get hubs for this user
        app = self.jupytercluster
        user_hubs = []
        all_hubs = []

        for hub_name, hub in app.hubs.items():
            hub_dict = hub.to_dict()
            hub_obj = DictObject(hub_dict)
            all_hubs.append(hub_obj)

            # Filter by ownership unless admin
            if self.is_admin or hub.owner == user:
                user_hubs.append(hub_obj)

        hub_stats = {
            "running": sum(1 for h in user_hubs if h.status == "running"),
            "pending": sum(1 for h in user_hubs if h.status == "pending"),
            "stopped": sum(1 for h in user_hubs if h.status == "stopped"),
            "error": sum(1 for h in user_hubs if h.status == "error"),
        }

        # Render home page
        self.render_template(
            "home.html",
            hubs=user_hubs,
            all_hubs=all_hubs if self.is_admin else user_hubs,
            hub_stats=hub_stats,
            user=user,
        )
