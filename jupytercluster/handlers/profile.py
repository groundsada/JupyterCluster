"""User profile handler - shows user's own settings and restrictions"""

import logging

from .base import BaseHandler, DictObject

logger = logging.getLogger(__name__)


class ProfileHandler(BaseHandler):
    """User profile page showing their own settings and restrictions"""

    async def get(self):
        """Render user profile page"""
        user = self.get_user_or_redirect()
        if not user:
            return

        app = self.jupytercluster

        # Get user from database
        from .. import orm

        db_user = app.db.query(orm.User).filter_by(name=user).first()

        # Get user's hubs
        user_hubs = []
        for hub_name, hub in app.hubs.items():
            if hub.owner == user:
                hub_dict = hub.to_dict()
                hub_obj = DictObject(hub_dict)
                user_hubs.append(hub_obj)

        # Get user's namespace restrictions
        allowed_namespaces = []
        max_hubs = None
        if db_user:
            allowed_namespaces = db_user.allowed_namespaces or []
            max_hubs = db_user.max_hubs

        # Get all namespaces user has access to (based on their hubs)
        accessible_namespaces = [hub.namespace for hub in user_hubs]

        # Render profile page
        self.render_template(
            "profile.html",
            user=user,
            is_admin=self.is_admin,
            allowed_namespaces=allowed_namespaces,
            max_hubs=max_hubs,
            current_hub_count=len(user_hubs),
            accessible_namespaces=accessible_namespaces,
            hubs=user_hubs,
        )
