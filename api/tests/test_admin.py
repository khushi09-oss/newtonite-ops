"""Admin screens: inviting, roles, deactivation, teams. Every test works in a team of its own, so
nobody it creates can join the Risk team that the claim race and the demo script use."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from conftest import session_cookie
from fastapi.testclient import TestClient
from test_google import CLIENT, KEY, token

from app import db, google
from app.main import app

ADMIN = "ava.okafor@newtonite.dev"


@pytest.fixture
def team(client, login):
    """A fresh team, created through the admin API."""
    login(ADMIN)
    r = client.post("/admin/teams", json={"name": f"Test team {uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    return r.json()


def invite(client, team, role="member", **extra):
    email = f"person-{uuid4().hex[:8]}@example.com"
    body = {
        "email": email,
        "name": "New Person",
        "teams": [{"team_id": team["id"], "role": role}],
        **extra,
    }
    r = client.post("/admin/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def as_user(user_id) -> TestClient:
    c = TestClient(app)
    c.cookies.set("session", session_cookie(user_id))
    return c


def new_item(c, team, title="Admin test item"):
    r = c.post(
        "/items",
        json={"team_id": team["id"], "kind": "task", "title": title},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_new_teams_get_their_own_code_and_count_from_one(client, team):
    first = client.post("/admin/teams", json={"name": f"Zephyr {uuid4().hex[:8]}"}).json()
    second = client.post("/admin/teams", json={"name": f"Zephyr {uuid4().hex[:8]}"}).json()
    assert first["key"].startswith("ZEP") and second["key"].startswith("ZEP")
    assert first["key"] != second["key"]
    assert [new_item(client, team)["number"] for _ in range(3)] == [1, 2, 3]
    assert new_item(client, first)["number"] == 1


def test_concurrent_creates_on_one_team_take_distinct_keys(client, team, login):
    admin = login(ADMIN)
    with ThreadPoolExecutor(8) as pool:
        numbers = list(
            pool.map(lambda _: new_item(as_user(admin["id"]), team)["number"], range(16))
        )
    assert sorted(numbers) == list(range(1, 17))


def test_non_admin_gets_404_on_admin_routes(client, login):
    login("finn.okafor@newtonite.dev")  # leads Risk, not an admin
    assert client.get("/admin/users").status_code == 404
    assert client.post("/admin/teams", json={"name": "sneaky"}).status_code == 404
    assert client.get("/admin/events").status_code == 404


def test_invited_person_signs_in_with_google_and_sees_their_team(client, team, monkeypatch):
    person = invite(client, team)
    assert person["signed_in"] is False and person["teams"][0]["role"] == "member"
    monkeypatch.setattr(google, "CLIENT_ID", CLIENT)
    monkeypatch.setattr(google, "signing_key", lambda _t: KEY.public_key())
    client.cookies.clear()
    r = client.post(
        "/auth/google", json={"credential": token(email=person["email"], sub=f"sub-{person['id']}")}
    )
    assert r.status_code == 200, r.text
    assert [(t["name"], t["role"]) for t in client.get("/me").json()["teams"]] == [
        (team["name"], "member")
    ]


def test_duplicate_invite_conflicts(client, team):
    person = invite(client, team)
    r = client.post("/admin/users", json={"email": person["email"].upper(), "name": "Again"})
    assert r.status_code == 409


def test_role_change_takes_effect_at_once(client, team):
    viewer = invite(client, team, role="viewer")
    item = new_item(client, team)  # created by the admin
    them = as_user(viewer["id"])
    assert them.post(f"/items/{item['id']}/claim", json={"version": 1}).status_code == 403
    r = client.put(f"/admin/teams/{team['id']}/members/{viewer['id']}", json={"role": "member"})
    assert r.status_code == 200
    assert them.post(f"/items/{item['id']}/claim", json={"version": 1}).status_code == 200


def test_removing_member_releases_their_open_items_with_history(client, team):
    member = invite(client, team)
    them = as_user(member["id"])
    item = new_item(them, team)
    assert them.post(f"/items/{item['id']}/claim", json={"version": 1}).status_code == 200
    r = client.delete(f"/admin/teams/{team['id']}/members/{member['id']}")
    assert r.status_code == 200 and r.json()["released"] == 1
    after = client.get(f"/items/{item['id']}").json()
    assert after["assignee_id"] is None
    assert (
        after["events"][0]["kind"] == "released" and "left the team" in after["events"][0]["note"]
    )
    assert after["version"] == len(client.get(f"/items/{item['id']}/events").json()["events"])
    assert them.get(f"/items/{item['id']}").status_code == 404  # no longer on the team


def test_deactivation_ends_sessions_and_releases_work(client, team):
    member = invite(client, team)
    them = as_user(member["id"])
    item = new_item(them, team)
    assert them.post(f"/items/{item['id']}/claim", json={"version": 1}).status_code == 200
    r = client.patch(f"/admin/users/{member['id']}", json={"active": False})
    assert r.status_code == 200 and r.json()["deactivated_at"] is not None
    assert them.get("/me").status_code == 401
    assert client.get(f"/items/{item['id']}").json()["assignee_id"] is None
    roster = client.get(f"/teams/{team['id']}/members").json()["members"]
    assert member["id"] not in {m["id"] for m in roster}  # not offered for assignment
    r = client.post(f"/items/{item['id']}/assign", json={"version": 3, "assignee_id": member["id"]})
    assert r.status_code == 422


def test_admin_cannot_lock_themselves_out(client, login):
    me = login(ADMIN)
    assert client.patch(f"/admin/users/{me['id']}", json={"is_admin": False}).status_code == 422
    assert client.patch(f"/admin/users/{me['id']}", json={"active": False}).status_code == 422
    assert client.get("/me").json()["is_admin"] is True


def test_admin_actions_are_recorded_and_append_only(client, team):
    person = invite(client, team)
    log = client.get("/admin/events").json()["events"]
    assert any(e["kind"] == "invited" and e["target"]["email"] == person["email"] for e in log)
    with db.pool.connection() as conn:
        for sql in (
            "update admin_events set kind = 'x' where id = (select max(id) from admin_events)",
            "delete from admin_events where id = (select max(id) from admin_events)",
        ):
            with (
                pytest.raises(psycopg.errors.RaiseException, match="append-only"),
                conn.transaction(),
            ):
                conn.execute(sql)
