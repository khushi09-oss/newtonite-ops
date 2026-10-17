"""Demo mode. The reset is tested against a throwaway database: run against the shared test database
it would wipe it mid-suite."""

from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from test_admin import as_user
from test_google import CLIENT, KEY, token

from app import db, demo, google, stream

ADMIN = "ava.okafor@newtonite.dev"


@pytest.fixture
def demo_on(monkeypatch):
    monkeypatch.setattr(demo, "DEMO_MODE", True)
    monkeypatch.setattr(google, "CLIENT_ID", CLIENT)
    monkeypatch.setattr(google, "signing_key", lambda _t: KEY.public_key())
    with db.pool.connection() as conn:
        conn.execute("delete from rate_limits where key like 'signin:%%'")


def test_demo_google_visitor_gets_in_without_invite(client, demo_on):
    email = f"visitor-{uuid4().hex[:8]}@gmail.com"
    client.cookies.clear()
    r = client.post(
        "/auth/google",
        json={"credential": token(email=email, sub=f"sub-{email}", name="A Visitor")},
    )
    assert r.status_code == 200, r.text
    me = client.get("/me").json()
    assert me["name"] == "A Visitor" and me["is_admin"] is False
    # A real person's account: hidden from the shared admin, and closed to password-free sign-in.
    client.cookies.clear()
    assert client.post("/auth/dev-login", json={"email": email}).status_code == 404
    assert sorted((t["name"], t["role"]) for t in me["teams"]) == [
        ("Risk", "viewer"),
        ("Support", "member"),
    ]
    assert client.get("/auth/config").json()["demo"] is True


def test_demo_accounts_cannot_be_changed(client, login, demo_on):
    """The target is a throwaway account on the demo domain, never one of the shared demo accounts,
    so even a broken guard cannot disturb the seed."""
    login(ADMIN)
    with db.pool.connection() as conn:
        target = conn.execute(
            "insert into users (email, name) values (%s, 'Guard target') returning id",
            (f"guard-target-{uuid4().hex[:8]}@newtonite.dev",),
        ).fetchone()["id"]
        team = conn.execute("select id from teams where name = 'Support'").fetchone()["id"]
        conn.execute(
            "insert into team_members (team_id, user_id, role) values (%s, %s, 'member')",
            (team, target),
        )
    sid, tid = str(target), str(team)
    assert client.patch(f"/admin/users/{sid}", json={"active": False}).status_code == 403
    assert client.put(f"/admin/teams/{tid}/members/{sid}", json={"role": "lead"}).status_code == 403
    assert client.delete(f"/admin/teams/{tid}/members/{sid}").status_code == 403
    with db.pool.connection() as conn:
        row = conn.execute(
            "select u.deactivated_at, m.role from users u join team_members m on m.user_id = u.id"
            " where u.id = %s",
            (sid,),
        ).fetchone()
    assert row == {"deactivated_at": None, "role": "member"}
    # A visitor's own invitee can still be changed: the demo stays explorable.
    invited = client.post(
        "/admin/users", json={"email": f"mine-{uuid4().hex[:8]}@example.com", "name": "Mine"}
    )
    assert invited.status_code == 201
    assert (
        client.patch(f"/admin/users/{invited.json()['id']}", json={"active": False}).status_code
        == 200
    )


