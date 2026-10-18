import time
from concurrent.futures import ThreadPoolExecutor

import psycopg

from app import db


def test_health_reaches_the_database(client):
    assert client.get("/health").json() == {"ok": True}


def test_a_connection_the_database_dropped_is_replaced_not_served(client, login):
    """Neon closes idle connections and suspends an idle database. A request after that must get a
    working connection, not the dead one the pool was holding, which used to answer 500."""
    login("dev.mehta@newtonite.dev")
    # Fill the pool with several open connections, as a busy server would have.
    with ThreadPoolExecutor(10) as ex:
        assert set(ex.map(lambda _: client.get("/health").status_code, range(30))) == {200}
    # From a separate connection, end every other one this API process holds, as the database does.
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as admin:
        killed = admin.execute(
            "select count(pg_terminate_backend(pid)) from pg_stat_activity"
            " where datname = current_database() and pid <> pg_backend_pid()"
            " and client_addr is not distinct from inet_client_addr()"
        ).fetchone()[0]
    assert killed >= 1
    started = time.monotonic()
    for _ in range(3):  # each request may meet a different dead connection
        assert client.get("/me").status_code == 200
    # And quickly: dead connections are skipped at once, not retried with growing waits.
    assert time.monotonic() - started < 3


def test_a_database_that_is_not_answering_gets_a_plain_503_on_every_route(
    client, login, monkeypatch
):
    """Down, waking, or dropping connections: every route answers the same plain 503, quickly,
    instead of hanging and answering a bare 500."""
    from psycopg_pool import PoolTimeout

    login("dev.mehta@newtonite.dev")
    for failure in (PoolTimeout("no connection"), psycopg.OperationalError("server closed")):

        def down(*args, failure=failure, **kwargs):
            raise failure

        monkeypatch.setattr(db.pool, "getconn", down)
        for method, path in [("GET", "/me"), ("GET", "/items"), ("POST", "/notifications/read")]:
            r = client.request(method, path, json={"up_to": 1} if method == "POST" else None)
            assert r.status_code == 503, (method, path, r.text)
            assert r.json()["detail"].startswith("The database is not answering")
        monkeypatch.undo()
    assert client.get("/me").status_code == 200  # and back to normal once it answers
