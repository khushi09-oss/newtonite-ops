"""Seeded facts these tests lean on: ben.okafor leads Compliance, views Risk and is on no other team;
finn.okafor leads Risk; dev.mehta is a member of Risk; ava.okafor is the global admin."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from conftest import session_cookie
from fastapi.testclient import TestClient

from app import db
from app.items import Conflict, apply_change, list_query
from app.main import app


def one_item(team: str, **where):
    cond = "".join(f" and w.{k} = %s" for k in where)
    with db.pool.connection() as conn:
        return conn.execute(
            f"select w.* from work_items w join teams t on t.id = w.team_id where t.name = %s{cond} limit 1",
            [team, *where.values()],
        ).fetchone()


def explain(sql, params):
    with db.pool.connection() as conn:
        return "\n".join(r["QUERY PLAN"] for r in conn.execute("explain " + sql, params).fetchall())


def event_count(item_id) -> int:
    with db.pool.connection() as conn:
        return conn.execute(
            "select count(*) from work_item_events where item_id = %s", (item_id,)
        ).fetchone()["count"]


def new_item(client, team_id, title="Queue backed up"):
    r = client.post(
        "/items",
        json={"team_id": str(team_id), "kind": "incident", "title": title},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_nonmember_gets_404_not_403(client, login):
    login("ben.okafor@newtonite.dev")
    assert client.get(f"/items/{one_item('Payments')['id']}").status_code == 404
    r = client.get(f"/items/{one_item('Compliance')['id']}")
    assert r.status_code == 200
    assert r.json()["role"] == "lead" and "comment" in r.json()["allowed_actions"]


def test_viewer_sees_but_cannot_create(client, login, team_id):
    login("ben.okafor@newtonite.dev")
    assert client.get(f"/items/{one_item('Risk')['id']}").status_code == 200
    body = {"team_id": str(team_id("Risk")), "kind": "task", "title": "viewer attempt"}
    key = {"Idempotency-Key": str(uuid4())}
    assert client.post("/items", json=body, headers=key).status_code == 403
    body["team_id"] = str(team_id("Payments"))
    assert client.post("/items", json=body, headers=key).status_code == 404


def as_user(user_id) -> TestClient:
    c = TestClient(app)
    c.cookies.set("session", session_cookie(user_id))
    return c


def check_offered_teams(c, label: str) -> None:
    """What /me offers this person must be exactly what the server lets them do."""
    with db.pool.connection() as conn:
        public = {r["id"] for r in conn.execute("select id from teams where not private")}
        private = {r["id"] for r in conn.execute("select id from teams where private")}
    me = c.get("/me").json()
    seen = {UUID(t["id"]): t for t in me["visible_teams"]}
    mine = {UUID(t["id"]) for t in me["teams"]}
    assert mine <= set(seen), label  # every team you are on is offered
    assert set(seen) == (public | (mine & private) if me["is_admin"] else mine), label
    # One team of each role they hold, plus one they cannot see: creating there must answer
    # exactly what /me promised (201 may create, 403 sees but may not, 404 cannot see).
    picks = {t["role"]: tid for tid, t in seen.items()}
    unseen = sorted((public | private) - set(seen))
    for tid in [*picks.values(), *unseen[:1]]:
        r = c.post(
            "/items",
            json={"team_id": str(tid), "kind": "task", "title": "Offered team check"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        expected = 404 if tid not in seen else 201 if seen[tid]["can_create"] else 403
        assert r.status_code == expected, (label, seen.get(tid), r.text)


def test_every_team_the_screens_offer_matches_what_the_server_allows(client, login, monkeypatch):
    """The screens build every team list from /me's visible_teams. The rule is checked for every kind
    of account, the shared demo people and brand-new ones made the ways real people arrive."""
    from app import demo

    # A new Google visitor on the demo: nothing at first, then their own workspace team.
    monkeypatch.setattr(demo, "DEMO_MODE", True)
    with db.pool.connection() as conn:
        uid = conn.execute(
            "insert into users (email, name, private) values (%s, 'New visitor', true) returning id",
            (f"visitor-{uuid4().hex[:8]}@gmail.com",),
        ).fetchone()["id"]
    visitor = as_user(uid)
    check_offered_teams(visitor, "new visitor, no teams yet")
    assert visitor.post("/teams", json={"name": "Visitor ops"}).status_code == 201
    check_offered_teams(visitor, "new visitor with a workspace team")
    monkeypatch.setattr(demo, "DEMO_MODE", False)

    # People an admin invites: a member, a viewer, and a new admin who is on no team at all.
    login("ava.okafor@newtonite.dev")
    with db.pool.connection() as conn:
        risk, cmp = (
            conn.execute("select id from teams where name = %s", (n,)).fetchone()["id"]
            for n in ("Risk", "Compliance")
        )
    invited = []
    for label, body in [
        ("new member", {"teams": [{"team_id": str(risk), "role": "member"}]}),
        ("new viewer", {"teams": [{"team_id": str(cmp), "role": "viewer"}]}),
        ("new admin on no team", {"is_admin": True}),
    ]:
        email = f"new-{uuid4().hex[:8]}@example.com"
        r = client.post("/admin/users", json={"email": email, "name": "New person", **body})
        assert r.status_code == 201, r.text
        invited.append((label, r.json()["id"]))
    for label, user_id in invited:
        check_offered_teams(as_user(user_id), label)

    # And the shared demo people.
    for person in ["ava.okafor", "finn.okafor", "dev.mehta", "ben.okafor"]:
        login(f"{person}@newtonite.dev")
        check_offered_teams(client, person)


def test_viewer_cannot_claim(client, login):
    login("ben.okafor@newtonite.dev")
    item = one_item("Risk", status="new")
    r = client.post(f"/items/{item['id']}/claim", json={"version": item["version"]})
    assert r.status_code == 403 and "claim" not in r.json()["detail"]["allowed"]


def test_every_mutation_writes_exactly_one_event(client, login, team_id):
    """One item walked through its whole life. After every call, version == number of events."""
    dev = login("dev.mehta@newtonite.dev")
    item = new_item(client, team_id("Risk"))
    iid = item["id"]
    assert item["version"] == 1 and event_count(iid) == 1 and "claim" in item["allowed_actions"]

    def step(method, path, body, expect=200):
        r = client.request(method, f"/items/{iid}{path}", json=body)
        assert r.status_code == expect, r.text
        got = r.json()
        assert got["version"] == event_count(iid), (path, got["version"], event_count(iid))
        return got

    item = step("POST", "/claim", {"version": 1})
    assert item["status"] == "in_progress" and item["assignee_id"] == dev["id"]
    item = step("PATCH", "", {"version": 2, "title": "Queue backed up badly", "priority": 0})
    assert item["title"] == "Queue backed up badly" and item["priority"] == 0
    item = step("POST", "/transition", {"version": 3, "to": "awaiting_approval"})
    assert item["approval_requested_by"] == dev["id"]
    item = step("POST", "/comments", {"body": "Ready for review."}, 201)
    assert (
        item["events"][0]["kind"] == "comment" and item["events"][0]["note"] == "Ready for review."
    )

    login("finn.okafor@newtonite.dev")
    item = step("POST", "/transition", {"version": 5, "to": "approved"})
    assert item["approval_requested_by"] is None
    login("dev.mehta@newtonite.dev")
    item = step("POST", "/transition", {"version": 6, "to": "resolved"})
    finn = login("finn.okafor@newtonite.dev")
    item = step("POST", "/transition", {"version": 7, "to": "closed"})
    assert item["allowed_actions"] == ["comment", "watch", "transition:triaged"]
    item = step("POST", "/transition", {"version": 8, "to": "triaged"})
    item = step("POST", "/release", {"version": 9})
    assert item["assignee_id"] is None
    item = step("POST", "/assign", {"version": 10, "assignee_id": finn["id"]})
    assert item["assignee_name"] == "Finn Okafor" and item["version"] == 11

    with db.pool.connection() as conn:
        kinds = [
            r["kind"]
            for r in conn.execute(
                "select kind from work_item_events where item_id = %s order by seq", (iid,)
            )
        ]
    assert kinds == [
        "created",
        "claimed",
        "edited",
        "status",
        "comment",
        "status",
        "status",
        "status",
        "status",
        "released",
        "assigned",
    ]


def test_stale_version_rejected_with_current_state(client, login, team_id):
    dev = login("dev.mehta@newtonite.dev")
    item = new_item(client, team_id("Risk"), "Stale edit target")
    client.post(f"/items/{item['id']}/comments", json={"body": "someone else was here"})
    r = client.patch(f"/items/{item['id']}", json={"version": 1, "title": "from a stale tab"})
    assert r.status_code == 409
    body = r.json()
    assert body["current"]["version"] == 2 and body["current"]["title"] == "Stale edit target"
    assert body["events"][0]["kind"] == "comment" and body["events"][0]["actor_name"] == dev["name"]
    assert event_count(item["id"]) == 2  # the refused write left no event behind
    # And the same guarantee below the HTTP layer, straight from apply_change.
    with db.pool.connection() as conn, conn.transaction(), pytest.raises(Conflict) as exc:
        apply_change(conn, dev, "edited", {"priority": 0}, UUID(item["id"]), expected_version=1)
    assert exc.value.item["version"] == 2


def test_claim_race_exactly_one_winner(login, team_id):
    with db.pool.connection() as conn:
        racers = conn.execute(
            "select u.id, u.name from team_members m join users u on u.id = m.user_id"
            " where m.team_id = %s and m.role <> 'viewer'",
            (team_id("Risk"),),
        ).fetchall()
    clients = []
    for n in range(20):
        c = TestClient(app)
        c.cookies.set("session", session_cookie(racers[n % len(racers)]["id"]))
        clients.append(c)
    item = new_item(clients[0], team_id("Risk"), "Everyone wants this one")

    def claim(c):
        return c.post(f"/items/{item['id']}/claim", json={"version": 1})

    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(claim, clients))
    codes = sorted(r.status_code for r in results)
    assert codes == [200] + [409] * 19, codes
    winner = next(r for r in results if r.status_code == 200).json()
    for r in results:
        if r.status_code == 409:
            assert r.json()["current"]["assignee_id"] == winner["assignee_id"]
            assert r.json()["events"][0]["kind"] == "claimed"
    assert event_count(item["id"]) == 2 and winner["version"] == 2


def test_transition_table_rejects_unlisted_and_tells_allowed(client, login, team_id):
    login("finn.okafor@newtonite.dev")
    item = new_item(client, team_id("Risk"), "Cannot skip ahead")
    r = client.post(f"/items/{item['id']}/transition", json={"version": 1, "to": "resolved"})
    assert r.status_code == 422 and r.json()["detail"]["allowed"] == ["triaged"]
    r = client.post(f"/items/{item['id']}/transition", json={"version": 1, "to": "sideways"})
    assert r.status_code == 422  # not even a status
    assert event_count(item["id"]) == 1


def test_approver_cannot_be_requester(client, login, team_id):
    login("finn.okafor@newtonite.dev")
    item = new_item(client, team_id("Risk"), "Lead asks for approval")
    iid = item["id"]
    assert client.post(f"/items/{iid}/claim", json={"version": 1}).status_code == 200
    r = client.post(f"/items/{iid}/transition", json={"version": 2, "to": "awaiting_approval"})
    assert r.status_code == 200 and "transition:approved" not in r.json()["allowed_actions"]
    r = client.post(f"/items/{iid}/transition", json={"version": 3, "to": "approved"})
    assert r.status_code == 422 and r.json()["detail"]["allowed"] == ["triaged"]
    r = client.post(f"/items/{iid}/transition", json={"version": 3, "to": "triaged"})
    assert r.status_code == 422  # rejecting needs a note
    login("ava.okafor@newtonite.dev")
    r = client.post(f"/items/{iid}/transition", json={"version": 3, "to": "approved"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_events_page_by_seq_without_overlap(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    item = new_item(client, team_id("Risk"), "Chatty item")
    for n in range(6):
        client.post(f"/items/{item['id']}/comments", json={"body": f"note {n}"})
    first = client.get(f"/items/{item['id']}/events", params={"limit": 4}).json()
    assert first["capped"] is True and [e["seq"] for e in first["events"]] == [7, 6, 5, 4]
    rest = client.get(f"/items/{item['id']}/events", params={"limit": 4, "before_seq": 4}).json()
    assert rest["capped"] is False and [e["seq"] for e in rest["events"]] == [3, 2, 1]


def test_events_table_rejects_update_and_delete():
    with db.pool.connection() as conn:
        for sql in (
            "update work_item_events set note = 'x' where id = (select min(id) from work_item_events)",
            "delete from work_item_events where id = (select min(id) from work_item_events)",
        ):
            with (
                pytest.raises(psycopg.errors.RaiseException, match="append-only"),
                conn.transaction(),
            ):
                conn.execute(sql)


def test_list_pages_by_cursor_without_overlap(client, login):
    login("ben.okafor@newtonite.dev")
    first = client.get("/items", params={"status": "in_progress", "limit": 5}).json()
    assert first["capped"] is True and len(first["items"]) == 5
    second = client.get(
        "/items", params={"status": "in_progress", "limit": 5, "cursor": first["next_cursor"]}
    ).json()
    both = first["items"] + second["items"]
    assert len({i["id"] for i in both}) == 10
    stamps = [(i["updated_at"], i["id"]) for i in both]
    assert stamps == sorted(stamps, reverse=True)
    assert client.get("/items", params={"cursor": "nonsense"}).status_code == 422


def test_list_uses_index(login, team_id):
    user = login("ben.okafor@newtonite.dev")
    user["id"] = UUID(user["id"])
    sql, params = list_query(user, team=team_id("Compliance"), status="in_progress", priority=1)
    plan = explain(sql, params)
    assert "Seq Scan on work_items" not in plan, plan
    sql, params = list_query(user, q="refund")
    plan = explain(sql, params)
    assert "Seq Scan on work_items" not in plan, plan


def test_status_counts_match_the_list_and_stay_inside_my_teams(client, login, team_id):
    login("ben.okafor@newtonite.dev")  # leads Compliance, views Risk
    title = f"Counted once {uuid4()}"
    r = client.post(
        "/items",
        json={"team_id": str(team_id("Compliance")), "kind": "task", "title": title},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    counts = client.get("/items/counts", params={"q": f'"{title}"'}).json()["counts"]
    assert counts == {s: (1 if s == "new" else 0) for s in counts} and len(counts) == 7
    outside = client.get("/items/counts", params={"team": str(team_id("Payments"))}).json()
    assert set(outside["counts"].values()) == {0}
    mine = sum(client.get("/items/counts").json()["counts"].values())
    with db.pool.connection() as conn:
        expected = conn.execute(
            "select count(*) as n from work_items where team_id = any(%s)",
            ([team_id("Compliance"), team_id("Risk")],),
        ).fetchone()["n"]
    assert mine == expected


def test_team_members_hidden_from_outsiders(client, login, team_id):
    login("ben.okafor@newtonite.dev")
    assert client.get(f"/teams/{team_id('Payments')}/members").status_code == 404
    risk = client.get(f"/teams/{team_id('Risk')}/members")
    assert risk.status_code == 200
    assert risk.json()["capped"] is False
    assert ("Finn Okafor", "lead") in {(m["name"], m["role"]) for m in risk.json()["members"]}


def test_item_says_whether_i_watch_it(client, login):
    login("ben.okafor@newtonite.dev")
    iid = one_item("Risk")["id"]
    assert client.get(f"/items/{iid}").json()["watching"] is False
    assert client.post(f"/items/{iid}/watch").status_code == 204
    assert client.get(f"/items/{iid}").json()["watching"] is True
    assert client.delete(f"/items/{iid}/watch").status_code == 204
    assert client.get(f"/items/{iid}").json()["watching"] is False
