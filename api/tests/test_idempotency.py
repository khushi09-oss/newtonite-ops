from uuid import uuid4

from app import db


def count_events(item_id) -> int:
    with db.pool.connection() as conn:
        return conn.execute(
            "select count(*) from work_item_events where item_id = %s", (item_id,)
        ).fetchone()["count"]


def test_create_requires_idempotency_key(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    body = {"team_id": str(team_id("Risk")), "kind": "task", "title": "no key"}
    assert client.post("/items", json=body).status_code == 422


def test_idempotency_same_key_same_response(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    key, title = str(uuid4()), f"double click {uuid4()}"
    body = {"team_id": str(team_id("Risk")), "kind": "task", "title": title}
    first = client.post("/items", json=body, headers={"Idempotency-Key": key})
    again = client.post("/items", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    with db.pool.connection() as conn:
        n = conn.execute("select count(*) from work_items where title = %s", (title,)).fetchone()
    assert n["count"] == 1

    # The same for a comment: one event, identical responses, even though comments carry no version.
    iid = first.json()["id"]
    key = str(uuid4())
    a = client.post(
        f"/items/{iid}/comments", json={"body": "once"}, headers={"Idempotency-Key": key}
    )
    b = client.post(
        f"/items/{iid}/comments", json={"body": "once"}, headers={"Idempotency-Key": key}
    )
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    assert count_events(iid) == 2


def test_idempotency_different_body_rejected(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    body = {"team_id": str(team_id("Risk")), "kind": "task", "title": f"reused key {uuid4()}"}
    item = client.post("/items", json=body, headers={"Idempotency-Key": str(uuid4())}).json()
    key = str(uuid4())
    r = client.post(
        f"/items/{item['id']}/comments", json={"body": "first"}, headers={"Idempotency-Key": key}
    )
    assert r.status_code == 201
    r = client.post(
        f"/items/{item['id']}/comments", json={"body": "second"}, headers={"Idempotency-Key": key}
    )
    assert r.status_code == 422
    assert count_events(item["id"]) == 2


def test_idempotency_does_not_store_failures(client, login, team_id):
    """A refused request must not poison the key: the retry after fixing the problem has to run."""
    login("finn.okafor@newtonite.dev")
    body = {"team_id": str(team_id("Risk")), "kind": "task", "title": f"retry after 422 {uuid4()}"}
    item = client.post("/items", json=body, headers={"Idempotency-Key": str(uuid4())}).json()
    key = str(uuid4())
    r = client.post(
        f"/items/{item['id']}/transition",
        json={"version": 1, "to": "closed"},
        headers={"Idempotency-Key": key},
    )
    assert r.status_code == 422
    r = client.post(
        f"/items/{item['id']}/transition",
        json={"version": 1, "to": "triaged"},
        headers={"Idempotency-Key": key},
    )
    assert r.status_code == 200 and r.json()["status"] == "triaged"
