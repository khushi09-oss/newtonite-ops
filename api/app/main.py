import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app import auth, db, google, inbox, items, notifications

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.pool.open()
    db.migrate()
    # Off unless switched on: a real deployment must never fill itself with demo people.
    if auth.flag("SEED_ON_BOOT") and db.seed_if_empty():
        log.info("seeded the demo dataset into an empty database")
    yield
    db.pool.close()


app = FastAPI(title="Newtonite Ops", lifespan=lifespan)

ALLOWED_ORIGINS = {
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
}


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


app.include_router(auth.router)
app.include_router(google.router)
app.include_router(items.router)
app.include_router(notifications.router)
app.include_router(inbox.router)


@app.exception_handler(items.Conflict)
def stale_version(request: Request, exc: items.Conflict):
    """409 with the current row and its recent history, so the client can show who changed what."""
    with db.pool.connection() as conn:
        current = items.load_item(conn, exc.item["id"])
        events = items.recent_events(conn, exc.item["id"])
    return JSONResponse(
        jsonable_encoder({"detail": "stale version", "current": current, "events": events}), 409
    )


@app.get("/health")
def health():
    with db.pool.connection() as conn:
        conn.execute("select 1")
    return {"ok": True}
