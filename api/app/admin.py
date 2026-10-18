"""People and teams, for admins only. Every action is recorded in admin_events. Non-admins get 404,
the same answer the rest of the API gives outsiders."""

import itertools
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from pydantic import BaseModel, Field, field_validator

from app import db, demo
from app.auth import current_user
from app.idempotency import RetryKey, idempotent
from app.items import OPEN, Text, _jsonb, apply_change, next_team_colour

router = APIRouter(prefix="/admin")
LIST_CAP = 100
Role = Literal["viewer", "member", "lead"]


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(404, "Not Found")
    return user


def record(conn, actor: dict, kind: str, **target) -> None:
    conn.execute(
        "insert into admin_events (actor_id, kind, target) values (%s, %s, %s)",
        (actor["id"], kind, _jsonb(target)),
    )


def require_public(conn, team_id=None, user_id=None) -> None:
    """404 for a private team or person, the same answer as one that does not exist. On the public
    demo the admin account is shared, so a visitor's own account and workspace are not its to see."""
    hidden = conn.execute(
        "select exists (select 1 from teams where id = %s and private)"
        " or exists (select 1 from users where id = %s and private) as hidden",
        (team_id, user_id),
    ).fetchone()["hidden"]
    if hidden:
        raise HTTPException(404, "Not Found")


USER_VIEW = (
    "select u.id, u.email, u.name, u.is_admin, u.deactivated_at, exists (select 1 from sessions s where s.user_id = u.id) as signed_in,"
    " (select max(s.created_at) from sessions s where s.user_id = u.id) as last_sign_in,"
    " coalesce((select json_agg(json_build_object('team_id', t.id, 'team', t.name, 'color', t.color,"
    "   'role', m.role)"
    "   order by t.name) from team_members m join teams t on t.id = m.team_id"
    "   where m.user_id = u.id and not t.private), '[]') as teams"
    " from users u"
)


def release_if_viewer(conn, actor: dict, team_id, user_id, role: str) -> int:
    """A viewer does no work, so becoming one hands back the open items you held on that team, the
    same as leaving it. Each release is in the item's history."""
    if role != "viewer":
        return 0
    note = "Released: their role on this team is now viewer"
    return len(release_open_items(conn, actor, user_id, note, team_id=team_id))


def release_open_items(conn, admin: dict, user_id, note: str, team_id=None) -> list[str]:
    """Release every open item `user_id` holds (on one team, or all), each through apply_change."""
    cond, params = "", [user_id, list(OPEN)]
    if team_id is not None:
        cond, params = " and team_id = %s", [*params, team_id]
    held = conn.execute(
        "select id, version from work_items where assignee_id = %s and status = any(%s)"
        f"{cond} order by id for update",
        params,
    ).fetchall()
    for item in held:
        apply_change(
            conn, admin, "released", {"assignee_id": None}, item["id"], item["version"], note=note
        )
    return [str(i["id"]) for i in held]


@router.get("/users")
def list_users(
    admin: dict = Depends(require_admin),
    q: str | None = None,
    limit: int = Query(50, ge=1, le=LIST_CAP),
):
    where, params = " where not u.private", []
    if q:
        where += " and (u.email ilike %s or u.name ilike %s)"
        params = [db.contains(q), db.contains(q)]
    with db.pool.connection() as conn:
        rows = conn.execute(
            f"{USER_VIEW}{where} order by u.name limit %s", [*params, limit + 1]
        ).fetchall()
    return {"users": rows[:limit], "capped": len(rows) > limit}


class Membership(BaseModel):
    team_id: UUID
    role: Role


class Invite(BaseModel):
    # A shape check only. Google's verified email at sign-in is what proves the address is real.
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    name: Text(120)
    is_admin: bool = False
    teams: list[Membership] = []

    @field_validator("teams")
    @classmethod
    def one_role_per_team(cls, teams: list[Membership]) -> list[Membership]:
        if len({m.team_id for m in teams}) != len(teams):
            raise ValueError("each team can be listed once, with one role")
        return teams


