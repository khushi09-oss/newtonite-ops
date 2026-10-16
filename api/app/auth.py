"""Dev login and the session cookie. No passwords: the seeded users are the whole user base."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from app import db

COOKIE = "session"
MAX_AGE = 7 * 24 * 3600
# Fails loudly at import when SESSION_SECRET is unset, rather than signing with a guessable default.
signer = URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="session")

router = APIRouter()


class Login(BaseModel):
    email: str


def current_user(request: Request) -> dict:
    """The signed-in user, or 401. Used as a dependency by every route that needs an actor."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "not signed in")
    try:
        user_id = signer.loads(token, max_age=MAX_AGE)
    except BadSignature:  # covers SignatureExpired too
        raise HTTPException(401, "session is invalid or expired")
    with db.pool.connection() as conn:
        user = conn.execute(
            "select id, email, name, is_admin from users where id = %s", (user_id,)
        ).fetchone()
    if user is None:
        raise HTTPException(401, "session user no longer exists")
    return user


@router.post("/auth/dev-login")
def dev_login(body: Login, response: Response):
    with db.pool.connection() as conn:
        user = conn.execute(
            "select id, email, name, is_admin from users where email = %s",
            (body.email.strip().lower(),),
        ).fetchone()
    if user is None:
        raise HTTPException(404, "no seeded user with that email")
    response.set_cookie(
        COOKIE, signer.dumps(str(user["id"])), max_age=MAX_AGE, httponly=True, samesite="lax"
    )
    return user


@router.get("/me")
def me(user: dict = Depends(current_user)):
    with db.pool.connection() as conn:
        teams = conn.execute(
            "select t.id, t.name, m.role from team_members m join teams t on t.id = m.team_id"
            " where m.user_id = %s order by t.name",
            (user["id"],),
        ).fetchall()
    return {**user, "teams": teams}
