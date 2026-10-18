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
from app.items import allowed_actions
from app.notifications import notify
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


def test_a_new_team_takes_the_colour_least_used_among_the_teams_its_creator_sees(client, login):
    login(ADMIN)
    used = [t["color"] for t in client.get("/me").json()["visible_teams"]]
    least = min(range(8), key=lambda c: (used.count(c), c))
    team = client.post("/admin/teams", json={"name": f"Colour {uuid4().hex[:8]}"}).json()
    assert team["color"] == least
    # The colour travels with the team: the team list, /me, and every item on it.
    assert {t["id"]: t["color"] for t in client.get("/admin/teams").json()["teams"]}[
        team["id"]
    ] == least
    item = new_item(client, team)
    assert item["team_color"] == least


def test_admin_cannot_lock_themselves_out(client, login):
    me = login(ADMIN)
    assert client.patch(f"/admin/users/{me['id']}", json={"is_admin": False}).status_code == 422
    assert client.patch(f"/admin/users/{me['id']}", json={"active": False}).status_code == 422
    assert client.get("/me").json()["is_admin"] is True


def test_admin_log_names_the_person_and_team_a_change_was_about(client, team):
    person = invite(client, team)
    r = client.put(f"/admin/teams/{team['id']}/members/{person['id']}", json={"role": "lead"})
    assert r.status_code == 200, r.text
    log = client.get("/admin/events").json()["events"]
    mine = [e for e in log if e["kind"] == "role_set" and e["target"]["user_id"] == person["id"]]
    change = mine[0]
    assert change["target_user_name"] == "New Person" and change["target_team_name"] == team["name"]
    assert change["target"]["before"] == "member" and change["target"]["after"] == "lead"


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


def test_a_team_past_the_first_100_people_hides_nobody_from_search(client, login):
    """Rosters and the assign picker load 100 people. Anyone past that must still be findable, and
    viewers, who cannot be given work, must not use up the picker's 100."""
    login(ADMIN)
    big = client.post("/admin/teams", json={"name": f"Big {uuid4().hex[:8]}"}).json()
    tag = uuid4().hex[:8]
    with db.pool.connection() as conn, conn.transaction():
        for n in range(105):
            role = "viewer" if n < 3 else "member"
            uid = conn.execute(
                "insert into users (email, name) values (%s, %s) returning id",
                (f"big{n}-{tag}@example.com", f"Person {n:03d} {tag}"),
            ).fetchone()["id"]
            conn.execute("insert into team_members values (%s, %s, %s)", (big["id"], uid, role))
        last = conn.execute(
            "insert into users (email, name) values (%s, 'Zed 50%% Last') returning id",
            (f"last-{tag}@example.com",),
        ).fetchone()["id"]
        conn.execute("insert into team_members values (%s, %s, 'member')", (big["id"], last))

    for url in (f"/teams/{big['id']}/members", f"/admin/teams/{big['id']}/members"):
        page = client.get(url).json()
        assert page["capped"] is True and len(page["members"]) == 100
        assert last not in {m["id"] for m in page["members"]}  # sorted last, past the 100
        found = client.get(url, params={"q": "zed 50%"}).json()["members"]
        assert [m["id"] for m in found] == [str(last)]
        # % and _ are literal: a search for them alone finds only the name that has one.
        assert [m["id"] for m in client.get(url, params={"q": "%"}).json()["members"]] == [
            str(last)
        ]
        assert client.get(url, params={"q": "_"}).json()["members"] == []

    owners = client.get(f"/teams/{big['id']}/members", params={"can_own": "true"}).json()
    assert all(m["role"] != "viewer" for m in owners["members"])
    viewer = client.get(f"/teams/{big['id']}/members", params={"q": f"Person 000 {tag}"}).json()
    assert [m["role"] for m in viewer["members"]] == ["viewer"]
    assert (
        client.get(
            f"/teams/{big['id']}/members", params={"q": f"Person 000 {tag}", "can_own": "true"}
        ).json()["members"]
        == []
    )


