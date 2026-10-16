"""Work items: the single write path, authorization as a query predicate, the transition table, and the
list, get and create routes."""

import base64
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import db
from app.auth import current_user

router = APIRouter()

OPEN = ("new", "triaged", "in_progress", "awaiting_approval", "approved")
LIST_CAP = 100

# Columns the API returns. The tsvector stays in the database.
COLS = ("w.id, w.team_id, w.kind, w.title, w.description, w.status, w.priority, w.reporter_id, w.assignee_id,"
        " w.approval_requested_by, w.due_at, w.version, w.created_at, w.updated_at, w.last_activity_at")
NAMES = " t.name as team_name, r.name as reporter_name, a.name as assignee_name"
FROM = (" from work_items w join teams t on t.id = w.team_id join users r on r.id = w.reporter_id"
        " left join users a on a.id = w.assignee_id")

# (from, to): who may. Anything not listed is refused. See docs/BUILD_PLAN.md.
TRANSITIONS = {
    ("new", "triaged"): "member",
    ("triaged", "in_progress"): "assignee",
    ("in_progress", "awaiting_approval"): "assignee_or_lead",
    ("awaiting_approval", "approved"): "lead_not_requester",
    ("awaiting_approval", "triaged"): "lead",
    ("approved", "resolved"): "assignee_or_lead",
    ("resolved", "closed"): "lead",
    ("closed", "triaged"): "lead",
}


def _holds(rule: str, user: dict, role: str, item: dict) -> bool:
    is_lead = role in ("lead", "admin")
    is_assignee = item["assignee_id"] == user["id"]
    return {
        "member": role != "viewer",
        "assignee": is_assignee,
        "assignee_or_lead": is_assignee or is_lead,
        "lead": is_lead,
        "lead_not_requester": is_lead and item["approval_requested_by"] != user["id"],
    }[rule]


def allowed_actions(user: dict, role: str, item: dict) -> list[str]:
    """Everything this user may do to this item right now. Routes check membership in this list;
    the UI draws its buttons from it. One function, so the two can never disagree."""
    actions = ["comment", "watch"]
    is_lead = role in ("lead", "admin")
    is_assignee = item["assignee_id"] == user["id"]
    if role != "viewer" and item["status"] in OPEN:
        if item["assignee_id"] is None:
            actions.append("claim")
        if item["reporter_id"] == user["id"] or is_lead:
            actions.append("edit")
        if is_assignee or is_lead:
            actions += ["priority", "assign"]
            if item["assignee_id"] is not None:
                actions.append("release")
    for (frm, to), rule in TRANSITIONS.items():
        if frm == item["status"] and _holds(rule, user, role, item):
            actions.append(f"transition:{to}")
    return actions


class Conflict(Exception):
    """The caller's version is stale. Carries the current row so the response can show what changed."""

    def __init__(self, item: dict):
        self.item = item


def _jsonb(d: dict) -> Jsonb:
    return Jsonb(d, dumps=lambda o: json.dumps(o, default=str))


def apply_change(conn, actor: dict, kind: str, changes: dict, item_id=None, expected_version=None,
                 note: str | None = None, guard: str = "") -> dict:
    """The only function that writes work_items. With item_id it updates the row where version matches
    (plus any extra guard, e.g. "and assignee_id is null" for a claim); without it, inserts. Either way it
    appends exactly one work_item_events row in the caller's transaction. Zero rows updated raises Conflict."""
    if item_id is None:
        cols = ", ".join(changes)
        row = conn.execute(
            f"insert into work_items ({cols}) values ({', '.join(['%s'] * len(changes))}) returning *",
            list(changes.values()),
        ).fetchone()
        before = None
    else:
        before = conn.execute("select * from work_items where id = %s", (item_id,)).fetchone()
        if before is None:
            raise HTTPException(404, "item not found")
        row = None
        if before["version"] == expected_version:
            sets = ", ".join(f"{k} = %s" for k in changes)
            row = conn.execute(
                f"update work_items set {sets}, version = version + 1, updated_at = now(),"
                f" last_activity_at = now() where id = %s and version = %s {guard} returning *",
                [*changes.values(), item_id, expected_version],
            ).fetchone()
        if row is None:
            raise Conflict(before)
    conn.execute(
        "insert into work_item_events (item_id, seq, actor_id, kind, before, after, note)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (row["id"], row["version"], actor["id"], kind,
         _jsonb({k: before[k] for k in changes}) if before else None,
         _jsonb({k: row[k] for k in changes}), note),
    )
    return row


