"""What a team lead does for their own team, and, on the demo, a visitor's own workspace.

- A lead adds people to their team by email, changes their roles, and takes them off it.
- On the demo, a visitor who signed in with Google creates their own teams. The teams and everyone
  added to them are private (D-26): the shared demo accounts cannot see them or be added to them.
- Off the demo, admins still decide who can get in: a lead can only add someone who already has
  an account."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from app import db, demo
from app.admin import record, release_open_items, team_key
from app.auth import current_user
from app.items import next_team_colour

router = APIRouter()


class NewTeam(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/teams", status_code=201)
def create_team(body: NewTeam, user: dict = Depends(current_user)):
    """A visitor's own team, with them as its lead. Only on the demo, and only for a visitor's own
    account: a team made from a shared demo account would be visible to every visitor."""
    if not demo.DEMO_MODE:
        raise HTTPException(404, "Not Found")
    if not user["private"]:
        raise HTTPException(403, "Sign in with Google to start your own workspace")
    name = body.name.strip()
    with db.pool.connection() as conn, conn.transaction():
        try:
            team = conn.execute(
                "insert into teams (name, key, private, color) values (%s, %s, true, %s)"
                " returning id, name, key, color",
                (name, team_key(conn, name), next_team_colour(conn, user)),
            ).fetchone()
        except UniqueViolation:  # two teams took the same code at once
            raise HTTPException(409, "Another team was just created with the same code. Try again.")
        conn.execute(
            "insert into team_members (team_id, user_id, role) values (%s, %s, 'lead')",
            (team["id"], user["id"]),
        )
        record(conn, user, "team_created", team_id=team["id"], name=team["name"])
    return {**team, "role": "lead"}


def lead_of(conn, user: dict, team_id: UUID) -> dict:
    """The team, if the caller leads it. 404 for outsiders, 403 for members who are not its lead."""
    team = conn.execute(
        "select t.id, t.private, m.role from teams t join team_members m"
        " on m.team_id = t.id and m.user_id = %s where t.id = %s",
        (user["id"], team_id),
    ).fetchone()
    if team is None:
        raise HTTPException(404, "team not found")
    if team["role"] != "lead":
        raise HTTPException(403, "Only the team's lead can change who is on it")
    # The demo's own teams belong to the shared accounts; only the Admin page changes those.
    if demo.DEMO_MODE and not team["private"]:
        raise HTTPException(
            403, "The demo's teams are shared. Start your own workspace to add people."
        )
    return team


class Member(BaseModel):
    # A shape check only. Google's verified email at sign-in is what proves the address is real.
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    name: str | None = Field(None, min_length=1, max_length=120)
    role: Literal["viewer", "member", "lead"] = "member"


@router.put("/teams/{team_id}/members")
def add_member(team_id: UUID, body: Member, user: dict = Depends(current_user)):
    """Add someone to the team by email, or change the role of someone already on it."""
    email = body.email.strip().lower()
    with db.pool.connection() as conn, conn.transaction():
        lead_of(conn, user, team_id)
        person = conn.execute(
            "select id, private, deactivated_at from users where email = %s", (email,)
        ).fetchone()
        if person and person["id"] == user["id"]:
            raise HTTPException(422, "You cannot change your own role")
        if person and person["deactivated_at"] is not None:
            raise HTTPException(422, f"{email} has been deactivated")
        if demo.DEMO_MODE and person and not person["private"]:
            raise HTTPException(
                403, f"{email} is a shared demo account and cannot join a workspace"
            )
        if person is None and not demo.DEMO_MODE:
            raise HTTPException(404, f"{email} has no account yet. Ask an admin to invite them.")
        if person is None:  # on the demo, adding someone is the invite: they sign in with Google
            person = conn.execute(
                "insert into users (email, name, private) values (%s, %s, true) returning id",
                (email, (body.name or email.split("@")[0]).strip()),
            ).fetchone()
        before = conn.execute(
            "select role from team_members where team_id = %s and user_id = %s",
            (team_id, person["id"]),
        ).fetchone()
        conn.execute(
            "insert into team_members (team_id, user_id, role) values (%s, %s, %s)"
            " on conflict (team_id, user_id) do update set role = excluded.role",
            (team_id, person["id"], body.role),
        )
        record(
            conn,
            user,
            "role_set",
            team_id=team_id,
            user_id=person["id"],
            before=before["role"] if before else None,
            after=body.role,
        )
    return {"team_id": team_id, "user_id": person["id"], "email": email, "role": body.role}


@router.delete("/teams/{team_id}/members/{user_id}")
def remove_member(team_id: UUID, user_id: UUID, user: dict = Depends(current_user)):
    """Take someone off the team. Their open items on it are released, each in the item's history."""
    if user_id == user["id"]:
        raise HTTPException(422, "You cannot take yourself off your own team")
    with db.pool.connection() as conn, conn.transaction():
        lead_of(conn, user, team_id)
        gone = conn.execute(
            "delete from team_members where team_id = %s and user_id = %s returning role",
            (team_id, user_id),
        ).fetchone()
        if gone is None:
            raise HTTPException(404, "not a member of that team")
        held = release_open_items(
            conn, user, user_id, "Released because the owner left the team", team_id
        )
        record(
            conn,
            user,
            "member_removed",
            team_id=team_id,
            user_id=user_id,
            role=gone["role"],
            released=held,
        )
    return {"released": len(held)}
