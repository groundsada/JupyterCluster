"""API handlers for hub management"""

import logging
from typing import Optional

from tornado import web

from ..pagination import pagination_envelope, parse_pagination
from ..utils import parse_config
from .base import APIHandler

logger = logging.getLogger(__name__)


class HubListAPIHandler(APIHandler):
    """List all hubs"""

    async def get(self):
        """GET /api/hubs - List all hubs

        Supports ``?limit=`` and ``?offset=`` for pagination.
        Optional ``?status=running|stopped|pending|error`` filter.
        """
        current_user = self.get_current_user()
        is_admin = self.is_admin()

        status_filter = self.get_argument("status", None)
        limit, offset = parse_pagination(self)

        # Filter by permission and optional status
        visible = [
            hub.to_dict()
            for hub in self.app.hubs.values()
            if (is_admin or hub.owner == current_user)
            and (status_filter is None or hub.status == status_filter)
        ]

        total = len(visible)
        page = visible[offset : offset + limit]

        response = {"hubs": page}
        response.update(pagination_envelope(total, limit, offset))
        self.write(response)


class HubAPIHandler(APIHandler):
    """Get, create, update, delete a specific hub"""

    def _get_hub_or_404(self, hub_name: str):
        if hub_name not in self.app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name!r} not found")
        return self.app.hubs[hub_name]

    async def get(self, hub_name: str):
        """GET /api/hubs/:name - Get hub details"""
        hub = self._get_hub_or_404(hub_name)
        self.require_hub_permission(hub.owner)
        self.write(hub.to_dict())

    async def post(self, hub_name: str):
        """POST /api/hubs/:name - Create a new hub"""
        current_user = self.get_current_user()
        if not current_user:
            raise web.HTTPError(401, "Authentication required")

        if hub_name in self.app.hubs:
            raise web.HTTPError(409, f"Hub {hub_name!r} already exists")

        body = self.get_json_body() or {}
        values_input = body.get("values")
        description = body.get("description", "")
        namespace = body.get("namespace")

        if isinstance(values_input, str):
            try:
                values = parse_config(values_input) if values_input else {}
            except ValueError as e:
                raise web.HTTPError(400, f"Invalid YAML or JSON in values: {e}")
        else:
            values = values_input if values_input is not None else {}

        try:
            hub = await self.app.create_hub(
                name=hub_name,
                owner=current_user,
                values=values,
                description=description,
                namespace=namespace,
            )
            self.set_status(201)
            self.write(hub.to_dict())
        except ValueError as e:
            raise web.HTTPError(403, str(e))
        except Exception as e:
            logger.error("Failed to create hub %s: %s", hub_name, e)
            raise web.HTTPError(500, f"Failed to create hub: {e}")

    async def put(self, hub_name: str):
        """PUT /api/hubs/:name - Update a hub"""
        current_user = self.get_current_user()
        if not current_user:
            raise web.HTTPError(401, "Authentication required")

        hub = self._get_hub_or_404(hub_name)
        self.require_hub_permission(hub.owner)

        body = self.get_json_body() or {}
        values_input = body.get("values")
        description = body.get("description")

        values = None
        if values_input is not None:
            if isinstance(values_input, str):
                try:
                    values = parse_config(values_input) if values_input else {}
                except ValueError as e:
                    raise web.HTTPError(400, f"Invalid YAML or JSON in values: {e}")
            else:
                values = values_input

        try:
            if values is not None:
                spawner = hub.get_spawner()
                hub.values = spawner._validate_helm_values(values)
            if description is not None:
                hub.description = description
            hub._save_to_orm()
            self.app.db.commit()
            self.write(hub.to_dict())
        except Exception as e:
            logger.error("Failed to update hub %s: %s", hub_name, e)
            raise web.HTTPError(500, f"Failed to update hub: {e}")

    async def delete(self, hub_name: str):
        """DELETE /api/hubs/:name - Delete a hub"""
        hub = self._get_hub_or_404(hub_name)
        self.require_hub_permission(hub.owner)
        try:
            await self.app.delete_hub(hub_name, caller=self.get_current_user())
            self.set_status(204)
        except ValueError as e:
            raise web.HTTPError(403, str(e))
        except Exception as e:
            logger.error("Failed to delete hub %s: %s", hub_name, e)
            raise web.HTTPError(500, f"Failed to delete hub: {e}")


class HubActionAPIHandler(APIHandler):
    """Actions on hubs (start, stop)"""

    async def post(self, hub_name: str, action: str):
        """POST /api/hubs/:name/:action - Perform action on hub"""
        if hub_name not in self.app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name!r} not found")

        hub = self.app.hubs[hub_name]
        self.require_hub_permission(hub.owner)

        try:
            if action == "start":
                await hub.start()
                self.write({"status": "started", "hub": hub.to_dict()})
            elif action == "stop":
                await hub.stop()
                self.write({"status": "stopped", "hub": hub.to_dict()})
            else:
                raise web.HTTPError(400, f"Unknown action: {action!r}")
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error("Failed to %s hub %s: %s", action, hub_name, e)
            raise web.HTTPError(500, f"Failed to {action} hub: {e}")
