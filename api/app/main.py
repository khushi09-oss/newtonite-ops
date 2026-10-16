import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app import auth, db, inbox, items, notifications

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.pool.open()
    db.migrate()
    if (
        os.environ.get("SEED_ON_BOOT", "true").lower() in ("1", "true", "yes")
        and db.seed_if_empty()
    ):
        log.info("seeded the demo dataset into an empty database")
    yield
    db.pool.close()


app = FastAPI(title="Newtonite Ops", lifespan=lifespan)
app.include_router(auth.router)
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
