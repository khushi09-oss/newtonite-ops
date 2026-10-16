from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.pool.open()
    db.migrate()
    yield
    db.pool.close()


app = FastAPI(title="Newtonite Ops", lifespan=lifespan)


@app.get("/health")
def health():
    with db.pool.connection() as conn:
        conn.execute("select 1")
    return {"ok": True}
