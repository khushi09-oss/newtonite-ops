"""One connection pool, one migration runner, one seed runner. Raw SQL via psycopg 3."""

import logging
import os
import threading
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ops:ops@localhost:5432/ops")
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SEED = Path(__file__).resolve().parent.parent / "seed.sql"
MIGRATION_LOCK = 7_461_001  # any constant; only this app takes it


class LivePool(ConnectionPool):
    """A pool that never hands out a connection the database has dropped.

    Neon closes idle connections and suspends an idle database, which drops every connection at once.
    Without this, the first request after a quiet spell got a dead connection and answered 500. The
    library's own `check=` option does test connections, but it waits longer after each dead one it
    finds (1 s, 2 s, 4 s and on), so a pool full of dead connections took seconds or timed out. Here a
    dead one is dropped and the next taken at once, until a live or brand-new connection comes."""

    def getconn(self, timeout: float | None = None):
        for _ in range(self.max_size + 1):
            conn = super().getconn(timeout)
            try:
                self.check_connection(conn)
            except psycopg.OperationalError:
                self.putconn(conn)  # broken, so the pool discards it and opens a fresh one
                continue
            return conn
        raise PoolTimeout("the database kept dropping new connections")


# prepare_threshold=None: no server-side prepared statements, which a transaction-mode pooler such as
# Neon's can route to the wrong server connection. The cost is negligible at this query volume.
# max_idle: idle connections are retired after a minute, before the database closes them itself.
# timeout: a request waits at most 10 s for a connection, then the app answers 503 (main.py), instead
# of hanging for the library's default 30 s while the database is down.
pool = LivePool(
    DATABASE_URL,
    min_size=1,
    max_size=int(os.environ.get("DB_POOL_MAX", "20")),
    open=False,
    max_idle=60,
    timeout=10,
    kwargs={"row_factory": dict_row, "prepare_threshold": None},
)
log = logging.getLogger("db")
_started = False
_start_lock = threading.Lock()


def startup(seed: bool) -> None:
    """Open the pool, migrate, and optionally seed, exactly once per process. Called by the app's
    startup hook and again by the first request, so a platform that skips the hook still starts."""
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        pool.open()
        migrate()
        if seed and seed_if_empty():
            log.info("seeded the demo dataset into an empty database")
        _started = True


def migrate(target: ConnectionPool | None = None) -> list[str]:
    """Apply every migrations/*.sql not yet recorded, in filename order. Each file is one savepoint."""
    applied: list[str] = []
    with (target or pool).connection() as conn, conn.transaction():
        # Several instances can start at once. The first takes this lock and migrates; the others wait,
        # then find nothing left to do. Transaction-scoped, so it also works through a pooler.
        conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute(
            "create table if not exists schema_migrations (name text primary key, applied_at timestamptz not null default now())"
        )
        done = {r["name"] for r in conn.execute("select name from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            applied.append(path.name)
    return applied


def seed_if_empty() -> bool:
    """Load seed.sql into a database with no users. One transaction, so a failure leaves nothing behind.
    The table lock stops two API instances booting together from both seeding."""
    with pool.connection() as conn, conn.transaction():
        conn.execute("lock table users in share row exclusive mode")
        if conn.execute("select exists (select 1 from users)").fetchone()["exists"]:
            return False
        conn.execute(SEED.read_text(encoding="utf-8"))
    return True