@router.post("/users", status_code=201)
def invite(body: Invite, admin: dict = Depends(require_admin), key: RetryKey = None):
    """Create a person who can sign in with Google using this email. Nothing is sent: the admin
    tells them, and they sign in with the button."""
    email = body.email.strip().lower()

    def run():
        try:
            uid = conn.execute(
                "insert into users (email, name, is_admin) values (%s, %s, %s) returning id",
                (email, body.name.strip(), body.is_admin),
            ).fetchone()["id"]
        except UniqueViolation:
            raise HTTPException(409, f"{email} has already been invited")
        for m in body.teams:
            require_public(conn, team_id=m.team_id)
        try:
            for m in body.teams:
                conn.execute(
                    "insert into team_members (team_id, user_id, role) values (%s, %s, %s)",
                    (m.team_id, uid, m.role),
                )
        except ForeignKeyViolation:
            raise HTTPException(422, "one of those teams does not exist")
        record(
            conn,
            admin,
            "invited",
            user_id=uid,
            email=email,
            is_admin=body.is_admin,
            teams=[m.model_dump() for m in body.teams],
        )
        return conn.execute(f"{USER_VIEW} where u.id = %s", (uid,)).fetchone()

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, admin, key, "invite", body, run, status=201)


class UserChange(BaseModel):
    name: Text(120) | None = None
    is_admin: bool | None = None
    active: bool | None = None


@router.patch("/users/{user_id}")
def change_user(
    user_id: UUID, body: UserChange, admin: dict = Depends(require_admin), key: RetryKey = None
):
    if user_id == admin["id"] and (body.is_admin is False or body.active is False):
        raise HTTPException(422, "You cannot remove your own admin access or deactivate yourself")

    def run():
        demo.guard(conn, user_id)
        require_public(conn, user_id=user_id)
        if conn.execute("select 1 from users where id = %s", (user_id,)).fetchone() is None:
            raise HTTPException(404, "user not found")
        if body.name is not None:
            conn.execute("update users set name = %s where id = %s", (body.name.strip(), user_id))
        if body.is_admin is not None:
            conn.execute("update users set is_admin = %s where id = %s", (body.is_admin, user_id))
        if body.active is False:
            conn.execute(
                "update users set deactivated_at = coalesce(deactivated_at, now()) where id = %s",
                (user_id,),
            )
            conn.execute(
                "update sessions set revoked_at = now() where user_id = %s and revoked_at is null",
                (user_id,),
            )
            release_open_items(conn, admin, user_id, "Released because the owner was deactivated")
        elif body.active is True:
            conn.execute("update users set deactivated_at = null where id = %s", (user_id,))
        record(conn, admin, "user_changed", user_id=user_id, **body.model_dump(exclude_none=True))
        return conn.execute(f"{USER_VIEW} where u.id = %s", (user_id,)).fetchone()

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, admin, key, f"change-user:{user_id}", body, run)


@router.get("/teams")
def list_teams(admin: dict = Depends(require_admin), q: str | None = Query(None, max_length=80)):
    """Public teams, the first 100 by name; `q` finds any other by name or code."""
    where, params = "", []
    if q:
        where, params = " and (t.name ilike %s or t.key ilike %s)", [db.contains(q)] * 2
    with db.pool.connection() as conn:
        rows = conn.execute(
            "select t.id, t.name, t.key, t.color, count(m.user_id) as members from teams t"
            f" left join team_members m on m.team_id = t.id where not t.private{where}"
            " group by t.id order by t.name"
            " limit %s",
            (*params, LIST_CAP + 1),
        ).fetchall()
    return {"teams": rows[:LIST_CAP], "capped": len(rows) > LIST_CAP}


class NewTeam(BaseModel):
    name: Text(80)


