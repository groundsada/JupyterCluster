"""Hub management handlers for web UI"""

import logging

from tornado import web

from ..utils import format_config, parse_config
from .base import BaseHandler, DictObject

logger = logging.getLogger(__name__)


class HubCreateHandler(BaseHandler):
    """Create a new hub via web UI"""

    async def get(self):
        """Show create hub form"""
        user = self.get_user_or_redirect()
        if not user:
            return

        app = self.jupytercluster

        # Get user's namespace restrictions to show in UI
        from .. import orm

        db_user = app.db.query(orm.User).filter_by(name=user).first()
        allowed_namespace_prefixes = []
        max_hubs = None
        current_hub_count = 0
        if db_user:
            allowed_namespace_prefixes = db_user.allowed_namespace_prefixes or []
            max_hubs = db_user.max_hubs
            current_hub_count = len([h for h in app.hubs.values() if h.owner == user])

        self.render_template(
            "hub_create.html",
            allowed_namespace_prefixes=allowed_namespace_prefixes,
            max_hubs=max_hubs,
            current_hub_count=current_hub_count,
        )

    async def post(self):
        """Handle hub creation"""
        user = self.get_user_or_redirect()
        if not user:
            return

        hub_name = self.get_argument("name", "")
        description = self.get_argument("description", "")
        values_str = self.get_argument("values", "")

        if not hub_name:
            self.render_template("hub_create.html", error="Hub name is required")
            return

        try:
            values = parse_config(values_str) if values_str else {}
        except ValueError as e:
            self.render_template(
                "hub_create.html", error=f"Invalid YAML or JSON in values field: {str(e)}"
            )
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

        # Format values as YAML for display
        hub_dict = hub.to_dict()
        hub_dict["values_yaml"] = format_config(hub.values, format="yaml")

        # Ensure error_message is included - check both dict, object, and ORM
        if not hub_dict.get("error_message"):
            hub_dict["error_message"] = (
                getattr(hub, "error_message", "")
                or getattr(hub.orm_hub, "error_message", None)
                or ""
            )

        # DEBUG: Ensure status is a string
        if "status" in hub_dict:
            hub_dict["status"] = str(hub_dict["status"])

        # Convert dict to object for template compatibility (Tornado templates use attribute access)
        hub_obj = DictObject(hub_dict)
        self.render_template("hub_detail.html", hub=hub_obj)

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
        values_str = self.get_argument("values", None)
        description = self.get_argument("description", None)

        try:
            # Handle values update
            if values_str is not None:
                try:
                    values = parse_config(values_str) if values_str else {}
                    # Update hub values through spawner validation
                    spawner = hub.get_spawner()
                    sanitized_values = spawner._validate_helm_values(values)
                    hub.values = sanitized_values
                    hub._save_to_orm()
                    app.db.commit()
                except ValueError as e:
                    hub_dict = hub.to_dict()
                    hub_dict["values_yaml"] = format_config(hub.values, format="yaml")
                    hub_obj = DictObject(hub_dict)
                    self.render_template(
                        "hub_detail.html",
                        hub=hub_obj,
                        error=f"Invalid YAML or JSON in values: {str(e)}",
                    )
                    return

            # Handle description update
            if description is not None:
                hub.description = description
                hub._save_to_orm()
                app.db.commit()

            # Handle actions
            if action == "start":
                await hub.start()
                app.db.commit()  # Commit after start to save error_message if any
            elif action == "stop":
                await hub.stop()
                app.db.commit()  # Commit after stop to save error_message if any
            elif action == "delete":
                await app.delete_hub(hub_name)
                self.redirect("/")
                return
            elif action:
                raise web.HTTPError(400, f"Unknown action: {action}")

            # Redirect back to hub page
            self.redirect(f"/hubs/{hub_name}")
        except Exception as e:
            logger.error(f"Failed to {action} hub {hub_name}: {e}")
            # Commit any error_message that was stored
            try:
                app.db.commit()
            except Exception:
                pass

            # Reload hub to get updated error_message
            app._load_hubs()
            hub = app.hubs.get(hub_name)
            if hub:
                hub_dict = hub.to_dict()
                hub_dict["values_yaml"] = format_config(hub.values, format="yaml")
                # Ensure error_message is included - check both dict and object
                if not hub_dict.get("error_message"):
                    hub_dict["error_message"] = (
                        getattr(hub, "error_message", "")
                        or getattr(hub.orm_hub, "error_message", None)
                        or ""
                    )
                hub_obj = DictObject(hub_dict)
                self.render_template(
                    "hub_detail.html", hub=hub_obj, error=f"Failed to {action} hub: {str(e)}"
                )
            else:
                raise
