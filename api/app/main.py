import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import auth, db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.pool.open()
    db.migrate()
    if os.environ.get("SEED_ON_BOOT", "true").lower() in ("1", "true", "yes") and db.seed_if_empty():
        log.info("seeded the demo dataset into an empty database")
    yield
    db.pool.close()


app = FastAPI(title="Newtonite Ops", lifespan=lifespan)
app.include_router(auth.router)


@app.get("/health")
def health():
    with db.pool.connection() as conn:
        conn.execute("select 1")
    return {"ok": True}
