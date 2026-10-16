"""Guards for rules that hold across the whole codebase, not one route."""

import ast
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import signer
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
WRITES_ITEMS = re.compile(r"\b(insert\s+into|update|delete\s+from)\s+work_items\b", re.IGNORECASE)


def sql_writes_to_items(path: Path) -> list[tuple[str, int]]:
    """(enclosing function, line) for every string literal in the file that writes work_items."""
    found = []

    def visit(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and WRITES_ITEMS.search(node.value)
        ):
            found.append((func, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(ast.parse(path.read_text(encoding="utf-8")), "<module>")
    return found


def test_only_apply_change_writes_work_items():
    sources = [*sorted((ROOT / "app").glob("*.py")), ROOT / "worker.py"]
    writers = {(p.name, func) for p in sources for func, _ in sql_writes_to_items(p)}
    assert writers == {("items.py", "apply_change")}, writers


def payments_item():
    with db.pool.connection() as conn:
        return conn.execute(
            "select w.id, w.version from work_items w join teams t on t.id = w.team_id"
            " where t.name = 'Payments' and w.status = 'new' limit 1"
        ).fetchone()


def event_count(item_id) -> int:
    with db.pool.connection() as conn:
        return conn.execute(
            "select count(*) from work_item_events where item_id = %s", (item_id,)
        ).fetchone()["count"]


ROUTES = [
    ("GET", "", None),
    ("GET", "/events", None),
    ("PATCH", "", {"title": "outsider edit"}),
    ("POST", "/claim", {}),
    ("POST", "/release", {}),
    ("POST", "/assign", {"assignee_id": str(uuid4())}),
    ("POST", "/transition", {"to": "triaged"}),
    ("POST", "/comments", {"body": "outsider comment"}),
    ("POST", "/watch", None),
    ("DELETE", "/watch", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES, ids=[f"{m} {p or '/'}" for m, p, _ in ROUTES])
def test_nonmember_gets_404_on_every_item_route(client, login, method, path, body):
    login("ben.okafor@newtonite.dev")  # leads Compliance, views Risk, not on Payments
    item = payments_item()
    if body is not None and path != "/comments":
        body = {**body, "version": item["version"]}
    before = event_count(item["id"])
    r = client.request(method, f"/items/{item['id']}{path}", json=body)
    assert r.status_code == 404, r.text
    assert event_count(item["id"]) == before


def test_idempotency_concurrent_duplicates_run_once(client, team_id):
    """client is unused directly; its startup opens the pool, so the test runs alone or in a suite."""
    with db.pool.connection() as conn:
        dev = conn.execute(
            "select id from users where email = 'dev.mehta@newtonite.dev'"
        ).fetchone()
    key, title = str(uuid4()), f"ten clicks at once {uuid4()}"
    body = {"team_id": str(team_id("Risk")), "kind": "task", "title": title}
    clients = []
    for _ in range(10):
        c = TestClient(app)
        c.cookies.set("session", signer.dumps(str(dev["id"])))
        clients.append(c)

    def create(c):
        return c.post("/items", json=body, headers={"Idempotency-Key": key})

    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(create, clients))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["id"] for r in results}) == 1
    with db.pool.connection() as conn:
        n = conn.execute("select count(*) from work_items where title = %s", (title,)).fetchone()
    assert n["count"] == 1
