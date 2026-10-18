"""Work items: the single write path, authorization as a query predicate, the transition table, and every
item route. Each mutation checks the action against allowed_actions() and writes through apply_change()."""

import base64
import json
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app import db
from app.auth import current_user
from app.idempotency import RetryKey, idempotent

router = APIRouter()

STATUSES = ("new", "triaged", "in_progress", "awaiting_approval", "approved", "resolved", "closed")
OPEN = STATUSES[:5]
LIST_CAP = 100

# Columns the API returns. The tsvector stays in the database.
COLS = (
    "w.id, w.team_id, w.kind, w.title, w.description, w.status, w.priority, w.reporter_id, w.assignee_id,"
    " w.approval_requested_by, w.due_at, w.version, w.created_at, w.updated_at, w.last_activity_at,"
    " w.number"
)
NAMES = (
    " t.name as team_name, t.key as team_key, t.color as team_color,"
    " r.name as reporter_name, a.name as assignee_name"
)
# Admins skip the membership check, except on private teams: on the public demo the admin account
# is shared, and a visitor's own workspace must stay theirs. See migration 008.
ADMIN_SEES = "(%(admin)s and not t.private)"
FROM = (
    " from work_items w join teams t on t.id = w.team_id join users r on r.id = w.reporter_id"
    " left join users a on a.id = w.assignee_id"
)

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
NOTE_REQUIRED = {("awaiting_approval", "triaged")}
# A claim takes ownership of unowned open work. On new or triaged work it also starts it: claiming
# means "I am doing this now". Anywhere else it only takes ownership, so an item waiting for approval
# stays in the approval queue and its request stays on record. Items report this as `claim_to`.
CLAIM_STARTS = {"new": "in_progress", "triaged": "in_progress"}


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


def apply_change(
    conn,
    actor: dict,
    kind: str,
    changes: dict,
    item_id=None,
    expected_version=None,
    note: str | None = None,
    guard: str = "",
) -> dict:
    """The only function that writes work_items. With item_id it updates the row where version matches
    (plus any extra guard, e.g. "and assignee_id is null" for a claim); without it, inserts. Either way it
    appends exactly one work_item_events row in the caller's transaction. Zero rows updated raises Conflict
    with the row as it is now. expected_version None means "whatever it is": the row is locked and bumped,
    which is how comments add history without the client holding a version."""
    if item_id is None:
        # The team row lock makes keys gapless and in order; creates on one team queue behind it.
        team = conn.execute(
            "update teams set next_number = next_number + 1 where id = %s returning next_number",
            (changes["team_id"],),
        ).fetchone()
        if team is None:
            raise HTTPException(404, "team not found")
        changes = {**changes, "number": team["next_number"]}
        cols = ", ".join(changes)
        row = conn.execute(
            f"insert into work_items ({cols}) values ({', '.join(['%s'] * len(changes))}) returning *",
            list(changes.values()),
        ).fetchone()
        before = None
    else:
        lock = " for update" if expected_version is None else ""
        before = conn.execute(
            f"select * from work_items where id = %s{lock}", (item_id,)
        ).fetchone()
        if before is None:
            raise HTTPException(404, "item not found")
        if expected_version is None:
            expected_version = before["version"]
        row = None
        if before["version"] == expected_version:
            sets = "".join(f"{k} = %s, " for k in changes)
            row = conn.execute(
                f"update work_items set {sets}version = version + 1, updated_at = now(),"
                f" last_activity_at = now() where id = %s and version = %s {guard} returning *",
                [*changes.values(), item_id, expected_version],
            ).fetchone()
        if row is None:
            raise Conflict(
                conn.execute("select * from work_items where id = %s", (item_id,)).fetchone()
            )
    event_id = conn.execute(
        "insert into work_item_events (item_id, seq, actor_id, kind, before, after, note)"
        " values (%s, %s, %s, %s, %s, %s, %s) returning id",
        (
            row["id"],
            row["version"],
            actor["id"],
            kind,
            _jsonb({k: before[k] for k in changes}) if before else None,
            _jsonb({k: row[k] for k in changes}),
            note,
        ),
    ).fetchone()["id"]
    # Same transaction as the row and the event: the notification work is enqueued or nothing is (D-04).
    conn.execute(
        "insert into outbox (kind, payload) values ('item_event', %s)",
        (
            _jsonb(
                {"item_id": row["id"], "event_id": event_id, "actor_id": actor["id"], "kind": kind}
            ),
        ),
    )
    return row


