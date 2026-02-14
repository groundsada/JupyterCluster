"""Error handlers for JupyterCluster"""

import logging

from tornado import web

from .base import BaseHandler

logger = logging.getLogger(__name__)


class NotFoundHandler(BaseHandler):
    """404 Not Found handler"""

    async def get(self):
        """Handle 404 errors"""
        self.set_status(404)
        self.render_template("error.html", status_code=404, error_message="Page not found")


class ErrorHandler(BaseHandler):
    """Generic error handler"""

    def write_error(self, status_code, **kwargs):
        """Write error page"""
        error_message = "An error occurred"
        if "exc_info" in kwargs:
            exception = kwargs["exc_info"][1]
            error_message = str(exception)

        self.render_template(
            "error.html",
            status_code=status_code,
            error_message=error_message,
        )
