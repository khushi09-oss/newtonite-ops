import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def login(client):
    """Sign the shared client in as a seeded user and return that user."""

    def _login(email: str) -> dict:
        client.cookies.clear()
        r = client.post("/auth/dev-login", json={"email": email})
        assert r.status_code == 200, r.text
        return r.json()

    return _login


@pytest.fixture
def team_id():
    """A team's id by name."""

    def _team(name: str):
        with db.pool.connection() as conn:
            return conn.execute("select id from teams where name = %s", (name,)).fetchone()["id"]

    return _team