def visible_teams(conn, user: dict, team_id=None) -> list[dict]:
    """Every team the caller can see, their role on it ("admin" when they see it only as an admin),
    and whether they may create items there. The one rule behind creating an item and behind every
    team list the screens show, so a picker can never offer a team the server would refuse."""
    cond = " and t.id = %(team)s" if team_id is not None else ""
    return conn.execute(
        "select t.id, t.name, t.key, t.color, t.private, coalesce(m.role, 'admin') as role,"
        " (%(admin)s or coalesce(m.role in ('member', 'lead'), false)) as can_create"
        " from teams t left join team_members m on m.team_id = t.id and m.user_id = %(me)s"
        f" where (m.user_id is not null or {ADMIN_SEES}){cond}"
        " order by m.user_id is null, t.name",
        {"admin": user["is_admin"], "me": user["id"], "team": team_id},
    ).fetchall()


TEAM_COLOURS = 8  # palette slots; the screens hold the eight colours (ui.jsx TEAM_SWATCHES)


def next_team_colour(conn, user: dict) -> int:
    """The palette slot least used among the teams `user` can see, lowest first on a tie. A new team
    takes it, so up to eight teams always look different in anyone's sidebar and lists."""
    used = [t["color"] for t in visible_teams(conn, user)]
    return min(range(TEAM_COLOURS), key=lambda c: (used.count(c), c))


def team_scope(conn, user: dict) -> tuple[list, list]:
    """Ids of every team the caller can see, and of those where they approve: the ones they lead, and
    for an admin every public team, as `allowed_actions` decides through `require_access`."""
    seen = visible_teams(conn, user)
    approve = [
        t["id"] for t in seen if t["role"] == "lead" or (user["is_admin"] and not t["private"])
    ]
    return [t["id"] for t in seen], approve


# Written exactly as the partial indexes define open work, so the planner can use them.
UNFINISHED = "('resolved', 'closed')"
STALE_AFTER = "3 days"
# Open and past its due date; the list's Overdue filter and the dashboard's tile both use it.
OVERDUE = f"w.status not in {UNFINISHED} and w.due_at < now()"
INBOX_VIEWS = ("assigned", "approvals", "unassigned_urgent", "stale")


def inbox_views(user: dict, teams: list, approve_on: list) -> dict[str, tuple[str, list]]:
    """The inbox's four questions as WHERE terms, defined once. The inbox shows the first 50 of each;
    the items list shows all of one with ?view=, so the two can never disagree."""
    return {
        "assigned": (f"w.assignee_id = %s and w.status not in {UNFINISHED}", [user["id"]]),
        "approvals": (
            (
                "w.team_id = any(%s) and w.status = 'awaiting_approval'"
                " and w.approval_requested_by <> %s"
            ),
            [approve_on, user["id"]],
        ),
        "unassigned_urgent": (
            (
                f"w.team_id = any(%s) and w.status not in {UNFINISHED}"
                " and w.assignee_id is null and w.priority <= 1"
            ),
            [teams],
        ),
        "stale": (
            (
                f"w.team_id = any(%s) and w.status not in {UNFINISHED}"
                f" and w.last_activity_at < now() - interval '{STALE_AFTER}'"
            ),
            [teams],
        ),
    }


def inbox_view(conn, user: dict, name: str) -> tuple[str, list]:
    return inbox_views(user, *team_scope(conn, user))[name]


def load_item(conn, item_id) -> dict | None:
    """The row with names, no access check. For responses after access has already been verified."""
    return conn.execute(f"select {COLS},{NAMES} {FROM} where w.id = %s", (item_id,)).fetchone()


def require_access(conn, user: dict, item_id: UUID) -> dict:
    """The item plus the caller's role on its team. Non-members get 404 so ids cannot be probed."""
    row = conn.execute(
        f"select {COLS},{NAMES}, case when {ADMIN_SEES} then 'admin' else m.role end as role {FROM}"
        " left join team_members m on m.team_id = w.team_id and m.user_id = %(me)s"
        f" where w.id = %(id)s and ({ADMIN_SEES} or m.user_id is not null)",
        {"admin": user["is_admin"], "me": user["id"], "id": item_id},
    ).fetchone()
    if row is None:
        raise HTTPException(404, "item not found")
    return row


