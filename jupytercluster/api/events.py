"""Hub events / audit log endpoint.

GET /api/hubs/:name/events

Exposes the HubEvent records that are written on every hub lifecycle
operation (created, started, stopped, deleted, error).  Supports
limit/offset pagination and optional time-based filtering via ``?since=``.
"""

import logging
from datetime import datetime

from tornado import web

from .. import orm
from ..pagination import paginate_query, pagination_envelope, parse_pagination
from .base import APIHandler

logger = logging.getLogger(__name__)


class HubEventsAPIHandler(APIHandler):
    """GET /api/hubs/:name/events — list audit events for a hub."""

    async def get(self, hub_name: str):
        """Return paginated hub events, newest first.

        Query parameters:

        ``limit`` (int, default 100, max 500)
            Maximum number of events to return.

        ``offset`` (int, default 0)
            Number of events to skip (for paging).

        ``since`` (ISO-8601 datetime string, optional)
            Only return events whose ``timestamp >= since``.

        Response::

            {
                "hub": "my-hub",
                "events": [
                    {
                        "id": 42,
                        "event_type": "started",
                        "message": "Hub started successfully",
                        "timestamp": "2026-03-11T10:00:00"
                    },
                    ...
                ],
                "_pagination": {"offset": 0, "limit": 100, "total": 1, "next_offset": null}
            }
        """
        if hub_name not in self.app.hubs:
            raise web.HTTPError(404, f"Hub {hub_name!r} not found")

        hub = self.app.hubs[hub_name]
        self.require_hub_permission(hub.owner)

        limit, offset = parse_pagination(self)

        # Optional time filter
        since = None
        since_str = self.get_argument("since", None)
        if since_str:
            try:
                since = datetime.fromisoformat(since_str)
            except ValueError:
                raise web.HTTPError(
                    400,
                    f"Invalid 'since' value {since_str!r}. Use ISO-8601 format, e.g. 2026-01-01T00:00:00",
                )

        q = (
            self.app.db.query(orm.HubEvent)
            .filter_by(hub_id=hub.orm_hub.id)
            .order_by(orm.HubEvent.timestamp.desc())
        )
        if since is not None:
            q = q.filter(orm.HubEvent.timestamp >= since)

        events, total = paginate_query(q, limit, offset)

        response = {
            "hub": hub_name,
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "message": e.message,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in events
            ],
        }
        response.update(pagination_envelope(total, limit, offset))
        self.set_header("Content-Type", "application/json")
        self.write(response)