def private_workspace():
    """A visitor's own team, lead and urgent item, as the demo makes them: both rows marked private."""
    tag = uuid4().hex[:8]
    with db.pool.connection() as conn:
        owner = conn.execute(
            "insert into users (email, name, private) values (%s, 'Owner', true) returning id",
            (f"owner-{tag}@gmail.com",),
        ).fetchone()["id"]
        team = conn.execute(
            "insert into teams (name, key, private) values (%s, %s, true) returning id, name",
            (f"Private {tag}", f"PV{tag.upper()}"),
        ).fetchone()
        conn.execute(
            "insert into team_members (team_id, user_id, role) values (%s, %s, 'lead')",
            (team["id"], owner),
        )
    mine = as_user(owner)
    r = mine.post(
        "/items",
        json={"team_id": str(team["id"]), "kind": "task", "title": f"Secret {tag}", "priority": 0},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return {
        "owner": str(owner),
        "email": f"owner-{tag}@gmail.com",
        "team": str(team["id"]),
        "item": r.json(),
        "client": mine,
    }


def test_the_shared_admin_cannot_see_a_private_workspace_by_any_route(client, login):
    ws = private_workspace()
    item, team, owner = ws["item"], ws["team"], ws["owner"]
    assert ws["client"].get(f"/items/{item['id']}").status_code == 200  # the owner still can

    admin = login(ADMIN)
    assert client.get(f"/items/{item['id']}").status_code == 404
    assert client.get("/items", params={"team": team}).json()["items"] == []
    assert sum(client.get("/items/counts", params={"team": team}).json()["counts"].values()) == 0
    assert client.get("/items", params={"q": item["title"]}).json()["items"] == []
    assert client.get(f"/teams/{team}/members").status_code == 404
    created = client.post(
        "/items",
        json={"team_id": team, "kind": "task", "title": "Planted"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 404
    inbox = client.get("/inbox").json()
    assert item["id"] not in {i["id"] for b in inbox.values() for i in b["items"]}
    recent = client.get("/dashboard").json()["recent"]
    assert item["id"] not in {e["item_id"] for e in recent}
    assert stream.tick(str(uuid4()), admin, item["id"])["version"] is None

    assert team not in {t["id"] for t in client.get("/admin/teams").json()["teams"]}
    assert client.get("/admin/users", params={"q": ws["email"]}).json()["users"] == []
    assert client.get(f"/admin/teams/{team}/members").status_code == 404
    assert client.patch(f"/admin/users/{owner}", json={"active": False}).status_code == 404
    assert (
        client.put(f"/admin/teams/{team}/members/{admin['id']}", json={"role": "lead"}).status_code
        == 404
    )
    invite = {
        "email": f"x-{uuid4().hex[:8]}@example.com",
        "name": "X",
        "teams": [{"team_id": team, "role": "member"}],
    }
    assert client.post("/admin/users", json=invite).status_code == 404

    # And nobody can take the account over through the demo's password-free sign-in.
    client.cookies.clear()
    assert client.post("/auth/dev-login", json={"email": ws["email"]}).status_code == 404


def test_nightly_reset_needs_demo_mode_and_the_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.setattr(demo, "DEMO_MODE", False)
    assert (
        client.get("/internal/reset-demo", headers={"Authorization": "Bearer s3cret"}).status_code
        == 404
    )
    monkeypatch.setattr(demo, "DEMO_MODE", True)
    assert client.get("/internal/reset-demo").status_code == 404
    assert (
        client.get("/internal/reset-demo", headers={"Authorization": "Bearer wrong"}).status_code
        == 404
    )
    # The right secret is not sent here: it would reset the shared test database. See the next test.


def test_demo_reset_restores_a_clean_seed(client):
    name = "ops_demo_reset"
    admin = psycopg.connect(db.DATABASE_URL, autocommit=True)
    try:
        admin.execute(f"drop database if exists {name}")
        admin.execute(f"create database {name}")
        url = db.DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
        with ConnectionPool(url, min_size=1, max_size=2, kwargs={"row_factory": dict_row}) as fresh:
            db.migrate(fresh)
            demo.reset(fresh)
            with fresh.connection() as conn:
                conn.execute("insert into users (email, name) values ('junk@example.com', 'Junk')")
            demo.reset(fresh)  # a second night: the junk goes, the seed comes back whole
            with fresh.connection() as conn:
                counts = conn.execute(
                    "select (select count(*) from users) as users, (select count(*) from teams) as teams,"
                    " (select count(*) from work_items) as items,"
                    " (select count(*) from users where email = 'junk@example.com') as junk,"
                    " (select count(*) from work_items w where version <>"
                    "   (select count(*) from work_item_events e where e.item_id = w.id)) as broken"
                ).fetchone()
        assert counts == {"users": 40, "teams": 8, "items": 20000, "junk": 0, "broken": 0}
    finally:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.close()