def move(c, item, to, **extra):
    r = c.post(
        f"/items/{item['id']}/{'claim' if to == 'claim' else 'transition'}",
        json={"version": item["version"], **({} if to == "claim" else {"to": to}), **extra},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_someone_taken_off_a_team_stops_seeing_its_items_anywhere(client, team):
    """Leaving a team ends access everywhere at once: the item page already said 404, and now the
    inbox, the dashboard and notifications agree, even for an item still in their name."""
    lead_id = invite(client, team, "lead")["id"]
    lead = as_user(lead_id)
    mate_id = invite(client, team, "member")["id"]
    mate = as_user(mate_id)
    item = new_item(mate, team)
    for c, to in ((mate, "claim"), (mate, "awaiting_approval"), (lead, "approved")):
        item = move(c, item, to)
    for c, to in ((mate, "resolved"), (lead, "closed")):
        item = move(c, item, to)
    assert client.delete(f"/admin/teams/{team['id']}/members/{mate_id}").json()["released"] == 0
    item = move(lead, item, "triaged")  # reopened, still in the old member's name
    assert item["assignee_id"] == mate_id

    comment = lead.post(f"/items/{item['id']}/comments", json={"body": "after they left"}).json()
    event = comment["events"][0]
    with db.pool.connection() as conn:
        first = conn.execute(
            "select id from work_item_events where item_id = %s and seq = 1", (item["id"],)
        ).fetchone()["id"]
        conn.execute(
            "insert into notifications (user_id, event_id) values (%s, %s) on conflict do nothing",
            (mate_id, first),
        )
        notify(
            conn,
            {
                "item_id": item["id"],
                "event_id": event["id"],
                "actor_id": lead_id,
                "kind": "comment",
            },
        )
        told = conn.execute(
            "select count(*) as n from notifications where user_id = %s and event_id = %s",
            (mate_id, event["id"]),
        ).fetchone()["n"]
    assert told == 0  # not notified of anything after leaving
    seen = mate.get("/notifications").json()["notifications"]
    assert item["id"] not in {n["item_id"] for n in seen}  # nor shown the ones from before
    inbox = mate.get("/inbox").json()
    assert item["id"] not in {i["id"] for i in inbox["assigned"]["items"]}
    assert mate.get("/items", params={"view": "assigned"}).json()["items"] == []
    assert mate.get(f"/items/{item['id']}").status_code == 404


def test_a_member_made_a_viewer_hands_back_their_open_work(client, team):
    lead = as_user(invite(client, team, "lead")["id"])
    mate_id = invite(client, team, "member")["id"]
    mate = as_user(mate_id)
    item = move(mate, new_item(mate, team), "claim")
    r = client.put(f"/admin/teams/{team['id']}/members/{mate_id}", json={"role": "viewer"})
    assert r.status_code == 200 and r.json()["released"] == 1
    now = mate.get(f"/items/{item['id']}").json()
    assert now["assignee_id"] is None and now["events"][0]["kind"] == "released"
    assert now["allowed_actions"] == ["comment", "watch"]
    # And the rule itself: a viewer whose name is still on an item may not move it.
    held = {**now, "assignee_id": mate_id, "status": "triaged"}
    assert allowed_actions({"id": mate_id}, "viewer", held) == ["comment", "watch"]
    assert lead.get(f"/items/{item['id']}").status_code == 200


def test_the_shared_admin_sees_no_job_about_a_private_item(client, login):
    with db.pool.connection() as conn:
        # A visitor's workspace item, as the demo makes one.
        owner = conn.execute(
            "insert into users (email, name, private) values (%s, 'Owner', true) returning id",
            (f"owner-{uuid4().hex[:8]}@example.com",),
        ).fetchone()["id"]
        team = conn.execute(
            "insert into teams (name, key, private, color, next_number)"
            " values ('Mine', %s, true, 0, 1) returning id",
            (f"P{uuid4().hex[:6].upper()}",),
        ).fetchone()["id"]
        private = conn.execute(
            "insert into work_items (team_id, kind, title, reporter_id, number)"
            " values (%s, 'task', 'Private', %s, 1) returning id",
            (team, owner),
        ).fetchone()
        conn.execute(  # its history, as apply_change would have written it
            "insert into work_item_events (item_id, seq, actor_id, kind) values (%s, 1, %s, 'created')",
            (private["id"], owner),
        )
        job = conn.execute(
            "insert into outbox (kind, payload, status, attempts, last_error)"
            " values ('item_event', jsonb_build_object('item_id', %s::text), 'dead', 8, 'private')"
            " returning id",
            (private["id"],),
        ).fetchone()["id"]
    login(ADMIN)
    before = client.get("/dashboard").json()["failed_jobs"]
    jobs = client.get("/admin/jobs", params={"status": "dead", "limit": 100}).json()
    assert job not in {j["id"] for j in jobs["jobs"]}
    assert client.post(f"/admin/jobs/{job}/retry").status_code == 404
    with db.pool.connection() as conn:
        conn.execute("delete from outbox where id = %s", (job,))
    assert client.get("/dashboard").json()["failed_jobs"] == before  # it was never counted


def test_an_invite_or_team_with_bad_input_is_refused_not_a_500(client, team):
    twice = [{"team_id": team["id"], "role": "member"}, {"team_id": team["id"], "role": "lead"}]
    body = {"email": f"twice-{uuid4().hex[:8]}@example.com", "name": "Twice", "teams": twice}
    assert client.post("/admin/users", json=body).status_code == 422
    assert client.post("/admin/teams", json={"name": "   "}).status_code == 422
    body = {"email": f"blank-{uuid4().hex[:8]}@example.com", "name": "  "}
    assert client.post("/admin/users", json=body).status_code == 422
