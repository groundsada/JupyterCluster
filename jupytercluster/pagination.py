"""Pagination helpers for JupyterCluster API list endpoints.

Provides consistent limit/offset pagination across all list endpoints,
matching JupyterHub's approach of offset-based pagination with a standard
``_pagination`` envelope in responses.
"""

from typing import Any, List, Tuple

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def parse_pagination(handler) -> Tuple[int, int]:
    """Parse ``?limit=N&offset=M`` from a Tornado request handler.

    Returns ``(limit, offset)`` with bounds applied:
    - limit clamped to [1, MAX_LIMIT]
    - offset clamped to [0, ∞)
    """
    try:
        limit = int(handler.get_argument("limit", DEFAULT_LIMIT))
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    try:
        offset = int(handler.get_argument("offset", 0))
    except (ValueError, TypeError):
        offset = 0
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    return limit, offset


def paginate_query(query, limit: int, offset: int) -> Tuple[List[Any], int]:
    """Apply limit/offset to a SQLAlchemy query.

    Returns ``(items, total)`` where *total* is the unfiltered row count.
    The total is computed before slicing so callers can build next-page links.
    """
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return items, total


def pagination_envelope(total: int, limit: int, offset: int) -> dict:
    """Build the standard ``_pagination`` dict included in list responses.

    ``next_offset`` is ``None`` when the current page is the last one,
    making it easy for clients to detect end-of-results without comparing
    counts.
    """
    next_offset = offset + limit if (offset + limit) < total else None
    return {
        "_pagination": {
            "offset": offset,
            "limit": limit,
            "total": total,
            "next_offset": next_offset,
        }
    }