def team_key(conn, name: str) -> str:
    """The first three letters of the name, then RSK2, RSK3 and on if that code is taken."""
    base = (re.sub("[^A-Za-z]", "", name).upper() + "XXX")[:3]
    taken = {
        r["key"] for r in conn.execute("select key from teams where key like %s", (base + "%",))
    }
    return next(
        k
        for k in itertools.chain([base], (f"{base}{n}" for n in itertools.count(2)))
        if k not in taken
    )


@router.post("/teams", status_code=201)
def create_team(body: NewTeam, admin: dict = Depends(require_admin), key: RetryKey = None):
    name = body.name.strip()

    def run():
        try:
            team = conn.execute(
                "insert into teams (name, key, color) values (%s, %s, %s) returning id, name, key, color",
                (name, team_key(conn, name), next_team_colour(conn, admin)),
            ).fetchone()
        except UniqueViolation as e:
            if e.diag.constraint_name == "teams_key_idx":  # two admins took the same code at once
                raise HTTPException(
                    409, "Another team was just created with the same code. Try again."
                )
            raise HTTPException(409, f"A team called {name} already exists")
        record(conn, admin, "team_created", team_id=team["id"], name=team["name"])
        return {**team, "members": 0}

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, admin, key, "create-team", body, run, status=201)


@router.get("/teams/{team_id}/members")
def team_roster(
    team_id: UUID,
    admin: dict = Depends(require_admin),
    q: str | None = Query(None, max_length=120),
):
    """The team's people, the first 100; `q` finds anyone else by name or email."""
    where, params = "", []
    if q:
        where, params = " and (u.name ilike %s or u.email ilike %s)", [db.contains(q)] * 2
    with db.pool.connection() as conn:
        require_public(conn, team_id=team_id)
        rows = conn.execute(
            "select u.id, u.email, u.name, m.role, u.deactivated_at from team_members m"
            f" join users u on u.id = m.user_id where m.team_id = %s and not u.private{where}"
            " order by m.role, u.name"
            " limit %s",
            (team_id, *params, LIST_CAP + 1),
        ).fetchall()
    return {"members": rows[:LIST_CAP], "capped": len(rows) > LIST_CAP}


class RoleChange(BaseModel):
    role: Role


@router.put("/teams/{team_id}/members/{user_id}")
def set_role(
    team_id: UUID,
    user_id: UUID,
    body: RoleChange,
    admin: dict = Depends(require_admin),
    key: RetryKey = None,
):
    """Add someone to a team, or change their role on it."""

    def run():
        demo.guard(conn, user_id)
        require_public(conn, team_id, user_id)
        before = conn.execute(
            "select role from team_members where team_id = %s and user_id = %s",
            (team_id, user_id),
        ).fetchone()
        try:
            conn.execute(
                "insert into team_members (team_id, user_id, role) values (%s, %s, %s)"
                " on conflict (team_id, user_id) do update set role = excluded.role",
                (team_id, user_id, body.role),
            )
        except ForeignKeyViolation:
            raise HTTPException(404, "team or user not found")
        released = release_if_viewer(conn, admin, team_id, user_id, body.role)
        record(
            conn,
            admin,
            "role_set",
            team_id=team_id,
            user_id=user_id,
            before=before["role"] if before else None,
            after=body.role,
        )
        return {"team_id": team_id, "user_id": user_id, "role": body.role, "released": released}

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, admin, key, f"set-role:{team_id}:{user_id}", body, run)


@router.delete("/teams/{team_id}/members/{user_id}")
def remove_member(
    team_id: UUID, user_id: UUID, admin: dict = Depends(require_admin), key: RetryKey = None
):
    """Take someone off a team. Their open items on it are released through apply_change, so each
    release is in the item's history and nothing stays owned by someone who can no longer see it."""

    def run():
        demo.guard(conn, user_id)
        require_public(conn, team_id, user_id)
        gone = conn.execute(
            "delete from team_members where team_id = %s and user_id = %s returning role",
            (team_id, user_id),
        ).fetchone()
        if gone is None:
            raise HTTPException(404, "not a member of that team")
        held = release_open_items(
            conn, admin, user_id, "Released because the owner left the team", team_id
        )
        record(
            conn,
            admin,
            "member_removed",
            team_id=team_id,
            user_id=user_id,
            role=gone["role"],
            released=held,
        )
        return {"released": len(held)}

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, admin, key, f"remove-member:{team_id}:{user_id}", None, run)


