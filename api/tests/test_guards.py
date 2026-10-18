"""Guards for rules that hold across the whole codebase, not one route."""

import ast
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import session_cookie
from fastapi.testclient import TestClient

from app import db
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
        c.cookies.set("session", session_cookie(dev["id"]))
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


def test_null_in_any_field_of_any_write_is_refused_not_a_500(client, login, team_id):
    """Every write route, every field set to null in turn. A field that may be empty takes it; any
    other answers 4xx. Nothing reaches the database as a NULL it forbids and comes back as 500."""
    risk = str(team_id("Risk"))
    hugo = "hugo.moreau@newtonite.dev"

    def item_route(path, body):
        def run(field):
            finn = login("finn.okafor@newtonite.dev")
            created = client.post(
                "/items",
                json={"team_id": risk, "kind": "task", "title": "Null check"},
                headers={"Idempotency-Key": str(uuid4())},
            ).json()
            sent = {**body(finn), field: None}
            r = client.request(
                "PATCH" if path == "" else "POST",
                f"/items/{created['id']}{path}",
                json=sent,
                headers={"Idempotency-Key": str(uuid4())},
            )
            return r

        return run

    def admin_route(method, path, body):
        def run(field):
            login("ava.okafor@newtonite.dev")
            target = client.post(
                "/admin/users",
                json={"email": f"null-{uuid4().hex[:8]}@example.com", "name": "Null"},
            ).json()
            team = client.post("/admin/teams", json={"name": f"Null {uuid4().hex[:8]}"}).json()
            url = path.format(user=target["id"], team=team["id"])
            return client.request(method, url, json={**body, field: None})

        return run

    def create(field):
        login("finn.okafor@newtonite.dev")
        body = {"team_id": risk, "kind": "task", "title": "t", "description": "d", "priority": 2}
        return client.post(
            "/items",
            json={**body, "due_at": "2030-01-01T00:00:00Z", field: None},
            headers={"Idempotency-Key": str(uuid4())},
        )

    def lead_adds(field):
        login("finn.okafor@newtonite.dev")
        url = f"/teams/{risk}/members"
        return client.put(url, json={"email": hugo, "name": "Hugo", "role": "member", field: None})

    def read_all(field):
        login("dev.mehta@newtonite.dev")
        return client.post("/notifications/read", json={"up_to": None})

    routes = {
        "create": (create, ["team_id", "kind", "title", "description", "priority", "due_at"]),
        "edit": (
            item_route(
                "",
                lambda _: {
                    "version": 1,
                    "title": "t",
                    "description": "d",
                    "priority": 1,
                    "due_at": "2030-01-01T00:00:00Z",
                },
            ),
            ["version", "title", "description", "priority", "due_at"],
        ),
        "claim": (item_route("/claim", lambda _: {"version": 1}), ["version"]),
        "release": (item_route("/release", lambda _: {"version": 1}), ["version"]),
        "assign": (
            item_route("/assign", lambda me: {"version": 1, "assignee_id": me["id"]}),
            ["version", "assignee_id"],
        ),
        "transition": (
            item_route("/transition", lambda _: {"version": 1, "to": "triaged", "note": "n"}),
            ["version", "to", "note"],
        ),
        "comment": (item_route("/comments", lambda _: {"body": "b"}), ["body"]),
        "lead adds": (lead_adds, ["email", "name", "role"]),
        "invite": (
            admin_route("POST", "/admin/users", {"email": "x@example.com", "name": "N"}),
            ["email", "name", "is_admin", "teams"],
        ),
        "change person": (
            admin_route("PATCH", "/admin/users/{user}", {"name": "N", "is_admin": False}),
            ["name", "is_admin", "active"],
        ),
        "create team": (admin_route("POST", "/admin/teams", {"name": "T"}), ["name"]),
        "set role": (
            admin_route("PUT", "/admin/teams/{team}/members/{user}", {"role": "member"}),
            ["role"],
        ),
        "read all": (read_all, ["up_to"]),
    }
    crashed = []
    for name, (send, fields) in routes.items():
        for field in fields:
            r = send(field)
            if r.status_code >= 500:
                crashed.append(f"{name} {field}=null -> {r.status_code}")
    assert crashed == []


# Writes that are safe to send twice without a key: repeating them leaves the same state and answer.
SAFE_TO_REPEAT = {
    "watch": "watching twice is still watching",
    "unwatch": "not watching twice is still not watching",
    "mark_read": "read twice is still read",
    "mark_all_read": "marks up to the same id; a repeat marks nothing more",
    "add_member": "an upsert: the same email and role leave the same membership",
    "logout": "ends the same session",
    "dev_login": "signs in again, a new session either way",
    "google_sign_in": "signs in again, a new session either way",
}


def test_every_write_route_is_safe_to_retry():
    """A client unsure whether its request landed sends it again. Every POST, PUT, PATCH and DELETE
    route either replays the first answer for the same Idempotency-Key, or is listed above with the
    reason a repeat is harmless. A new route that is neither fails here."""
    unsafe = []
    for path in sorted((ROOT / "app").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            writes = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr in ("post", "put", "patch", "delete")
                for d in fn.decorator_list
            )
            if not writes or fn.name in SAFE_TO_REPEAT:
                continue
            calls = {
                n.func.id
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            if "idempotent" not in calls:
                unsafe.append(f"{path.name}:{fn.name}")
    assert unsafe == []


def test_a_retried_write_replays_its_first_answer_instead_of_failing(client, login, team_id):
    """The case the brief names: the first request landed but its answer was lost, so the client
    sends it again with the same key. Claim, edit, release and an admin invite each replay."""
    login("dev.mehta@newtonite.dev")
    item = client.post(
        "/items",
        json={"team_id": str(team_id("Risk")), "kind": "task", "title": "Retry me"},
        headers={"Idempotency-Key": str(uuid4())},
    ).json()

    def twice(method, url, body):
        key = {"Idempotency-Key": str(uuid4())}
        first = client.request(method, url, json=body, headers=key)
        again = client.request(method, url, json=body, headers=key)
        assert first.status_code < 300, first.text
        assert (again.status_code, again.json()) == (first.status_code, first.json())
        return first.json()

    claimed = twice("POST", f"/items/{item['id']}/claim", {"version": item["version"]})
    edited = twice(
        "PATCH", f"/items/{item['id']}", {"version": claimed["version"], "title": "Done"}
    )
    twice("POST", f"/items/{item['id']}/release", {"version": edited["version"]})
    with db.pool.connection() as conn:
        kinds = [
            r["kind"]
            for r in conn.execute(
                "select kind from work_item_events where item_id = %s order by seq", (item["id"],)
            )
        ]
    assert kinds == ["created", "claimed", "edited", "released"]  # each ran once

    login("ava.okafor@newtonite.dev")
    invited = twice(
        "POST", "/admin/users", {"email": f"r-{uuid4().hex[:8]}@example.com", "name": "R"}
    )
    assert invited["email"].startswith("r-")
