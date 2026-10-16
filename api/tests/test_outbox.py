"""The outbox and its worker. The compose stack runs a real worker against the same database, so these
tests use outbox kinds of their own, which that worker ignores, wherever the test needs to drive the
rows itself."""

import time
from uuid import uuid4

from psycopg.types.json import Jsonb

from app import db, main, outbox
from app.notifications import notify
from app.outbox import MAX_ATTEMPTS, claim_batch, process_batch


def enqueue(kind: str, n: int = 1) -> list[int]:
    with db.pool.connection() as conn:
        return [
            conn.execute(
                "insert into outbox (kind, payload) values (%s, %s) returning id", (kind, Jsonb({}))
            ).fetchone()["id"]
            for _ in range(n)
        ]


def outbox_row(rid: int) -> dict:
    with db.pool.connection() as conn:
        return conn.execute("select * from outbox where id = %s", (rid,)).fetchone()


def make_item(client, team_id, title):
    r = client.post(
        "/items",
        json={"team_id": str(team_id), "kind": "task", "title": title},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return r.json()


def notifications_for(user_id, event_seq, item_id) -> list[dict]:
    with db.pool.connection() as conn:
        return conn.execute(
            "select n.* from notifications n join work_item_events e on e.id = n.event_id"
            " where n.user_id = %s and e.item_id = %s and e.seq = %s",
            (user_id, item_id, event_seq),
        ).fetchall()


def test_every_change_enqueues_one_outbox_row(client, login, team_id):
    login("dev.mehta@newtonite.dev")
    item = make_item(client, team_id("Risk"), "outbox check")
    client.post(f"/items/{item['id']}/comments", json={"body": "and a comment"})
    with db.pool.connection() as conn:
        kinds = [
            r["payload"]["kind"]
            for r in conn.execute(
                "select payload from outbox where kind = 'item_event' and payload->>'item_id' = %s"
                " order by id",
                (item["id"],),
            )
        ]
    assert kinds == ["created", "comment"]


def test_outbox_handler_twice_one_notification(client, login, team_id):
    dev = login("dev.mehta@newtonite.dev")
    item = make_item(client, team_id("Risk"), "watched item")
    ben = login("ben.okafor@newtonite.dev")
    assert client.post(f"/items/{item['id']}/watch").status_code == 204
    finn = login("finn.okafor@newtonite.dev")
    r = client.post(f"/items/{item['id']}/comments", json={"body": "lead here"})
    event_id = r.json()["events"][0]["id"]
    payload = {
        "item_id": item["id"],
        "event_id": event_id,
        "actor_id": finn["id"],
        "kind": "comment",
    }
    with db.pool.connection() as conn:
        notify(conn, payload)
        notify(conn, payload)
        who = {
            str(n["user_id"])
            for n in conn.execute(
                "select user_id from notifications where event_id = %s", (event_id,)
            )
        }
    assert who == {dev["id"], ben["id"]}  # reporter and watcher once each, never the actor


def test_worker_delivers_to_watcher_and_the_list_shows_it(client, login, team_id):
    dev = login("dev.mehta@newtonite.dev")
    item = make_item(client, team_id("Risk"), "end to end notification")
    login("finn.okafor@newtonite.dev")
    r = client.post(f"/items/{item['id']}/comments", json={"body": "ping"})
    seq = r.json()["events"][0]["seq"]
    for _ in range(50):  # the compose worker or this call: whichever gets the row first
        with db.pool.connection() as conn:
            process_batch(conn)
        if notifications_for(dev["id"], seq, item["id"]):
            break
        time.sleep(0.1)
    assert len(notifications_for(dev["id"], seq, item["id"])) == 1
    login("dev.mehta@newtonite.dev")
    unread = client.get("/notifications", params={"unread": True}).json()
    mine = [n for n in unread["notifications"] if n["item_id"] == item["id"]]
    assert mine and mine[0]["actor_name"] == "Finn Okafor" and mine[0]["note"] == "ping"
    assert mine[0]["kind"] == "comment" and "before" in mine[0] and "after" in mine[0]
    assert client.post(f"/notifications/{mine[0]['id']}/read").status_code == 204
    unread = client.get("/notifications", params={"unread": True}).json()
    assert not [n for n in unread["notifications"] if n["id"] == mine[0]["id"]]


def test_outbox_dead_after_max_attempts():
    kind = f"test_explode_{uuid4().hex[:8]}"
    (rid,) = enqueue(kind)
    marker = str(uuid4())

    def explode(conn, payload):
        # A partial write, then a failure: the savepoint must roll the write back.
        conn.execute(
            "insert into idempotency_keys (key, user_id, request_hash, status, body)"
            " select %s, id, 'x', 200, '{}' from users limit 1",
            (marker,),
        )
        raise RuntimeError("handler blew up")

    for _ in range(MAX_ATTEMPTS + 2):
        with db.pool.connection() as conn:
            conn.execute("update outbox set next_attempt_at = now() where id = %s", (rid,))
            process_batch(conn, {kind: explode})
    row = outbox_row(rid)
    assert row["status"] == "dead" and row["attempts"] == MAX_ATTEMPTS
    assert "RuntimeError: handler blew up" in row["last_error"]
    with db.pool.connection() as conn:
        leaked = conn.execute("select count(*) from idempotency_keys where key = %s", (marker,))
        assert leaked.fetchone()["count"] == 0
        conn.execute("delete from outbox where id = %s", (rid,))  # leave no poison behind


class Died(Exception):
    """Stands in for a worker process that stops before it commits."""


def test_two_workers_skip_locked_no_double_processing():
    kind = f"test_hold_{uuid4().hex[:8]}"
    ids = enqueue(kind, 4)
    with db.pool.connection() as one, db.pool.connection() as two, two.transaction():
        try:
            with one.transaction():
                first = {r["id"] for r in claim_batch(one, [kind], 2)}
                second = {r["id"] for r in claim_batch(two, [kind], 2)}
                assert len(first) == len(second) == 2 and not (first & second)
                assert first | second == set(ids)
                raise Died  # the first worker dies mid-batch: its rows become claimable again
        except Died:
            pass
        with one.transaction():
            assert {r["id"] for r in claim_batch(one, [kind], 4)} == set(ids) - second
    with db.pool.connection() as conn:
        conn.execute("delete from outbox where kind = %s", (kind,))


def test_outbox_rows_survive_a_crash_mid_batch():
    kind = f"test_crash_{uuid4().hex[:8]}"
    ids = enqueue(kind, 2)
    handled = []
    with db.pool.connection() as conn:
        try:
            with conn.transaction():
                assert len(claim_batch(conn, [kind])) == 2
                raise Died  # the process dies before it commits
        except Died:
            pass
        assert process_batch(conn, {kind: lambda c, p: handled.append(p)}) == 2
    assert len(handled) == 2
    assert {outbox_row(i)["status"] for i in ids} == {"done"}
    with db.pool.connection() as conn:
        conn.execute("delete from outbox where kind = %s", (kind,))


def test_api_drains_the_outbox_when_no_worker_runs(client, monkeypatch):
    """Where no worker process exists (Vercel), a request's response triggers one drain."""
    kind = f"test_drain_{uuid4().hex[:8]}"
    (rid,) = enqueue(kind)
    handled = []
    monkeypatch.setattr(outbox, "HANDLERS", {kind: lambda conn, payload: handled.append(payload)})
    monkeypatch.setattr(main, "DRAIN_IN_REQUESTS", True)
    monkeypatch.setattr(main, "_last_drain", 0.0)
    assert client.get("/health").status_code == 200  # the drain runs after this response is sent
    assert handled == [{}] and outbox_row(rid)["status"] == "done"
    with db.pool.connection() as conn:
        conn.execute("delete from outbox where kind = %s", (kind,))


def test_api_does_not_drain_when_a_worker_runs(client, monkeypatch):
    kind = f"test_nodrain_{uuid4().hex[:8]}"
    (rid,) = enqueue(kind)
    monkeypatch.setattr(outbox, "HANDLERS", {kind: lambda conn, payload: None})
    monkeypatch.setattr(main, "DRAIN_IN_REQUESTS", False)
    monkeypatch.setattr(main, "_last_drain", 0.0)
    client.get("/health")
    assert outbox_row(rid)["status"] == "pending"
    with db.pool.connection() as conn:
        conn.execute("delete from outbox where kind = %s", (kind,))


def test_scheduled_sweep_needs_the_secret(client, monkeypatch):
    monkeypatch.setattr(main, "CRON_SECRET", "s3cret")
    assert client.get("/internal/drain").status_code == 404
    assert (
        client.get("/internal/drain", headers={"Authorization": "Bearer wrong"}).status_code == 404
    )
    r = client.get("/internal/drain", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and "processed" in r.json()
    monkeypatch.setattr(main, "CRON_SECRET", "")
    assert client.get("/internal/drain", headers={"Authorization": "Bearer "}).status_code == 404
