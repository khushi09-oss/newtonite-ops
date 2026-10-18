"""The inbox: four capped buckets, each one indexed query. What needs my attention, without a scan.
The three action buckets show the most urgent, then the most recent, first; stale shows the most neglected first."""

from fastapi import APIRouter, Depends

from app import db
from app.auth import current_user
from app.items import COLS, FROM, NAMES, inbox_views, team_scope

router = APIRouter()
BUCKET_CAP = 50


ORDER = {
    "assigned": "w.priority, w.updated_at desc",
    "approvals": "w.priority, w.updated_at desc",
    "unassigned_urgent": "w.priority, w.created_at desc",
    "stale": "w.last_activity_at",
}


def inbox_queries(conn, user: dict) -> dict[str, tuple[str, list]]:
    """Each question from inbox_views (shared with the items list's ?view=), its order, and a cap.
    Team ids are fetched first and passed as arrays, so every bucket is a plain index condition."""
    out = {}
    for name, (where, params) in inbox_views(user, *team_scope(conn, user)).items():
        sql = f"select {COLS},{NAMES} {FROM} where {where} order by {ORDER[name]} limit %s"
        out[name] = (sql, [*params, BUCKET_CAP + 1])
    return out


@router.get("/inbox")
def inbox(user: dict = Depends(current_user)):
    out = {}
    with db.pool.connection() as conn:
        for name, (sql, params) in inbox_queries(conn, user).items():
            rows = conn.execute(sql, params).fetchall()
            out[name] = {"items": rows[:BUCKET_CAP], "capped": len(rows) > BUCKET_CAP}
    return out
