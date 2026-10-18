"""The dashboard: what needs me, how my teams' open work is spread, how it moved over two weeks, and
what just happened. Every number is one query on an index, scoped to the caller's teams."""

from fastapi import APIRouter, Depends

from app import db
from app.auth import current_user
from app.items import OPEN

router = APIRouter()
TREND_DAYS = 14
RECENT = 15
CLOSED = "('resolved', 'closed')"  # written as the partial indexes define it


def my_teams(conn, user: dict) -> tuple[list, list]:
    if user["is_admin"]:
        teams = [r["id"] for r in conn.execute("select id from teams where not private")]
        return teams, teams
    rows = conn.execute(
        "select team_id, role from team_members where user_id = %s", (user["id"],)
    ).fetchall()
    return [r["team_id"] for r in rows], [r["team_id"] for r in rows if r["role"] == "lead"]


def queries(user: dict, teams: list, lead_of: list) -> dict[str, tuple[str, dict]]:
    """Named so the tests can EXPLAIN each one."""
    p = {
        "me": user["id"],
        "teams": teams,
        "lead_of": lead_of,
        "open": list(OPEN),
        "days": TREND_DAYS,
        "recent": RECENT,
    }
    return {
        "counts": (
            (
                "select"
                f" (select count(*) from work_items where assignee_id = %(me)s and status not in {CLOSED}) as assigned,"
                " (select count(*) from work_items where team_id = any(%(lead_of)s)"
                "   and status = 'awaiting_approval' and approval_requested_by <> %(me)s) as approvals,"
                " (select count(*) from work_items where team_id = any(%(teams)s)"
                f"   and status not in {CLOSED} and assignee_id is null and priority <= 1) as urgent,"
                " (select count(*) from work_items where team_id = any(%(teams)s)"
                f"   and status not in {CLOSED} and due_at is not null and due_at < now()) as overdue,"
                " (select count(*) from work_items where team_id = any(%(teams)s)"
                f"   and status not in {CLOSED} and last_activity_at < now() - interval '3 days') as stale"
            ),
            p,
        ),
        "by_team": (
            (
                "select t.id as team_id, t.name as team, w.status, count(*) as n"
                " from work_items w join teams t on t.id = w.team_id"
                " where w.team_id = any(%(teams)s) and w.status = any(%(open)s)"
                " group by t.id, t.name, w.status order by t.name"
            ),
            p,
        ),
        "created": (
            (
                "select date_trunc('day', created_at) as day, count(*) as n from work_items"
                " where team_id = any(%(teams)s) and created_at >= date_trunc('day', now()) - make_interval(days => %(days)s - 1)"
                " group by 1"
            ),
            p,
        ),
        "resolved": (
            (
                "select date_trunc('day', e.created_at) as day, count(*) as n"
                " from work_item_events e join work_items w on w.id = e.item_id"
                " where e.created_at >= date_trunc('day', now()) - make_interval(days => %(days)s - 1)"
                " and e.kind = 'status' and e.after->>'status' = 'resolved' and w.team_id = any(%(teams)s)"
                " group by 1"
            ),
            p,
        ),
        "recent": (
            (
                "select e.id, e.kind, e.before, e.after, e.note, e.created_at, e.actor_id,"
                " u.name as actor_name, w.id as item_id, w.number, w.title, t.name as team, t.key as team_key,"
                " t.color as team_color"
                " from work_item_events e join work_items w on w.id = e.item_id"
                " join users u on u.id = e.actor_id join teams t on t.id = w.team_id"
                " where w.team_id = any(%(teams)s) order by e.created_at desc limit %(recent)s"
            ),
            p,
        ),
    }


@router.get("/dashboard")
def dashboard(user: dict = Depends(current_user)):
    with db.pool.connection() as conn:
        teams, lead_of = my_teams(conn, user)
        q = queries(user, teams, lead_of)
        counts = conn.execute(*q["counts"]).fetchone()
        by_team: dict = {}
        for r in conn.execute(*q["by_team"]):
            entry = by_team.setdefault(
                r["team_id"], {"team_id": r["team_id"], "team": r["team"], "by_status": {}}
            )
            entry["by_status"][r["status"]] = r["n"]
        created = {r["day"].date().isoformat(): r["n"] for r in conn.execute(*q["created"])}
        resolved = {r["day"].date().isoformat(): r["n"] for r in conn.execute(*q["resolved"])}
        days = [
            r["day"].isoformat()
            for r in conn.execute(
                "select (date_trunc('day', now()) - make_interval(days => g))::date as day"
                " from generate_series(%s - 1, 0, -1) as g",
                (TREND_DAYS,),
            )
        ]
        recent = conn.execute(*q["recent"]).fetchall()
        # Admins are the ones who can retry a failed job, so their dashboard and sidebar say when
        # one is waiting (D-21). Everyone else gets null. One count on the (status, id) index.
        failed_jobs = (
            conn.execute("select count(*) as n from outbox where status = 'dead'").fetchone()["n"]
            if user["is_admin"]
            else None
        )
    return {
        "failed_jobs": failed_jobs,
        "counts": counts,
        "by_team": sorted(by_team.values(), key=lambda t: t["team"]),
        "trend": [
            {"day": d, "created": created.get(d, 0), "resolved": resolved.get(d, 0)} for d in days
        ],
        "recent": recent,
    }
