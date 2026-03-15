"""GET /api/info — server capabilities and version.

Unauthenticated endpoint that lets clients discover the server's version
and feature set before establishing a session, mirroring JupyterHub's
/hub/api/ info response.
"""

import platform
import sys

from .base import APIHandler


class InfoAPIHandler(APIHandler):
    """GET /api/info — return server version and capabilities."""

    def get(self):
        """Return server metadata.  No authentication required.

        Response::

            {
                "version": "0.1.0",
                "python": "3.11.0",
                "auth_type": "password",
                "features": {
                    "token_auth": true,
                    "cors": true,
                    "pagination": true,
                    "hub_events": true,
                    "namespace_deletion": false
                }
            }
        """
        from .._version import __version__

        app = self.application.settings.get("jupytercluster")

        auth_type = "password"
        if app and hasattr(app, "authenticator"):
            cls_name = type(app.authenticator).__name__
            if "OAuth" in cls_name:
                auth_type = "oauth"

        features = {
            "token_auth": True,
            "cors": True,
            "pagination": True,
            "hub_events": True,
            "namespace_creation": getattr(app, "allow_namespace_creation", True) if app else True,
            "namespace_deletion": getattr(app, "allow_namespace_deletion", False) if app else False,
            "user_namespace_management": (
                getattr(app, "allow_user_namespace_management", True) if app else True
            ),
        }

        self.write(
            {
                "version": __version__,
                "python": platform.python_version(),
                "auth_type": auth_type,
                "features": features,
            }
        )