@router.get("/events")
def admin_log(admin: dict = Depends(require_admin), limit: int = Query(50, ge=1, le=LIST_CAP)):
    with db.pool.connection() as conn:
        rows = conn.execute(
            # Names for the person and team a change was about, so the log can say who and where.
            "select e.id, e.kind, e.target, e.created_at, u.name as actor_name,"
            " tu.name as target_user_name, tt.name as target_team_name from admin_events e"
            " join users u on u.id = e.actor_id"
            " left join users tu on tu.id = (e.target->>'user_id')::uuid"
            " left join teams tt on tt.id = (e.target->>'team_id')::uuid"
            " where not coalesce(tu.private, false) and not coalesce(tt.private, false)"
            " order by e.id desc limit %s",
            (limit + 1,),
        ).fetchall()
    return {"events": rows[:limit], "capped": len(rows) > limit}


JOB_STATUSES = ("pending", "done", "dead")
# Jobs the admin may see and retry: all but those about an item on a private team, whose title,
# error text and very existence belong to that workspace (D-26). Those still retry on their own.
PUBLIC_JOB = (
    "not exists (select 1 from work_items pw join teams pt on pt.id = pw.team_id"
    " where pw.id::text = o.payload->>'item_id' and pt.private)"
)


@router.get("/jobs")
def list_jobs(
    admin: dict = Depends(require_admin),
    status: str = "dead",
    limit: int = Query(50, ge=1, le=LIST_CAP),
):
    """Background jobs by status, newest first, with what each was about and why it last failed."""
    if status not in JOB_STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(JOB_STATUSES)}")
    with db.pool.connection() as conn:
        counts = {
            r["status"]: r["n"]
            for r in conn.execute(
                f"select status, count(*) as n from outbox o where {PUBLIC_JOB} group by status"
            )
        }
        rows = conn.execute(
            "select o.id, o.kind, o.status, o.attempts, o.last_error, o.created_at,"
            " o.next_attempt_at, o.processed_at, o.payload->>'kind' as event_kind,"
            " w.id as item_id, w.title as item_title"
            " from outbox o left join (work_items w join teams t on t.id = w.team_id and not t.private)"
            " on w.id::text = o.payload->>'item_id'"
            f" where o.status = %s and {PUBLIC_JOB} order by o.id desc limit %s",
            (status, limit + 1),
        ).fetchall()
    return {
        "counts": {s: counts.get(s, 0) for s in JOB_STATUSES},
        "jobs": rows[:limit],
        "capped": len(rows) > limit,
    }


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, admin: dict = Depends(require_admin), key: RetryKey = None):
    """Give a dead job a fresh set of attempts. One conditional update, so two admins retrying at
    once cannot both succeed; the second gets 409. Safe because every handler is idempotent."""

    def run():
        row = conn.execute(
            "update outbox o set status = 'pending', attempts = 0, next_attempt_at = now()"
            f" where id = %s and status = 'dead' and {PUBLIC_JOB} returning id, kind, last_error",
            (job_id,),
        ).fetchone()
        if row is None:
            seen = conn.execute(f"select 1 from outbox o where id = %s and {PUBLIC_JOB}", (job_id,))
            if seen.fetchone() is None:
                raise HTTPException(404, "job not found")
            raise HTTPException(409, "That job is not failed any more; someone may have retried it")
        record(
            conn, admin, "job_retried", job_id=job_id, job_kind=row["kind"], error=row["last_error"]
        )
        return {"id": job_id, "status": "pending"}

    with db.pool.connection() as conn, conn.transaction():
        return idempotent(conn, admin, key, f"retry-job:{job_id}", None, run)
