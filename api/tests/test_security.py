"""Sessions, sign-in switches, cross-site writes, rate limits and concurrent start-up.

Seeded facts: gia.mehta is a member of Payments; esha.moreau is a member of Support. Neither is
used by other tests, so changing them here cannot disturb anything else."""

from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import auth, db


def payments_item_id():
    with db.pool.connection() as conn:
        return conn.execute(
            "select w.id from work_items w join teams t on t.id = w.team_id"
            " where t.name = 'Payments' limit 1"
        ).fetchone()["id"]


def test_signout_revokes_the_session(client, login):
    login("gia.mehta@newtonite.dev")
    cookie = client.cookies.get("session")
    assert client.get("/me").status_code == 200
    assert client.post("/auth/logout").status_code == 204
    client.cookies.set("session", cookie)  # a copy of the cookie kept by someone else
    assert client.get("/me").status_code == 401
    client.cookies.clear()


def test_deactivated_user_is_locked_out_at_once(client, login):
    user = login("esha.moreau@newtonite.dev")
    assert client.get("/me").status_code == 200
    try:
        with db.pool.connection() as conn:
            conn.execute("update users set deactivated_at = now() where id = %s", (user["id"],))
        assert client.get("/me").status_code == 401
        client.cookies.clear()
        r = client.post("/auth/dev-login", json={"email": "esha.moreau@newtonite.dev"})
        assert r.status_code == 404
    finally:
        with db.pool.connection() as conn:
            conn.execute("update users set deactivated_at = null where id = %s", (user["id"],))


def test_dev_login_off_unless_enabled(client, monkeypatch):
    monkeypatch.setattr(auth, "DEV_LOGIN", False)
    r = client.post("/auth/dev-login", json={"email": "gia.mehta@newtonite.dev"})
    assert r.status_code == 404


def test_cross_site_write_refused(client, login):
    login("gia.mehta@newtonite.dev")
    iid = payments_item_id()
    with db.pool.connection() as conn:
        before = conn.execute("select version from work_items where id = %s", (iid,)).fetchone()
    evil = {"Origin": "https://evil.example"}
    r = client.post(f"/items/{iid}/comments", json={"body": "forged"}, headers=evil)
    assert r.status_code == 403
    assert client.get(f"/items/{iid}", headers=evil).status_code == 200  # reads are not a risk
    with db.pool.connection() as conn:
        after = conn.execute("select version from work_items where id = %s", (iid,)).fetchone()
    assert after == before
    ours = {"Origin": "http://localhost:5173"}
    assert client.post(f"/items/{iid}/watch", headers=ours).status_code == 204


def test_writes_rate_limited_per_user(client, login, monkeypatch):
    monkeypatch.setattr(auth, "WRITE_LIMIT_PER_MINUTE", 3)
    monkeypatch.setattr(auth, "WRITE_WINDOW_SECONDS", 3600)  # no window boundary mid-test
    ava = login("ava.okafor@newtonite.dev")
    gia = login("gia.mehta@newtonite.dev")
    keys = [f"write:{gia['id']}", f"write:{ava['id']}"]
    with (
        db.pool.connection() as conn
    ):  # counters from earlier writes this hour would skew the count
        conn.execute("delete from rate_limits where key = any(%s)", (keys,))
    try:
        iid = payments_item_id()
        for _ in range(3):
            assert client.post(f"/items/{iid}/watch").status_code == 204
        r = client.post(f"/items/{iid}/watch")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert client.get(f"/items/{iid}").status_code == 200  # reads still work
        login("ava.okafor@newtonite.dev")  # someone else is not affected
        assert client.post(f"/items/{iid}/watch").status_code == 204
    finally:
        with db.pool.connection() as conn:
            conn.execute("delete from rate_limits where key = any(%s)", (keys,))


def test_concurrent_startups_migrate_safely(client):
    """Four instances start at once against an empty database. Each migration runs exactly once."""
    name = "ops_migrate_race"
    admin = psycopg.connect(db.DATABASE_URL, autocommit=True)
    try:
        admin.execute(f"drop database if exists {name}")
        admin.execute(f"create database {name}")
        url = db.DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
        with (
            ConnectionPool(url, min_size=4, max_size=4, kwargs={"row_factory": dict_row}) as fresh,
            ThreadPoolExecutor(4) as workers,
        ):
            results = list(workers.map(lambda _: db.migrate(fresh), range(4)))
        files = sorted(p.name for p in db.MIGRATIONS.glob("*.sql"))
        assert (
            sorted(f for applied in results for f in applied) == files
        )  # every file applied once, by exactly one instance
    finally:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.close()
