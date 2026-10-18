"""Google sign-in. Tokens are signed with a throwaway key and the verifier is pointed at it, so every
rejection path runs without contacting Google. Seeded fact: ira.okafor is a member of Payments and a
viewer of Onboarding, and is used by no other test."""

import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import db, google

CLIENT = "test-client.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
IRA = "ira.okafor@newtonite.dev"


def token(key=KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT,
        "sub": "google-sub-ira",
        "email": IRA,
        "email_verified": True,
        "name": "Ira Okafor",
        "iat": now,
        "exp": now + 600,
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture(autouse=True)
def google_configured(monkeypatch):
    monkeypatch.setattr(google, "CLIENT_ID", CLIENT)
    monkeypatch.setattr(google, "signing_key", lambda _token: KEY.public_key())
    monkeypatch.setattr(google, "ADMIN_EMAILS", set())
    with db.pool.connection() as conn:
        conn.execute("update users set google_sub = null where email = %s", (IRA,))
        conn.execute("delete from rate_limits where key like 'signin:%%'")
    yield
    with db.pool.connection() as conn:
        conn.execute("update users set google_sub = null where email = %s", (IRA,))


def sign_in(client, credential):
    client.cookies.clear()
    return client.post("/auth/google", json={"credential": credential})


def test_invited_user_signs_in_with_google(client):
    r = sign_in(client, token())
    assert r.status_code == 200, r.text
    me = client.get("/me").json()
    assert me["email"] == IRA and [t["name"] for t in me["teams"]] == ["Onboarding", "Payments"]
    with db.pool.connection() as conn:
        row = conn.execute("select google_sub from users where email = %s", (IRA,)).fetchone()
    assert row["google_sub"] == "google-sub-ira"


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "someone-elses-app.apps.googleusercontent.com"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 60},
        {"email_verified": False},
    ],
    ids=["token for another app", "wrong issuer", "expired", "unverified email"],
)
def test_untrustworthy_google_token_rejected(client, bad):
    r = sign_in(client, token(**bad))
    assert r.status_code == 401
    assert client.get("/me").status_code == 401


def test_google_token_with_forged_signature_rejected(client):
    assert sign_in(client, token(key=OTHER_KEY)).status_code == 401


def test_uninvited_google_account_refused(client):
    stranger = f"stranger-{uuid4().hex[:8]}@gmail.com"
    r = sign_in(client, token(email=stranger, sub="google-sub-stranger"))
    assert r.status_code == 403 and "not been invited" in r.json()["detail"]
    with db.pool.connection() as conn:
        n = conn.execute("select count(*) from users where email = %s", (stranger,)).fetchone()
    assert n["count"] == 0


def test_email_taken_over_by_another_google_account_refused(client):
    assert sign_in(client, token()).status_code == 200  # links ira to google-sub-ira
    r = sign_in(client, token(sub="google-sub-someone-else"))
    assert r.status_code == 403 and "different Google account" in r.json()["detail"]


def test_first_admin_is_created_from_the_setting(client, monkeypatch):
    founder = f"founder-{uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(google, "ADMIN_EMAILS", {founder})
    r = sign_in(client, token(email=founder, sub=f"google-sub-{founder}", name="Founder"))
    assert r.status_code == 200 and r.json()["is_admin"] is True
    assert client.get("/me").json()["name"] == "Founder"
    with db.pool.connection() as conn:
        uid = conn.execute("select id from users where email = %s", (founder,)).fetchone()["id"]
        conn.execute("delete from sessions where user_id = %s", (uid,))
        conn.execute("delete from users where id = %s", (uid,))


def test_google_sign_in_hidden_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(google, "CLIENT_ID", "")
    assert sign_in(client, token()).status_code == 404
    assert client.get("/auth/config").json()["google_client_id"] is None


def test_google_sign_in_rate_limited_per_address(client, monkeypatch):
    monkeypatch.setattr(google, "SIGNIN_LIMIT_PER_MINUTE", 2)
    codes = [sign_in(client, token(key=OTHER_KEY)).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_the_owner_address_becomes_admin_even_if_an_account_was_made_for_it_first(
    client, monkeypatch
):
    """A visitor can add any address to their team, which makes an account for it. If that address
    is the owner's, the owner must still become admin when Google proves it is them."""
    founder = f"founder-{uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(google, "ADMIN_EMAILS", {founder})
    with db.pool.connection() as conn:
        conn.execute(
            "insert into users (email, name, private) values (%s, 'Placeholder', true)", (founder,)
        )
    r = sign_in(client, token(email=founder, sub=f"google-sub-{founder}", name="Founder"))
    assert r.status_code == 200 and r.json()["is_admin"] is True
