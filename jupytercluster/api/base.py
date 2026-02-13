"""Base API handler"""

import json
import logging
from typing import Optional

from tornado import web
from tornado.log import access_log

logger = logging.getLogger(__name__)


class APIHandler(web.RequestHandler):
    """Base class for API handlers"""

    def set_default_headers(self):
        """Set default headers"""
        self.set_header("Content-Type", "application/json")

    def write_error(self, status_code: int, **kwargs):
        """Write error response"""
        self.set_header("Content-Type", "application/json")
        if "exc_info" in kwargs:
            exception = kwargs["exc_info"][1]
            message = str(exception)
        else:
            message = self._reason

        self.write(
            json.dumps(
                {
                    "error": {
                        "status_code": status_code,
                        "message": message,
                    }
                }
            )
        )

    def get_json_body(self) -> Optional[dict]:
        """Get JSON body from request"""
        try:
            return json.loads(self.request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def get_current_user(self) -> Optional[str]:
        """Get current authenticated user"""
        # Placeholder - implement actual authentication
        # In production, check tokens, sessions, etc.
        return self.request.headers.get("X-User", None)

    def is_admin(self) -> bool:
        """Check if current user is admin"""
        # Placeholder - implement actual admin check
        admin_header = self.request.headers.get("X-Admin", "false")
        return admin_header.lower() == "true"

    def check_hub_permission(self, hub_owner: str) -> bool:
        """Check if current user can manage a hub"""
        current_user = self.get_current_user()
        if not current_user:
            return False

        # Admins can manage all hubs
        if self.is_admin():
            return True

        # Users can manage their own hubs
        return current_user == hub_owner

    def require_hub_permission(self, hub_owner: str):
        """Require permission to manage a hub, raise 403 if not"""
        if not self.check_hub_permission(hub_owner):
            raise web.HTTPError(403, "Permission denied")

