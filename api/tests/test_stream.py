"""Live updates. The test client delivers a streamed body when the stream ends, so each test makes
the stream short and changes things from a background thread while it is open."""

import json
import threading
import time
from uuid import uuid4

import pytest

from app import db, stream


@pytest.fixture(autouse=True)
def quick_stream(monkeypatch):
    monkeypatch.setattr(stream, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(stream, "STREAM_SECONDS", 1.5)


def read_events(response) -> list[tuple[str, dict]]:
    out, name = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            out.append((name, json.loads(line[6:])))
            name = None
    return out


def later(seconds, fn):
    t = threading.Timer(seconds, fn)
    t.start()
    return t


def new_item(client, team_id):
    r = client.post(
        "/items",
        json={"team_id": str(team_id("Risk")), "kind": "task", "title": f"live {uuid4()}"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_stream_pushes_item_and_notification_changes(client, login, team_id):
    dev = login("dev.mehta@newtonite.dev")
    item = new_item(client, team_id)

    def someone_else_comments():
        from app.items import apply_change

        with db.pool.connection() as conn, conn.transaction():
            finn = conn.execute(
                "select id, email, name, is_admin from users where email = 'finn.okafor@newtonite.dev'"
            ).fetchone()
            apply_change(conn, finn, "comment", {}, item["id"], note="while you watch")
            event_id = conn.execute(
                "select max(id) as id from work_item_events where item_id = %s", (item["id"],)
            ).fetchone()["id"]
            conn.execute(
                "insert into notifications (user_id, event_id) values (%s, %s) on conflict do nothing",
                (dev["id"], event_id),
            )

    timer = later(0.4, someone_else_comments)
    with client.stream("GET", f"/stream?item={item['id']}") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = read_events(r)
    timer.join()
    versions = [d["version"] for n, d in events if n == "item"]
    latest = [d["latest"] for n, d in events if n == "notifications"]
    assert versions == [1, 2]  # the starting state, then the change
    assert len(latest) == 2 and latest[1] > latest[0]


def test_stream_refuses_outsiders(client, login):
    login("ben.okafor@newtonite.dev")  # not on Payments
    with db.pool.connection() as conn:
        iid = conn.execute(
            "select w.id from work_items w join teams t on t.id = w.team_id"
            " where t.name = 'Payments' limit 1"
        ).fetchone()["id"]
    assert client.get(f"/stream?item={iid}").status_code == 404
    client.cookies.clear()
    assert client.get("/stream").status_code == 401


def test_stream_ends_when_the_session_is_revoked(client, login, monkeypatch):
    monkeypatch.setattr(stream, "STREAM_SECONDS", 5.0)
    user = login("dev.mehta@newtonite.dev")

    def sign_out_everywhere():
        with db.pool.connection() as conn:
            conn.execute(
                "update sessions set revoked_at = now() where user_id = %s and revoked_at is null",
                (user["id"],),
            )

    timer = later(0.3, sign_out_everywhere)
    started = time.monotonic()
    with client.stream("GET", "/stream") as r:
        events = read_events(r)
    timer.join()
    assert ("ended", {"reason": "session"}) in events
    assert time.monotonic() - started < 3  # it stopped at once, not at the 5 second limit


def test_stream_closes_before_the_platform_limit():
    assert stream.STREAM_SECONDS < 300  # Vercel ends functions at 300 s on this plan
