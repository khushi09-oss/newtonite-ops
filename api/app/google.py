"""Sign in with Google. The browser gets an ID token from Google; this module proves it is genuine,
fresh, meant for this app and for a verified email, then maps it to an invited user."""

import os

import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app import auth, db, demo, limits

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CERTS_URL = os.environ.get("GOOGLE_CERTS_URL", "https://www.googleapis.com/oauth2/v3/certs")
ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
# People who become admins the first time they sign in. How the first admin of a new deployment
# gets in, since nobody exists yet to invite them.
ADMIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
}
SIGNIN_LIMIT_PER_MINUTE = 20

_keys = jwt.PyJWKClient(CERTS_URL, cache_keys=True, lifespan=3600)
router = APIRouter()


def signing_key(token: str):
    """Google's public key for this token, fetched from its published key set and cached."""
    return _keys.get_signing_key_from_jwt(token).key


def verify(token: str) -> dict:
    """The token's claims, or 401. Checks signature, issuer, audience, expiry and verified email."""
    try:
        claims = jwt.decode(
            token,
            signing_key(token),
            algorithms=["RS256"],
            audience=CLIENT_ID,
            issuer=ISSUERS,
            options={"require": ["exp", "iat", "sub", "email", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Google sign-in could not be verified ({type(exc).__name__})")
    if claims.get("email_verified") is not True:
        raise HTTPException(401, "Google has not verified this email address")
    return claims


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host


class GoogleSignIn(BaseModel):
    credential: str


@router.get("/auth/config")
def auth_config():
    """Which sign-in methods this deployment offers. Public, and holds nothing secret."""
    return {
        "google_client_id": CLIENT_ID or None,
        "dev_login": auth.DEV_LOGIN,
        "demo": demo.DEMO_MODE,
    }


@router.post("/auth/google")
def google_sign_in(body: GoogleSignIn, request: Request, response: Response):
    if not CLIENT_ID:
        raise HTTPException(404, "Not Found")
    limits.check(f"signin:{client_ip(request)}", SIGNIN_LIMIT_PER_MINUTE)
    claims = verify(body.credential)
    email, sub = claims["email"].strip().lower(), claims["sub"]
    with db.pool.connection() as conn, conn.transaction():
        user = conn.execute(
            "select id, email, name, is_admin, google_sub, deactivated_at from users where email = %s",
            (email,),
        ).fetchone()
        if user is None and email in ADMIN_EMAILS:
            # On the demo this is a real person's account beside the shared ones, so it is private.
            user = conn.execute(
                "insert into users (email, name, is_admin, private) values (%s, %s, true, %s)"
                " returning id, email, name, is_admin, google_sub, deactivated_at",
                (email, claims.get("name") or email.split("@")[0], demo.DEMO_MODE),
            ).fetchone()
        elif user is None and demo.DEMO_MODE:  # a public demo lets any verified Google account in
            user = demo.add_visitor(conn, email, claims.get("name") or email.split("@")[0], sub)
        if user is None or user["deactivated_at"] is not None:
            raise HTTPException(403, f"{email} has not been invited. Ask an admin to invite you.")
        if user["google_sub"] is not None and user["google_sub"] != sub:
            raise HTTPException(
                403, "This email is linked to a different Google account. Ask an admin for help."
            )
        if user["google_sub"] is None:
            conn.execute("update users set google_sub = %s where id = %s", (sub, user["id"]))
        auth.set_session_cookie(response, auth.issue_session(conn, user["id"]))
    return {k: user[k] for k in ("id", "email", "name", "is_admin")}
