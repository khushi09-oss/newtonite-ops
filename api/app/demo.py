"""Demo mode: a public site with shared, fake data. Off unless DEMO_MODE=true.

- Any verified Google account gets in without an invite, to an empty workspace of its own.
- The seeded demo accounts cannot be changed by admins, so one visitor cannot break them for all.
- A nightly call wipes everything and reseeds, behind CRON_SECRET."""

import os

from fastapi import APIRouter, HTTPException, Request
from psycopg_pool import ConnectionPool

from app import auth, db

DEMO_MODE = auth.flag("DEMO_MODE")
SEEDED_DOMAIN = "@newtonite.dev"
DATA_TABLES = (
    "notifications, watchers, outbox, idempotency_keys, rate_limits, sessions, admin_events,"
    " work_item_events, work_items, team_members, teams, users"
)

router = APIRouter()


def is_demo_account(email: str) -> bool:
    return email.lower().endswith(SEEDED_DOMAIN)


def guard(conn, user_id) -> None:
    """In demo mode, refuse admin changes to a seeded demo account."""
    if not DEMO_MODE:
        return
    row = conn.execute("select email from users where id = %s", (user_id,)).fetchone()
    if row and is_demo_account(row["email"]):
        raise HTTPException(
            403,
            "Demo accounts are shared and cannot be changed. Invite your own person to try this.",
        )


def add_visitor(conn, email: str, name: str, sub: str) -> dict:
    """Create a Google visitor with no teams: they start their own workspace (app/teams.py). The demo
    people explore the shared data. Called only in demo mode."""
    return conn.execute(
        "insert into users (email, name, google_sub, private) values (%s, %s, %s, true)"
        " returning id, email, name, is_admin, google_sub, deactivated_at",
        (email, name, sub),
    ).fetchone()


def reset(target: ConnectionPool | None = None) -> None:
    """Wipe every data table and load the seed again, in one transaction. TRUNCATE does not fire the
    row-level append-only triggers; in demo mode, starting the history over each night is the point."""
    with (target or db.pool).connection() as conn, conn.transaction():
        conn.execute(f"truncate {DATA_TABLES} restart identity cascade")
        conn.execute(db.SEED.read_text(encoding="utf-8"))


@router.get("/internal/reset-demo")
def nightly_reset(request: Request):
    secret = os.environ.get("CRON_SECRET", "")
    if not DEMO_MODE or not secret or request.headers.get("authorization") != f"Bearer {secret}":
        raise HTTPException(404, "Not Found")
    reset()
    return {"reset": True}
