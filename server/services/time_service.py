"""
server/services/time_service.py
----------------------------------
Time synchronisation service.
Returns the server's current UTC time as an ISO-8601 string.
Clients use this to align their clocks for session timing and logs.
"""

from datetime import datetime, timezone

from server.models.schemas import ok


async def get_server_time() -> dict:
    """Return current UTC time as ISO-8601."""
    now = datetime.now(tz=timezone.utc).isoformat()
    return ok({"utc_time": now})
