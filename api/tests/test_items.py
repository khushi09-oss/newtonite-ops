"""Seeded facts these tests lean on: ben.okafor leads Compliance, views Risk and is on no other team;
finn.okafor leads Risk; dev.mehta is a member of Risk; ava.okafor is the global admin."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import signer
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
        c.cookies.set("session", signer.dumps(str(racers[n % len(racers)]["id"])))
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
