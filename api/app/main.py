import logging
import os
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from starlette.background import BackgroundTask

from app import (
    admin,
    auth,
    dashboard,
    db,
    demo,
    google,
    inbox,
    items,
    notifications,
    outbox,
    stream,
    teams,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


# Off unless switched on: a real deployment must never fill itself with demo people.
SEED_ON_BOOT = auth.flag("SEED_ON_BOOT")
# On where no worker process runs (Vercel): each instance drains the outbox after it answers.
DRAIN_IN_REQUESTS = auth.flag("DRAIN_OUTBOX_IN_REQUESTS")
CRON_SECRET = os.environ.get("CRON_SECRET", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.startup(SEED_ON_BOOT)
    yield
    db.pool.close()  # only at process exit: a closed pool cannot be reopened


app = FastAPI(title="Newtonite Ops", lifespan=lifespan)

ALLOWED_ORIGINS = {
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
} | {  # on Vercel, the deployment's own URLs, so production and every preview accept themselves
    f"https://{os.environ[v]}"
    for v in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL")
    if os.environ.get(v)
}
_last_drain = 0.0


@app.middleware("http")
async def refuse_cross_site_writes(request: Request, call_next):
    """Browsers send Origin on every cross-site write. One from a page that is not ours is refused,
    so another site cannot make a signed-in user's browser change things. Requests without Origin
    (curl, scripts) carry no ambient cookie risk and pass."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "cross-site request refused"}, 403)
    return await call_next(request)


@app.middleware("http")
async def start_and_drain(request: Request, call_next):
    """Make sure this process has started, even if the platform skipped the startup hook. Where no
    worker runs, drain one outbox batch after the response is sent, at most once a second here."""
    global _last_drain
    await run_in_threadpool(db.startup, SEED_ON_BOOT)
    response = await call_next(request)
    if DRAIN_IN_REQUESTS and response.background is None and time.monotonic() - _last_drain > 1:
        _last_drain = time.monotonic()
        response.background = BackgroundTask(outbox.drain)
    return response


app.include_router(auth.router)
app.include_router(google.router)
app.include_router(items.router)
app.include_router(notifications.router)
app.include_router(inbox.router)
app.include_router(admin.router)
app.include_router(demo.router)
app.include_router(stream.router)
app.include_router(dashboard.router)
app.include_router(teams.router)


@app.exception_handler(items.Conflict)
def stale_version(request: Request, exc: items.Conflict):
    """409 with the current row and its recent history, so the client can show who changed what."""
    with db.pool.connection() as conn:
        current = items.load_item(conn, exc.item["id"])
        events = items.recent_events(conn, exc.item["id"])
    return JSONResponse(
        jsonable_encoder({"detail": "stale version", "current": current, "events": events}), 409
    )


@app.exception_handler(PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
def database_unavailable(request: Request, exc: Exception):
    """The database is down, waking, or dropped the connection mid-request. Every route gets the same
    plain 503 rather than a bare 500. Any write is one transaction, so nothing is half-saved, and a
    retry with the same Idempotency-Key cannot run twice."""
    log.error("database unavailable on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        {"detail": "The database is not answering right now. Try again in a moment."}, 503
    )


@app.get("/health")
def health():
    with db.pool.connection() as conn:
        conn.execute("select 1")
    return {"ok": True}


@app.get("/internal/drain")
def scheduled_drain(request: Request):
    """The daily scheduled sweep: jobs left waiting while nobody used the app, then housekeeping.
    Vercel's scheduler
    sends `Authorization: Bearer $CRON_SECRET`; anything else is refused."""
    if not CRON_SECRET or request.headers.get("authorization") != f"Bearer {CRON_SECRET}":
        raise HTTPException(404, "Not Found")
    processed = outbox.drain(max_batches=50)
    with db.pool.connection() as conn:
        outbox.housekeeping(conn)  # on Vercel this is the only place it runs
    return {"processed": processed}