def _may(conn, user: dict, item_id: UUID, action: str, version: int | None = None) -> dict:
    """require_access, then the version check, then the permission check. A stale version is answered
    409 before anything else, because what the caller may do depends on a row they have not seen yet.
    Transitions answer 422 with the allowed targets; everything else answers 403."""
    item = require_access(conn, user, item_id)
    if version is not None and item["version"] != version:
        raise Conflict(item)
    allowed = allowed_actions(user, item["role"], item)
    if action not in allowed:
        if action.startswith("transition:"):
            targets = [a.split(":", 1)[1] for a in allowed if a.startswith("transition:")]
            raise HTTPException(
                422,
                {
                    "message": f"cannot move from {item['status']} to {action[11:]}",
                    "allowed": targets,
                },
            )
        raise HTTPException(403, {"message": f"not allowed to {action}", "allowed": allowed})
    return item


def _respond(conn, user: dict, item_id) -> dict:
    item = require_access(conn, user, item_id)
    watching = conn.execute(
        "select 1 from watchers where item_id = %s and user_id = %s", (item_id, user["id"])
    ).fetchone()
    return {
        **item,
        "claim_to": CLAIM_STARTS.get(item["status"]),
        "watching": watching is not None,
        "allowed_actions": allowed_actions(user, item["role"], item),
        "events": recent_events(conn, item_id),
    }


def recent_events(conn, item_id, limit: int = 20, before_seq: int | None = None) -> list[dict]:
    cond = " and e.seq < %s" if before_seq is not None else ""
    return conn.execute(
        "select e.id, e.seq, e.actor_id, u.name as actor_name, e.kind, e.before, e.after, e.note, e.created_at"
        " from work_item_events e join users u on u.id = e.actor_id"
        f" where e.item_id = %s{cond} order by e.seq desc limit %s",
        [item_id, *([before_seq] if before_seq is not None else []), limit],
    ).fetchall()


def filters(
    user: dict, team, status, priority, assignee, q, overdue, view=None
) -> tuple[list, list]:
    """The WHERE terms the list and its counts share: visibility first, then each filter given."""
    where, params = [], []
    # The same rule as visible_teams: your own teams, and for an admin every public one.
    where.append(
        "((%s and not t.private) or exists"
        " (select 1 from team_members m where m.team_id = w.team_id and m.user_id = %s))"
    )
    params += [user["is_admin"], user["id"]]
    if view is not None:  # one of the inbox's questions, (WHERE term, params) from inbox_view
        where.append(view[0])
        params += view[1]
    for col, val in (
        ("w.team_id", team),
        ("w.status", status),
        ("w.priority", priority),
        ("w.assignee_id", assignee),
    ):
        if val is not None:
            where.append(f"{col} = %s")
            params.append(val)
    if overdue:  # matches the partial index on (team_id, due_at) for open work
        where.append(OVERDUE)
    key = re.fullmatch(r"\s*(?:([a-z]{2,6})-)?(\d+)\s*", q or "", re.IGNORECASE)
    # "RSK-1204" finds that item on the (team_id, number) index; "1204" finds it on every team.
    if key:
        where.append("w.number = %s")
        params.append(int(key.group(2)))
        if key.group(1):
            where.append("t.key = %s")
            params.append(key.group(1).upper())
    elif q:
        # Whole words and their stems through the full-text index. From four letters up, also a title
        # word that closely matches what was typed ("refnd" finds "Refund") through the trigram index
        # (migration 012); shorter than that, near matches would be nearly everything.
        if len(q.strip()) >= 4:
            where.append("(w.search @@ websearch_to_tsquery('english', %s) or %s <%% w.title)")
            params += [q, q]
        else:
            where.append("w.search @@ websearch_to_tsquery('english', %s)")
            params.append(q)
    return where, params


# How close a typed word must be to a title word. The default, 0.6, missed common typos: "chargbak"
# scores 0.56 against "Chargeback", "refnd" 0.50 against "Refund"; unrelated words score 0.2 or less.
SIMILARITY = 0.5


def open_search(conn) -> None:
    """Set the typo threshold for this transaction only. Not on the database or the session: a pooled
    connection opened before such a change, or a transaction-mode pooler like Neon's, would miss it."""
    conn.execute(f"set local pg_trgm.word_similarity_threshold = {SIMILARITY}")


def list_query(
    user: dict,
    team=None,
    status=None,
    priority=None,
    assignee=None,
    q=None,
    cursor=None,
    limit: int = 50,
    overdue: bool = False,
    view=None,
) -> tuple[str, list]:
    """Built per request so each filter is a real constant to the planner and the composite index is used."""
    where, params = filters(user, team, status, priority, assignee, q, overdue, view)
    if cursor:
        try:
            at, _, cid = base64.urlsafe_b64decode(cursor).decode().partition(" ")
            params += [datetime.fromisoformat(at), UUID(cid)]
        except ValueError:
            raise HTTPException(422, "bad cursor")
        where.append("(w.updated_at, w.id) < (%s, %s)")
    sql = (
        f"select {COLS},{NAMES} {FROM} where {' and '.join(where) or 'true'}"
        f" order by w.updated_at desc, w.id desc limit {int(limit) + 1}"
    )
    return sql, params


