"""Demo mode: a public site with shared, fake data. Off unless DEMO_MODE=true.

- Any verified Google account gets in without an invite, to an empty workspace of its own.
- The seeded demo accounts cannot be changed by admins, so one visitor cannot break them for all.
- A nightly call wipes the shared data and reseeds, behind CRON_SECRET. A visitor's private
  workspace is kept while anyone on it has signed in within the last week (D-41)."""

import logging
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

# A session lasts seven days (auth.MAX_AGE), so anyone using a workspace signs in within this.
KEEP_FOR = "7 days"
ACTIVE = (
    "exists (select 1 from sessions s where s.user_id = {user}"
    f" and s.created_at > now() - interval '{KEEP_FOR}')"
)
# What survives the reset, in the order it goes back in: each a copy into a temp table keep_<table>.
# Every person a kept row points at is kept, so putting the rows back can never break a reference.
KEEP = (
    (
        "teams",
        f"select * from teams t where t.private and exists (select 1 from team_members m"
        f" where m.team_id = t.id and {ACTIVE.format(user='m.user_id')})",
    ),
    ("work_items", "select * from work_items where team_id in (select id from keep_teams)"),
    (
        "work_item_events",
        "select * from work_item_events where item_id in (select id from keep_work_items)",
    ),
    (
        "users",
        f"select * from users u where (u.private and {ACTIVE.format(user='u.id')})"
        " or u.id in (select user_id from team_members where team_id in"
        " (select id from keep_teams))"
        " or u.id in (select actor_id from keep_work_item_events)"
        " or u.id in (select unnest(array[reporter_id, assignee_id, approval_requested_by])"
        " from keep_work_items)",
    ),
    ("team_members", "select * from team_members where team_id in (select id from keep_teams)"),
    (
        "watchers",
        "select * from watchers where item_id in (select id from keep_work_items)"
        " and user_id in (select id from keep_users)",
    ),
    (
        "notifications",
        "select * from notifications where event_id in"
        " (select id from keep_work_item_events)"
        " and user_id in (select id from keep_users)",
    ),
    (
        "sessions",
        "select * from sessions where user_id in (select id from keep_users)"
        " and revoked_at is null and expires_at > now()",
    ),
    (
        "admin_events",
        "select * from admin_events where actor_id in (select id from keep_users)"
        " and (target->>'team_id')::uuid in (select id from keep_teams)",
    ),
    # Jobs still to do for kept items, so a notification due at reset time is not lost.
    (
        "outbox",
        "select * from outbox where status <> 'done'"
        " and (payload->>'item_id')::uuid in (select id from keep_work_items)",
    ),
)
INSERT_ORDER = (
    "users",
    "teams",
    "team_members",
    "work_items",
    "work_item_events",
    "watchers",
    "notifications",
    "sessions",
    "admin_events",
    "outbox",
)

router = APIRouter()
log = logging.getLogger(__name__)


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


def reset(target: ConnectionPool | None = None) -> dict:
    """Wipe the data and load the seed again, keeping the private workspaces still in use, in one
    transaction. TRUNCATE does not fire the row-level append-only triggers; for the shared data,
    starting the history over each night is the point. Kept rows go back with their own ids and
    history. Identities are not restarted, so the seed's new ids cannot collide with kept ones."""
    with (target or db.pool).connection() as conn, conn.transaction():
        for table, query in KEEP:
            conn.execute(f"create temp table keep_{table} on commit drop as {query}")
        conn.execute(f"truncate {DATA_TABLES} cascade")
        conn.execute(db.SEED.read_text(encoding="utf-8"))
        for table in INSERT_ORDER:
            # Generated columns (an item's search vector) are computed again, not copied.
            cols = ", ".join(
                r["column_name"]
                for r in conn.execute(
                    "select column_name from information_schema.columns where table_schema ="
                    " current_schema and table_name = %s and is_generated = 'NEVER'"
                    " order by ordinal_position",
                    (table,),
                )
            )
            conn.execute(f"insert into {table} ({cols}) select {cols} from keep_{table}")
        return conn.execute(
            "select (select count(*) from keep_users) as users,"
            " (select count(*) from keep_teams) as teams,"
            " (select count(*) from keep_work_items) as items"
        ).fetchone()


@router.get("/internal/reset-demo")
def nightly_reset(request: Request):
    secret = os.environ.get("CRON_SECRET", "")
    if not DEMO_MODE or not secret or request.headers.get("authorization") != f"Bearer {secret}":
        raise HTTPException(404, "Not Found")
    kept = reset()
    log.info("demo reset, kept %s", kept)
    return {"reset": True, "kept": kept}
