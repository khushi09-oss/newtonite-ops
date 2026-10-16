"""One connection pool, one migration runner. Raw SQL via psycopg 3."""

import os
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ops:ops@localhost:5432/ops")
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=20, open=False, kwargs={"row_factory": dict_row})


def migrate() -> list[str]:
    """Apply every migrations/*.sql not yet recorded, in filename order. Each file is one transaction."""
    applied: list[str] = []
    with pool.connection() as conn:
        conn.execute("create table if not exists schema_migrations (name text primary key, applied_at timestamptz not null default now())")
        done = {r["name"] for r in conn.execute("select name from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            applied.append(path.name)
    return applied
