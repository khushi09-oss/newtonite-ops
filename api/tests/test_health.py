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
