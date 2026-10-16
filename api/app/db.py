"""One connection pool, one migration runner, one seed runner. Raw SQL via psycopg 3."""

import logging
import os
import threading
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ops:ops@localhost:5432/ops")
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SEED = Path(__file__).resolve().parent.parent / "seed.sql"
MIGRATION_LOCK = 7_461_001  # any constant; only this app takes it

# prepare_threshold=None: no server-side prepared statements, which a transaction-mode pooler such as
# Neon's can route to the wrong server connection. The cost is negligible at this query volume.
pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=int(os.environ.get("DB_POOL_MAX", "20")),
    open=False,
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
