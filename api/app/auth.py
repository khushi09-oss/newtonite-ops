"""Sessions and sign-in. The cookie carries a signed session id; the session row decides whether it
still works, so signing out or deactivating a user takes effect on the next request."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from app import db, limits


def flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


COOKIE = "session"
MAX_AGE = 7 * 24 * 3600
# Off unless switched on: production never offers a passwordless sign-in by accident.
DEV_LOGIN = flag("DEV_LOGIN")
# On wherever the site is served over HTTPS; off for plain-HTTP localhost.
COOKIE_SECURE = flag("COOKIE_SECURE")
WRITE_LIMIT_PER_MINUTE = int(os.environ.get("WRITE_LIMIT_PER_MINUTE", "300"))
WRITE_WINDOW_SECONDS = 60
# Fails loudly at import when SESSION_SECRET is unset, rather than signing with a guessable default.
signer = URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="session")

router = APIRouter()
USER_COLS = "u.id, u.email, u.name, u.is_admin, u.private"


def issue_session(conn, user_id) -> str:
    """Create a session row and return the signed cookie value for it."""
    sid = conn.execute(
        "insert into sessions (user_id, expires_at) values (%s, now() + make_interval(secs => %s))"
        " returning id",
        (user_id, MAX_AGE),
    ).fetchone()["id"]
    return signer.dumps(str(sid))


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE, token, max_age=MAX_AGE, httponly=True, samesite="lax", secure=COOKIE_SECURE
    )


def session_id(request: Request) -> str:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "not signed in")
    try:
        return signer.loads(token, max_age=MAX_AGE)
    except BadSignature:  # covers SignatureExpired too
        raise HTTPException(401, "session is invalid or expired")


# The one definition of a live session: unrevoked, unexpired, and its user still active.
LIVE_SESSION = (
    "select {cols} from sessions s join users u on u.id = s.user_id"
    " where s.id = %s and s.revoked_at is null and s.expires_at > now()"
    " and u.deactivated_at is null"
)


def session_user(conn, sid) -> dict | None:
    return conn.execute(LIVE_SESSION.format(cols=USER_COLS), (sid,)).fetchone()


def current_user(request: Request) -> dict:
    """The signed-in user, or 401. Every route that needs an actor depends on this. Writes are also
    rate limited per user here, so no mutating route can forget it."""
    sid = session_id(request)
    with db.pool.connection() as conn:
        user = session_user(conn, sid)
    if user is None:
        raise HTTPException(401, "session has ended; sign in again")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        limits.check(f"write:{user['id']}", WRITE_LIMIT_PER_MINUTE, WRITE_WINDOW_SECONDS)
    return user


class Login(BaseModel):
    email: str


@router.post("/auth/dev-login")
def dev_login(body: Login, response: Response):
    """Sign in as a seeded user with no password: local development, tests and the demo's role cards.
    Never as a private account, which belongs to a real person who signed in with Google."""
    if not DEV_LOGIN:
        raise HTTPException(404, "Not Found")
    from app import demo  # demo imports this module

    email = body.email.strip().lower()
    # On the public demo only the shared demo people: anyone else there, even someone the shared
    # admin invited, is a real person whose account must not open without their Google sign-in.
    if demo.DEMO_MODE and not demo.is_demo_account(email):
        raise HTTPException(404, "no seeded user with that email")
    with db.pool.connection() as conn:
        user = conn.execute(
            f"select {USER_COLS} from users u"
            " where u.email = %s and u.deactivated_at is null and not u.private",
            (email,),
        ).fetchone()
        if user is None:
            raise HTTPException(404, "no seeded user with that email")
        set_session_cookie(response, issue_session(conn, user["id"]))
    return user


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    """Revoke the session server-side. The cookie is HttpOnly, so the browser cannot clear it itself."""
    try:
        sid = session_id(request)
    except HTTPException:
        sid = None
    if sid:
        with db.pool.connection() as conn:
            conn.execute(
                "update sessions set revoked_at = now() where id = %s and revoked_at is null",
                (sid,),
            )
    response.delete_cookie(COOKIE, httponly=True, samesite="lax", secure=COOKIE_SECURE)


@router.get("/me")
def me(user: dict = Depends(current_user)):
    """The caller, the teams they are on, and every team they can see. The screens build every team
    list from `visible_teams`, the same rule the item routes use (app.items.visible_teams)."""
    from app.items import visible_teams  # items imports this module for current_user

    with db.pool.connection() as conn:
        teams = conn.execute(
            "select t.id, t.name, t.key, t.color, m.role from team_members m join teams t on t.id = m.team_id"
            " where m.user_id = %s order by t.name",
            (user["id"],),
        ).fetchall()
        return {**user, "teams": teams, "visible_teams": visible_teams(conn, user)}
