"""Seeded facts these tests lean on: ben.okafor leads Compliance, views Risk and is on no other team;
dev.mehta is a member of Risk."""

from uuid import UUID

import pytest

from app import db
from app.items import Conflict, apply_change, list_query


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
    assert client.post("/items", json=body).status_code == 403
    body["team_id"] = str(team_id("Payments"))
    assert client.post("/items", json=body).status_code == 404


def test_every_mutation_writes_exactly_one_event(client, login, team_id):
    me = login("dev.mehta@newtonite.dev")
    r = client.post("/items", json={"team_id": str(team_id("Risk")), "kind": "incident", "title": "Queue backed up"})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["version"] == 1 and item["reporter_id"] == me["id"] and "claim" in item["allowed_actions"]
    with db.pool.connection() as conn:
        events = conn.execute("select kind, after from work_item_events where item_id = %s", (item["id"],)).fetchall()
    assert len(events) == 1 and events[0]["kind"] == "created" and events[0]["after"]["title"] == "Queue backed up"


def test_stale_version_rejected_with_current_state(login):
    actor = login("dev.mehta@newtonite.dev")
    item = one_item("Risk", status="new")
    with db.pool.connection() as conn, conn.transaction():
        with pytest.raises(Conflict) as exc:
            apply_change(conn, actor, "priority", {"priority": 0}, item["id"], expected_version=item["version"] + 7)
        assert exc.value.item["version"] == item["version"]
        count = conn.execute("select count(*) from work_item_events where item_id = %s", (item["id"],)).fetchone()["count"]
        assert count == item["version"]  # the refused write left no event behind


def test_list_pages_by_cursor_without_overlap(client, login):
    login("ben.okafor@newtonite.dev")
    first = client.get("/items", params={"status": "in_progress", "limit": 5}).json()
    assert first["capped"] is True and len(first["items"]) == 5
    second = client.get("/items", params={"status": "in_progress", "limit": 5, "cursor": first["next_cursor"]}).json()
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
