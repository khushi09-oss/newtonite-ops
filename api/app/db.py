"""One connection pool, one migration runner, one seed runner. Raw SQL via psycopg 3."""

import os
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ops:ops@localhost:5432/ops")
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SEED = Path(__file__).resolve().parent.parent / "seed.sql"
MIGRATION_LOCK = 7_461_001  # any constant; only this app takes it

pool = ConnectionPool(
    DATABASE_URL, min_size=1, max_size=20, open=False, kwargs={"row_factory": dict_row}
)


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
