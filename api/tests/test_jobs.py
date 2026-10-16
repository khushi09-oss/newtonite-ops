"""The admin job screen and housekeeping. Jobs here use kinds of their own, which the local worker
does not handle, except where a test needs a real job to be processed."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from conftest import session_cookie
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import db, outbox
from app.main import app

ADMIN = "ava.okafor@newtonite.dev"


def dead_job(kind=None, payload=None, error="RuntimeError: provider timed out") -> int:
    with db.pool.connection() as conn:
        return conn.execute(
            "insert into outbox (kind, payload, status, attempts, last_error)"
            " values (%s, %s, 'dead', 8, %s) returning id",
            (kind or f"test_dead_{uuid4().hex[:8]}", Jsonb(payload or {}), error),
        ).fetchone()["id"]


def job(jid) -> dict:
    with db.pool.connection() as conn:
        return conn.execute("select * from outbox where id = %s", (jid,)).fetchone()


def drop(*ids):
    with db.pool.connection() as conn:
        conn.execute("delete from outbox where id = any(%s)", (list(ids),))


def test_non_admin_cannot_see_or_retry_jobs(client, login):
    jid = dead_job()
    login("finn.okafor@newtonite.dev")
    assert client.get("/admin/jobs").status_code == 404
    assert client.post(f"/admin/jobs/{jid}/retry").status_code == 404
    assert job(jid)["status"] == "dead"
    drop(jid)


def test_admin_sees_failed_jobs_with_their_errors(client, login):
    jid = dead_job(error="RuntimeError: mail provider said no")
    login(ADMIN)
    body = client.get("/admin/jobs", params={"status": "dead"}).json()
    mine = [j for j in body["jobs"] if j["id"] == jid]
    assert mine and mine[0]["last_error"] == "RuntimeError: mail provider said no"
    assert body["counts"]["dead"] >= 1 and set(body["counts"]) == {"pending", "done", "dead"}
    assert client.get("/admin/jobs", params={"status": "bogus"}).status_code == 422
    drop(jid)


def test_retry_gives_a_failed_job_fresh_attempts_and_is_recorded(client, login):
    jid = dead_job()
    login(ADMIN)
    r = client.post(f"/admin/jobs/{jid}/retry")
    assert r.status_code == 200
    after = job(jid)
    assert after["status"] == "pending" and after["attempts"] == 0
    log = client.get("/admin/events").json()["events"]
    assert any(e["kind"] == "job_retried" and e["target"]["job_id"] == jid for e in log)
    drop(jid)


def test_two_admins_retrying_at_once_retry_it_once(client, login):
    jid = dead_job()
    admin = login(ADMIN)
    clients = []
    for _ in range(5):
        c = TestClient(app)
        c.cookies.set("session", session_cookie(admin["id"]))
        clients.append(c)
    with ThreadPoolExecutor(5) as pool:
        codes = sorted(pool.map(lambda c: c.post(f"/admin/jobs/{jid}/retry").status_code, clients))
    assert codes == [200, 409, 409, 409, 409]
    with db.pool.connection() as conn:
        n = conn.execute(
            "select count(*) from admin_events where kind = 'job_retried' and (target->>'job_id')::bigint = %s",
            (jid,),
        ).fetchone()["count"]
    assert n == 1
    drop(jid)


def test_a_retried_job_is_processed(client, login, team_id):
    """A real notification job, failed for good, then retried: it is delivered and marked done."""
    dev = login("dev.mehta@newtonite.dev")
    item = client.post(
        "/items",
        json={"team_id": str(team_id("Risk")), "kind": "task", "title": f"retry me {uuid4()}"},
        headers={"Idempotency-Key": str(uuid4())},
    ).json()
    finn = login("finn.okafor@newtonite.dev")
    event_id = client.post(f"/items/{item['id']}/comments", json={"body": "ping"}).json()["events"][
        0
    ]["id"]
    payload = {
        "item_id": item["id"],
        "event_id": event_id,
        "actor_id": finn["id"],
        "kind": "comment",
    }
    jid = dead_job(kind="item_event", payload=payload)
    login(ADMIN)
    assert client.post(f"/admin/jobs/{jid}/retry").status_code == 200
    # The local worker may lock the job first, and then this call rightly skips it, so wait for done
    # rather than assuming this call did the work.
    for _ in range(50):
        with db.pool.connection() as conn:
            outbox.process_batch(conn)
        if job(jid)["status"] == "done":
            break
        time.sleep(0.1)
    with db.pool.connection() as conn:
        delivered = conn.execute(
            "select count(*) from notifications where user_id = %s and event_id = %s",
            (dev["id"], event_id),
        ).fetchone()["count"]
    assert job(jid)["status"] == "done" and delivered == 1


def test_housekeeping_purges_old_finished_jobs_but_keeps_failed_ones(client):
    with db.pool.connection() as conn:
        old_done, recent_done, old_dead = (
            conn.execute(
                "insert into outbox (kind, payload, status, processed_at, created_at)"
                " values (%s, '{}', %s, now() - %s::interval, now() - %s::interval) returning id",
                (f"test_hk_{uuid4().hex[:8]}", status, age, age),
            ).fetchone()["id"]
            for status, age in (("done", "8 days"), ("done", "1 day"), ("dead", "30 days"))
        )
        outbox.housekeeping(conn)
        left = {
            r["id"]
            for r in conn.execute(
                "select id from outbox where id = any(%s)", ([old_done, recent_done, old_dead],)
            )
        }
    assert left == {recent_done, old_dead}
    drop(recent_done, old_dead)
