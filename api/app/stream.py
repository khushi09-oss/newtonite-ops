"""Live updates over server-sent events. One connection per browser tab.

Postgres LISTEN does not work through Neon's transaction pooler, so each connection checks for
changes every POLL_SECONDS with one small indexed query, and pushes an event when something moved:
- `item`: the open item's version changed (the client refetches it).
- `notifications`: the newest notification for this user changed (the client refetches the count).
Every check also re-validates the session and the item access, so signing out, deactivation or
leaving the team stops the stream. Connections close before the platform's duration limit and the
browser reconnects on its own."""

import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app import auth, db
from app.auth import current_user
from app.items import require_access

router = APIRouter()
POLL_SECONDS = 1.5
HEARTBEAT_SECONDS = 15.0
STREAM_SECONDS = 240.0  # under Vercel's 300 s function limit; the browser reconnects after this
RETRY_MS = 2000

TICK = (
    "select ({live}) as user_id,"
    " (select w.version from work_items w"
    "    left join team_members m on m.team_id = w.team_id and m.user_id = %(me)s"
    "   where w.id = %(item)s and (%(admin)s or m.user_id is not null)) as version,"
    " (select coalesce(max(n.id), 0) from notifications n where n.user_id = %(me)s) as latest"
).format(
    live=auth.LIVE_SESSION.format(cols="u.id").replace("%s", "%(sid)s"),
)


def tick(sid, user: dict, item_id) -> dict:
    with db.pool.connection() as conn:
        return conn.execute(
            TICK, {"sid": sid, "me": user["id"], "admin": user["is_admin"], "item": item_id}
        ).fetchone()


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def events(request: Request, sid, user: dict, item_id):
    yield f"retry: {RETRY_MS}\n\n"
    deadline = time.monotonic() + STREAM_SECONDS
    last_beat = time.monotonic()
    seen_version = seen_latest = None
    while time.monotonic() < deadline:
        if await request.is_disconnected():
            return
        row = await run_in_threadpool(tick, sid, user, item_id)
        if row["user_id"] is None:
            yield event("ended", {"reason": "session"})
            return
        if item_id is not None and row["version"] is None:
            yield event("ended", {"reason": "access"})
            return
        if item_id is not None and row["version"] != seen_version:
            seen_version = row["version"]
            yield event("item", {"id": str(item_id), "version": seen_version})
        if row["latest"] != seen_latest:
            seen_latest = row["latest"]
            yield event("notifications", {"latest": seen_latest})
        if time.monotonic() - last_beat > HEARTBEAT_SECONDS:
            last_beat = time.monotonic()
            yield ": still here\n\n"  # keeps proxies from closing an idle connection
        await asyncio.sleep(POLL_SECONDS)


@router.get("/stream")
def stream(request: Request, item: UUID | None = None, user: dict = Depends(current_user)):
    """Outsiders get 404 here, before any event is sent, like every other item route."""
    if item is not None:
        with db.pool.connection() as conn:
            require_access(conn, user, item)
    sid = auth.session_id(request)
    return StreamingResponse(
        events(request, sid, user, item),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
