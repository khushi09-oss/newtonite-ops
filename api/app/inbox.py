"""The inbox: four capped buckets, each one indexed query. What needs my attention, without a scan.
The three action buckets show the most urgent, then the most recent, first; stale shows the most neglected first."""

from fastapi import APIRouter, Depends

from app import db
from app.auth import current_user
from app.items import COLS, FROM, NAMES

router = APIRouter()
BUCKET_CAP = 50
STALE_AFTER = "3 days"
CLOSED = (
    "('resolved', 'closed')"  # written exactly like the partial indexes so the planner can use them
)


def inbox_queries(conn, user: dict) -> dict[str, tuple[str, list]]:
    """Team ids are fetched first and passed as arrays, so every bucket is a plain index condition."""
    if user["is_admin"]:
        teams = [r["id"] for r in conn.execute("select id from teams where not private")]
        lead_of = teams
    else:
        rows = conn.execute(
            "select team_id, role from team_members where user_id = %s", (user["id"],)
        ).fetchall()
        teams = [r["team_id"] for r in rows]
        lead_of = [r["team_id"] for r in rows if r["role"] == "lead"]
    limit = BUCKET_CAP + 1
    return {
        "assigned": (
            (
                f"select {COLS},{NAMES} {FROM} where w.assignee_id = %s and w.status not in {CLOSED}"
                " order by w.priority, w.updated_at desc limit %s"
            ),
            [user["id"], limit],
        ),
        "approvals": (
            (
                f"select {COLS},{NAMES} {FROM} where w.team_id = any(%s) and w.status = 'awaiting_approval'"
                " and w.approval_requested_by <> %s order by w.priority, w.updated_at desc limit %s"
            ),
            [lead_of, user["id"], limit],
        ),
        "unassigned_urgent": (
            (
                f"select {COLS},{NAMES} {FROM} where w.team_id = any(%s) and w.status not in {CLOSED}"
                " and w.assignee_id is null and w.priority <= 1"
                " order by w.priority, w.created_at desc limit %s"
            ),
            [teams, limit],
        ),
        "stale": (
            (
                f"select {COLS},{NAMES} {FROM} where w.team_id = any(%s) and w.status not in {CLOSED}"
                f" and w.last_activity_at < now() - interval '{STALE_AFTER}'"
                " order by w.last_activity_at limit %s"
            ),
            [teams, limit],
        ),
    }


@router.get("/inbox")
def inbox(user: dict = Depends(current_user)):
    out = {}
    with db.pool.connection() as conn:
        for name, (sql, params) in inbox_queries(conn, user).items():
            rows = conn.execute(sql, params).fetchall()
            out[name] = {"items": rows[:BUCKET_CAP], "capped": len(rows) > BUCKET_CAP}
    return out
