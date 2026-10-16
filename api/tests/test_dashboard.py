"""The dashboard and item keys. Seeded facts: ben.okafor leads Compliance and views Risk; dev.mehta is
a member of Risk; finn.okafor leads Risk."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app import dashboard, db


def create(client, team_id, title, priority=2):
    r = client.post(
        "/items",
        json={"team_id": str(team_id), "kind": "task", "title": title, "priority": priority},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return r.json()


def counts(client):
    return client.get("/dashboard").json()["counts"]


def test_dashboard_is_scoped_to_my_teams(client, login):
    login("ben.okafor@newtonite.dev")
    body = client.get("/dashboard").json()
    assert {t["team"] for t in body["by_team"]} == {"Compliance", "Risk"}
    assert body["recent"] and {e["team"] for e in body["recent"]} <= {"Compliance", "Risk"}
    assert len(body["trend"]) == dashboard.TREND_DAYS
    assert set(body["counts"]) == {"assigned", "approvals", "urgent", "overdue", "stale"}


def test_dashboard_counts_follow_the_work(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    before = counts(client)
    item = create(client, team_id("Risk"), f"Dashboard urgent {uuid4()}", priority=0)
    assert counts(client)["urgent"] == before["urgent"] + 1

    assert client.post(f"/items/{item['id']}/claim", json={"version": 1}).status_code == 200
    after_claim = counts(client)
    assert after_claim["urgent"] == before["urgent"]  # claimed, so no longer unclaimed
    assert after_claim["assigned"] == before["assigned"] + 1

    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    r = client.patch(f"/items/{item['id']}", json={"version": 2, "due_at": yesterday})
    assert r.status_code == 200, r.text
    assert counts(client)["overdue"] == before["overdue"] + 1


def test_dashboard_trend_counts_today(client, login, team_id):
    def today():
        return client.get("/dashboard").json()["trend"][-1]

    def move(iid, version, to):
        r = client.post(f"/items/{iid}/transition", json={"version": version, "to": to})
        assert r.status_code == 200, r.text

    login("finn.okafor@newtonite.dev")
    start = today()
    item = create(client, team_id("Risk"), f"Dashboard trend {uuid4()}")
    assert today()["created"] == start["created"] + 1
    assert client.post(f"/items/{item['id']}/claim", json={"version": 1}).status_code == 200
    move(item["id"], 2, "awaiting_approval")
    login("ava.okafor@newtonite.dev")  # an approver who did not ask for approval
    move(item["id"], 3, "approved")
    login("finn.okafor@newtonite.dev")
    move(item["id"], 4, "resolved")
    assert today()["resolved"] == start["resolved"] + 1


def test_dashboard_queries_use_indexes(login):
    user = login("ben.okafor@newtonite.dev")
    user["id"] = UUID(user["id"])
    with db.pool.connection() as conn:
        teams, lead_of = dashboard.my_teams(conn, user)
        for name, (sql, params) in dashboard.queries(user, teams, lead_of).items():
            plan = "\n".join(r["QUERY PLAN"] for r in conn.execute("explain " + sql, params))
            for table in ("work_items", "work_item_events"):
                assert f"Seq Scan on {table}" not in plan, (name, plan)


def test_search_by_key_finds_the_item(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    item = create(client, team_id("Risk"), f"Find me by key {uuid4()}")
    assert isinstance(item["number"], int)
    for q in (f"OPS-{item['number']}", f"ops-{item['number']}", str(item["number"])):
        found = client.get("/items", params={"q": q}).json()["items"]
        assert [i["id"] for i in found] == [item["id"]], q