@router.get("/items")
def list_items(
    user: dict = Depends(current_user),
    team: UUID | None = None,
    status: str | None = None,
    priority: int | None = Query(None, ge=0, le=3),
    assignee: UUID | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=LIST_CAP),
    overdue: bool = False,
    view: Literal[INBOX_VIEWS] | None = None,
):
    with db.pool.connection() as conn, conn.transaction():
        open_search(conn)
        scope = inbox_view(conn, user, view) if view else None
        sql, params = list_query(
            user, team, status, priority, assignee, q, cursor, limit, overdue, scope
        )
        rows = conn.execute(sql, params).fetchall()
    capped = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if capped:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(
            f"{last['updated_at'].isoformat()} {last['id']}".encode()
        ).decode()
    return {"items": rows, "capped": capped, "next_cursor": next_cursor}


@router.get("/items/counts")
def count_items(
    user: dict = Depends(current_user),
    team: UUID | None = None,
    priority: int | None = Query(None, ge=0, le=3),
    assignee: UUID | None = None,
    q: str | None = None,
    overdue: bool = False,
    view: Literal[INBOX_VIEWS] | None = None,
):
    """How many items match the list's filters, per status, for the headers of a list grouped by status."""
    with db.pool.connection() as conn, conn.transaction():
        open_search(conn)
        scope = inbox_view(conn, user, view) if view else None
        where, params = filters(user, team, None, priority, assignee, q, overdue, scope)
        rows = conn.execute(
            "select w.status, count(*) as n from work_items w join teams t on t.id = w.team_id"
            f" where {' and '.join(where) or 'true'} group by w.status",
            params,
        ).fetchall()
    return {"counts": {s: 0 for s in STATUSES} | {r["status"]: r["n"] for r in rows}}


@router.get("/items/{item_id}")
def get_item(item_id: UUID, user: dict = Depends(current_user)):
    with db.pool.connection() as conn:
        return _respond(conn, user, item_id)


@router.get("/items/{item_id}/events")
def list_events(
    item_id: UUID,
    user: dict = Depends(current_user),
    before_seq: int | None = None,
    limit: int = Query(50, ge=1, le=LIST_CAP),
):
    with db.pool.connection() as conn:
        require_access(conn, user, item_id)
        rows = recent_events(conn, item_id, limit + 1, before_seq)
    return {"events": rows[:limit], "capped": len(rows) > limit}


class CreateItem(BaseModel):
    team_id: UUID
    kind: Literal["incident", "customer_issue", "payment", "compliance", "task"]
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: int = Field(2, ge=0, le=3)
    due_at: datetime | None = None


@router.post("/items", status_code=201)
def create_item(
    body: CreateItem,
    user: dict = Depends(current_user),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def run():
        team = visible_teams(conn, user, body.team_id)
        if not team:
            raise HTTPException(404, "team not found")
        if not team[0]["can_create"]:
            raise HTTPException(403, "viewers cannot create items")
        row = apply_change(conn, user, "created", {**body.model_dump(), "reporter_id": user["id"]})
        return _respond(conn, user, row["id"])

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, user, key, "create", body, run, status=201, required=True)


class Versioned(BaseModel):
    version: int


class Patch(Versioned):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    priority: int | None = Field(None, ge=0, le=3)
    due_at: datetime | None = None

    # Leave a field out to keep it. Only the due date can be cleared, so an explicit null anywhere
    # else is refused here with 422, instead of reaching a NOT NULL column and answering 500.
    @field_validator("title", "description", "priority")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("cannot be null; leave the field out to keep it")
        return value


@router.patch("/items/{item_id}")
def patch_item(
    item_id: UUID, body: Patch, user: dict = Depends(current_user), key: RetryKey = None
):
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if not changes:
        raise HTTPException(422, "nothing to change")

    def run():
        if "priority" in changes:
            _may(conn, user, item_id, "priority", body.version)
        if set(changes) - {"priority"}:
            _may(conn, user, item_id, "edit", body.version)
        apply_change(conn, user, "edited", changes, item_id, body.version)
        return _respond(conn, user, item_id)

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, user, key, f"edit:{item_id}", body, run)


