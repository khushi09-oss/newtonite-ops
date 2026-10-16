"""Seeded facts: finn.okafor leads Risk, dev.mehta is a member of Risk, ben.okafor views Risk."""

from uuid import UUID, uuid4

from app import db
from app.inbox import BUCKET_CAP, inbox_queries


def create(client, team_id, title, priority):
    r = client.post(
        "/items",
        json={"team_id": str(team_id), "kind": "incident", "title": title, "priority": priority},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return r.json()


def ids(bucket) -> set:
    return {i["id"] for i in bucket["items"]}


def test_inbox_buckets_follow_the_item_through_its_life(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    item = create(client, team_id("Risk"), f"p0 nobody owns {uuid4()}", 0)
    iid = item["id"]

    # Unassigned and urgent: visible to everyone on the team, including the viewer.
    assert iid in ids(client.get("/inbox").json()["unassigned_urgent"])
    login("ben.okafor@newtonite.dev")
    assert iid in ids(client.get("/inbox").json()["unassigned_urgent"])
    login("finn.okafor@newtonite.dev")
    finn_inbox = client.get("/inbox").json()
    assert iid in ids(finn_inbox["unassigned_urgent"]) and iid not in ids(finn_inbox["approvals"])

    # Claimed: leaves the urgent bucket, enters the assignee's own.
    login("dev.mehta@newtonite.dev")
    assert client.post(f"/items/{iid}/claim", json={"version": 1}).status_code == 200
    mine = client.get("/inbox").json()
    assert iid in ids(mine["assigned"]) and iid not in ids(mine["unassigned_urgent"])

    # Awaiting approval: in the lead's approvals, never in the requester's.
    r = client.post(f"/items/{iid}/transition", json={"version": 2, "to": "awaiting_approval"})
    assert r.status_code == 200
    assert iid not in ids(client.get("/inbox").json()["approvals"])
    login("finn.okafor@newtonite.dev")
    assert iid in ids(client.get("/inbox").json()["approvals"])
    login("ben.okafor@newtonite.dev")
    assert iid not in ids(client.get("/inbox").json()["approvals"])  # a viewer approves nothing

    # Closed: gone from every bucket, even for the assignee.
    login("finn.okafor@newtonite.dev")
    for version, to in ((3, "approved"), (4, "resolved"), (5, "closed")):
        r = client.post(f"/items/{iid}/transition", json={"version": version, "to": to})
        assert r.status_code == 200, r.text
    login("dev.mehta@newtonite.dev")
    assert all(iid not in ids(b) for b in client.get("/inbox").json().values())


def test_inbox_stale_bucket_is_capped_and_scoped(client, login):
    login("finn.okafor@newtonite.dev")
    my_teams = {t["name"] for t in client.get("/me").json()["teams"]}
    stale = client.get("/inbox").json()["stale"]
    assert stale["capped"] is True and len(stale["items"]) == BUCKET_CAP
    assert {i["team_name"] for i in stale["items"]} <= my_teams
    assert all(i["status"] not in ("resolved", "closed") for i in stale["items"])
    stamps = [i["last_activity_at"] for i in stale["items"]]
    assert stamps == sorted(stamps)  # most neglected first


def test_inbox_queries_use_indexes(login):
    user = login("finn.okafor@newtonite.dev")
    user["id"] = UUID(user["id"])
    with db.pool.connection() as conn:
        for name, (sql, params) in inbox_queries(conn, user).items():
            plan = "\n".join(r["QUERY PLAN"] for r in conn.execute("explain " + sql, params))
            assert "Seq Scan on work_items" not in plan, (name, plan)