def require_access(conn, user: dict, item_id: UUID) -> dict:
    """The item plus the caller's role on its team. Non-members get 404 so ids cannot be probed."""
    row = conn.execute(
        f"select {COLS},{NAMES}, case when %(admin)s then 'admin' else m.role end as role {FROM}"
        " left join team_members m on m.team_id = w.team_id and m.user_id = %(me)s"
        " where w.id = %(id)s and (%(admin)s or m.user_id is not null)",
        {"admin": user["is_admin"], "me": user["id"], "id": item_id},
    ).fetchone()
    if row is None:
        raise HTTPException(404, "item not found")
    return row


def recent_events(conn, item_id, limit: int = 20) -> list[dict]:
    return conn.execute(
        "select e.id, e.seq, e.actor_id, u.name as actor_name, e.kind, e.before, e.after, e.note, e.created_at"
        " from work_item_events e join users u on u.id = e.actor_id"
        " where e.item_id = %s order by e.seq desc limit %s",
        (item_id, limit),
    ).fetchall()


def list_query(user: dict, team=None, status=None, priority=None, assignee=None, q=None,
               cursor=None, limit: int = 50) -> tuple[str, list]:
    """Built per request so each filter is a real constant to the planner and the composite index is used."""
    where, params = [], []
    if not user["is_admin"]:
        where.append("exists (select 1 from team_members m where m.team_id = w.team_id and m.user_id = %s)")
        params.append(user["id"])
    for col, val in (("w.team_id", team), ("w.status", status), ("w.priority", priority), ("w.assignee_id", assignee)):
        if val is not None:
            where.append(f"{col} = %s")
            params.append(val)
    if q:
        where.append("w.search @@ websearch_to_tsquery('english', %s)")
        params.append(q)
    if cursor:
        try:
            at, _, cid = base64.urlsafe_b64decode(cursor).decode().partition(" ")
            params += [datetime.fromisoformat(at), UUID(cid)]
        except ValueError:
            raise HTTPException(422, "bad cursor")
        where.append("(w.updated_at, w.id) < (%s, %s)")
    sql = (f"select {COLS},{NAMES} {FROM} where {' and '.join(where) or 'true'}"
           f" order by w.updated_at desc, w.id desc limit {int(limit) + 1}")
    return sql, params


@router.get("/items")
def list_items(user: dict = Depends(current_user), team: UUID | None = None, status: str | None = None,
               priority: int | None = Query(None, ge=0, le=3), assignee: UUID | None = None,
               q: str | None = None, cursor: str | None = None, limit: int = Query(50, ge=1, le=LIST_CAP)):
    sql, params = list_query(user, team, status, priority, assignee, q, cursor, limit)
    with db.pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    capped = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if capped:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(f"{last['updated_at'].isoformat()} {last['id']}".encode()).decode()
    return {"items": rows, "capped": capped, "next_cursor": next_cursor}


@router.get("/items/{item_id}")
def get_item(item_id: UUID, user: dict = Depends(current_user)):
    with db.pool.connection() as conn:
        item = require_access(conn, user, item_id)
        events = recent_events(conn, item_id)
    return {**item, "allowed_actions": allowed_actions(user, item["role"], item), "events": events}


class CreateItem(BaseModel):
    team_id: UUID
    kind: Literal["incident", "customer_issue", "payment", "compliance", "task"]
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: int = Field(2, ge=0, le=3)
    due_at: datetime | None = None


@router.post("/items", status_code=201)
def create_item(body: CreateItem, user: dict = Depends(current_user)):
    with db.pool.connection() as conn, conn.transaction():
        membership = conn.execute(
            "select role from team_members where team_id = %s and user_id = %s", (body.team_id, user["id"])
        ).fetchone()
        if membership is None and not user["is_admin"]:
            raise HTTPException(404, "team not found")
        if membership is not None and membership["role"] == "viewer" and not user["is_admin"]:
            raise HTTPException(403, "viewers cannot create items")
        row = apply_change(conn, user, "created", {**body.model_dump(), "reporter_id": user["id"]})
        item = require_access(conn, user, row["id"])
    return {**item, "allowed_actions": allowed_actions(user, item["role"], item), "events": []}
