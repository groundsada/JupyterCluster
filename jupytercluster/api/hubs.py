"""API handlers for hub management"""

import logging
from typing import Optional

from tornado import web

from .base import APIHandler

logger = logging.getLogger(__name__)


class HubListAPIHandler(APIHandler):
    """List all hubs"""

    async def get(self):
        """GET /api/hubs - List all hubs"""
        current_user = self.get_current_user()
        is_admin = self.is_admin()

        # Get hubs from application
        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")
        hubs = []

        # Filter based on permissions
        for hub in app.hubs.values():
            hub_dict = hub.to_dict()

            # Users can only see their own hubs unless admin
            if not is_admin and hub.owner != current_user:
                continue

            hubs.append(hub_dict)

        self.write({"hubs": hubs})


class HubAPIHandler(APIHandler):
    """Get, create, update, delete a specific hub"""

    async def get(self, hub_name: str):
        """GET /api/hubs/:name - Get hub details"""
        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        if hub_name not in app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name} not found")

        hub = app.hubs[hub_name]

        # Check permission
        self.require_hub_permission(hub.owner)

        self.write(hub.to_dict())

    async def post(self, hub_name: str):
        """POST /api/hubs/:name - Create a new hub"""
        current_user = self.get_current_user()
        if not current_user:
            raise web.HTTPError(401, "Authentication required")

        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        # Check if hub already exists
        if hub_name in app.hubs:
            raise web.HTTPError(409, f"Hub {hub_name} already exists")

        # Get configuration from request body
        body = self.get_json_body() or {}
        values = body.get("values", {})
        description = body.get("description", "")

        # Create hub
        try:
            hub = await app.create_hub(
                name=hub_name,
                owner=current_user,
                values=values,
                description=description,
            )

            self.set_status(201)
            self.write(hub.to_dict())
        except Exception as e:
            logger.error(f"Failed to create hub {hub_name}: {e}")
            raise web.HTTPError(500, f"Failed to create hub: {str(e)}")

    async def delete(self, hub_name: str):
        """DELETE /api/hubs/:name - Delete a hub"""
        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        if hub_name not in app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name} not found")

        hub = app.hubs[hub_name]

        # Check permission
        self.require_hub_permission(hub.owner)

        try:
            await app.delete_hub(hub_name)
            self.set_status(204)
        except Exception as e:
            logger.error(f"Failed to delete hub {hub_name}: {e}")
            raise web.HTTPError(500, f"Failed to delete hub: {str(e)}")


class HubActionAPIHandler(APIHandler):
    """Actions on hubs (start, stop, etc.)"""

    async def post(self, hub_name: str, action: str):
        """POST /api/hubs/:name/:action - Perform action on hub"""
        app = self.application.settings.get("jupytercluster")
        if not app:
            raise web.HTTPError(500, "JupyterCluster application not found")

        if hub_name not in app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name} not found")

        hub = app.hubs[hub_name]

        # Check permission
        self.require_hub_permission(hub.owner)

        try:
            if action == "start":
                await hub.start()
                self.write({"status": "started", "hub": hub.to_dict()})
            elif action == "stop":
                await hub.stop()
                self.write({"status": "stopped", "hub": hub.to_dict()})
            else:
                raise web.HTTPError(400, f"Unknown action: {action}")
        except Exception as e:
            logger.error(f"Failed to {action} hub {hub_name}: {e}")
            raise web.HTTPError(500, f"Failed to {action} hub: {str(e)}")
