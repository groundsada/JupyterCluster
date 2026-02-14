"""Hub management handlers for web UI"""

import json
import logging

from tornado import web

from .base import BaseHandler

logger = logging.getLogger(__name__)


class HubCreateHandler(BaseHandler):
    """Create a new hub via web UI"""

    async def get(self):
        """Show create hub form"""
        user = self.get_user_or_redirect()
        if not user:
            return

        self.render_template("hub_create.html")

    async def post(self):
        """Handle hub creation"""
        user = self.get_user_or_redirect()
        if not user:
            return

        hub_name = self.get_argument("name", "")
        description = self.get_argument("description", "")
        values_json = self.get_argument("values", "{}")

        if not hub_name:
            self.render_template("hub_create.html", error="Hub name is required")
            return

        try:
            values = json.loads(values_json) if values_json else {}
        except json.JSONDecodeError:
            self.render_template("hub_create.html", error="Invalid JSON in values field")
            return

        app = self.jupytercluster

        # Check if hub already exists
        if hub_name in app.hubs:
            self.render_template("hub_create.html", error=f"Hub {hub_name} already exists")
            return

        try:
            # Create hub
            hub = await app.create_hub(
                name=hub_name,
                owner=user,
                values=values,
                description=description,
            )

            # Redirect to hub page
            self.redirect(f"/hubs/{hub_name}")
        except Exception as e:
            logger.error(f"Failed to create hub {hub_name}: {e}")
            self.render_template("hub_create.html", error=f"Failed to create hub: {str(e)}")


class HubDetailHandler(BaseHandler):
    """View and manage a specific hub"""

    async def get(self, hub_name: str):
        """Show hub details"""
        user = self.get_user_or_redirect()
        if not user:
            return

        app = self.jupytercluster

        if hub_name not in app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name} not found")

        hub = app.hubs[hub_name]

        # Check permission
        if not self.is_admin and hub.owner != user:
            raise web.HTTPError(403, "Permission denied")

        self.render_template("hub_detail.html", hub=hub.to_dict())

    async def post(self, hub_name: str):
        """Handle hub actions (start, stop, delete)"""
        user = self.get_user_or_redirect()
        if not user:
            return

        app = self.jupytercluster

        if hub_name not in app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name} not found")

        hub = app.hubs[hub_name]

        # Check permission
        if not self.is_admin and hub.owner != user:
            raise web.HTTPError(403, "Permission denied")

        action = self.get_argument("action", "")

        try:
            if action == "start":
                await hub.start()
            elif action == "stop":
                await hub.stop()
            elif action == "delete":
                await app.delete_hub(hub_name)
                self.redirect("/")
                return
            else:
                raise web.HTTPError(400, f"Unknown action: {action}")

            # Redirect back to hub page
            self.redirect(f"/hubs/{hub_name}")
        except Exception as e:
            logger.error(f"Failed to {action} hub {hub_name}: {e}")
            self.render_template(
                "hub_detail.html", hub=hub.to_dict(), error=f"Failed to {action} hub: {str(e)}"
            )