@router.post("/items/{item_id}/claim")
def claim_item(
    item_id: UUID, body: Versioned, user: dict = Depends(current_user), key: RetryKey = None
):
    def run():
        item = _may(conn, user, item_id, "claim", body.version)
        changes = {"assignee_id": user["id"]}
        if item["status"] in CLAIM_STARTS:
            changes["status"] = CLAIM_STARTS[item["status"]]
        # The guard makes the claim atomic even if two callers hold the same version: one row, one winner.
        apply_change(
            conn, user, "claimed", changes, item_id, body.version, guard="and assignee_id is null"
        )
        return _respond(conn, user, item_id)

    # A retried claim whose first answer was lost replays that answer, instead of a 409 naming you.
    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, user, key, f"claim:{item_id}", body, run)


@router.post("/items/{item_id}/release")
def release_item(
    item_id: UUID, body: Versioned, user: dict = Depends(current_user), key: RetryKey = None
):
    def run():
        _may(conn, user, item_id, "release", body.version)
        apply_change(conn, user, "released", {"assignee_id": None}, item_id, body.version)
        return _respond(conn, user, item_id)

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, user, key, f"release:{item_id}", body, run)


class Assign(Versioned):
    assignee_id: UUID


@router.post("/items/{item_id}/assign")
def assign_item(
    item_id: UUID, body: Assign, user: dict = Depends(current_user), key: RetryKey = None
):
    def run():
        item = _may(conn, user, item_id, "assign", body.version)
        ok = conn.execute(
            "select 1 from team_members m join users u on u.id = m.user_id"
            " where m.team_id = %s and m.user_id = %s and (m.role <> 'viewer' or u.is_admin)"
            " and u.deactivated_at is null",
            (item["team_id"], body.assignee_id),
        ).fetchone()
        if ok is None:
            raise HTTPException(422, "assignee must be a member or lead of the item's team")
        apply_change(
            conn, user, "assigned", {"assignee_id": body.assignee_id}, item_id, body.version
        )
        return _respond(conn, user, item_id)

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, user, key, f"assign:{item_id}", body, run)


class Transition(Versioned):
    to: Literal[STATUSES]
    note: str | None = None


@router.post("/items/{item_id}/transition")
def transition_item(
    item_id: UUID,
    body: Transition,
    user: dict = Depends(current_user),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def run():
        item = _may(conn, user, item_id, f"transition:{body.to}", body.version)
        if (item["status"], body.to) in NOTE_REQUIRED and not (body.note or "").strip():
            raise HTTPException(422, "a note is required for this transition")
        changes = {"status": body.to}
        if body.to == "awaiting_approval":
            changes["approval_requested_by"] = user["id"]
        elif item["status"] == "awaiting_approval":
            changes["approval_requested_by"] = None
        apply_change(conn, user, "status", changes, item_id, body.version, note=body.note)
        return _respond(conn, user, item_id)

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, user, key, f"transition:{item_id}", body, run)


class Comment(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.post("/items/{item_id}/comments", status_code=201)
def comment_item(
    item_id: UUID,
    body: Comment,
    user: dict = Depends(current_user),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def run():
        _may(conn, user, item_id, "comment")
        apply_change(conn, user, "comment", {}, item_id, note=body.body)
        return _respond(conn, user, item_id)

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, user, key, f"comment:{item_id}", body, run, status=201)


@router.get("/teams/{team_id}/members")
def team_members(
    team_id: UUID,
    user: dict = Depends(current_user),
    q: str | None = Query(None, max_length=120),
    can_own: bool = False,
):
    """Active people on the team, the first 100. `q` finds anyone else by name or email, and
    `can_own` leaves out viewers, who cannot be given work, so they never use up the 100. Outsiders
    get 404; a team whose members are all deactivated is an empty list."""
    where, params = "", []
    if q:
        where, params = " and (u.name ilike %s or u.email ilike %s)", [db.contains(q)] * 2
    if can_own:
        where += " and m.role <> 'viewer'"
    with db.pool.connection() as conn:
        if not visible_teams(conn, user, team_id):
            raise HTTPException(404, "team not found")
        rows = conn.execute(
            "select u.id, u.name, u.email, m.role from team_members m join users u on u.id = m.user_id"
            f" where m.team_id = %s and u.deactivated_at is null{where}"
            " order by m.role, u.name limit %s",
            (team_id, *params, LIST_CAP + 1),
        ).fetchall()
    return {"members": rows[:LIST_CAP], "capped": len(rows) > LIST_CAP}
