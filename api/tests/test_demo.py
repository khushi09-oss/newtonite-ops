"""Demo mode. The reset is tested against a throwaway database: run against the shared test database
it would wipe it mid-suite."""

from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from test_admin import as_user
from test_google import CLIENT, KEY, token

from app import db, demo, google, stream

ADMIN = "ava.okafor@newtonite.dev"


@pytest.fixture
def demo_on(monkeypatch):
    monkeypatch.setattr(demo, "DEMO_MODE", True)
    monkeypatch.setattr(google, "CLIENT_ID", CLIENT)
    monkeypatch.setattr(google, "signing_key", lambda _t: KEY.public_key())
    with db.pool.connection() as conn:
        conn.execute("delete from rate_limits where key like 'signin:%%'")


def test_demo_google_visitor_gets_in_to_an_empty_workspace_of_their_own(client, demo_on):
    email = f"visitor-{uuid4().hex[:8]}@gmail.com"
    client.cookies.clear()
    r = client.post(
        "/auth/google",
        json={"credential": token(email=email, sub=f"sub-{email}", name="A Visitor")},
    )
    assert r.status_code == 200, r.text
    me = client.get("/me").json()
    assert me["name"] == "A Visitor" and me["is_admin"] is False
    # A real person's account: hidden from the shared admin, and closed to password-free sign-in.
    client.cookies.clear()
    assert client.post("/auth/dev-login", json={"email": email}).status_code == 404
    assert me["teams"] == [] and me["private"] is True  # none of the shared demo teams
    assert client.get("/auth/config").json()["demo"] is True


def visitor() -> tuple[TestClient, str]:
    """A Google visitor as the demo makes one, signed in on a client of their own."""
    with db.pool.connection() as conn:
        uid = conn.execute(
            "insert into users (email, name, private) values (%s, 'Visitor', true) returning id",
            (f"visitor-{uuid4().hex[:8]}@gmail.com",),
        ).fetchone()["id"]
    return as_user(uid), str(uid)


def new_item(c, team_id):
    r = c.post(
        "/items",
        json={"team_id": team_id, "kind": "task", "title": "Workspace item"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_workspace_is_seen_only_by_its_lead_and_the_people_they_add(client, login, demo_on):
    lead, _ = visitor()
    other, _ = visitor()
    team = lead.post("/teams", json={"name": "Marketing"}).json()
    assert team["role"] == "lead" and team["key"].startswith("MAR")
    # Another visitor's team of the same name is theirs, not a clash that reveals the first one.
    assert other.post("/teams", json={"name": "Marketing"}).status_code == 201

    email = f"teammate-{uuid4().hex[:8]}@example.com"
    r = lead.put(f"/teams/{team['id']}/members", json={"email": email, "name": "Teammate"})
    assert r.status_code == 200, r.text
    mate = as_user(r.json()["user_id"])
    assert [t["role"] for t in mate.get("/me").json()["teams"]] == ["member"]
    item = new_item(mate, team["id"])
    assert lead.get(f"/items/{item['id']}").status_code == 200

    assert other.get(f"/items/{item['id']}").status_code == 404
    assert other.put(f"/teams/{team['id']}/members", json={"email": email}).status_code == 404
    login(ADMIN)
    assert client.get(f"/items/{item['id']}").status_code == 404
    assert client.get("/admin/users", params={"q": email}).json()["users"] == []


def test_a_new_workspace_gives_its_first_eight_teams_eight_different_colours(client, demo_on):
    lead, _ = visitor()
    colours = [lead.post("/teams", json={"name": f"Team {n}"}).json()["color"] for n in range(9)]
    assert sorted(colours[:8]) == list(range(8))  # all eight colours before any repeats
    assert colours[8] == 0  # then the least used again, lowest first
    seen = {t["name"]: t["color"] for t in lead.get("/me").json()["visible_teams"]}
    assert [seen[f"Team {n}"] for n in range(9)] == colours  # and every screen gets the same one


def test_workspace_rules_keep_the_shared_demo_accounts_out(client, login, demo_on):
    login("dev.mehta@newtonite.dev")
    assert client.post("/teams", json={"name": "Shared"}).status_code == 403
    lead, lead_id = visitor()
    team = lead.post("/teams", json={"name": f"Ops {uuid4().hex[:6]}"}).json()["id"]

    def add(c, email, role="member"):
        return c.put(f"/teams/{team}/members", json={"email": email, "role": role})

    assert add(lead, "dev.mehta@newtonite.dev").status_code == 403
    with db.pool.connection() as conn:
        lead_email = conn.execute("select email from users where id = %s", (lead_id,)).fetchone()
    assert add(lead, lead_email["email"], "viewer").status_code == 422  # no demoting yourself

    email = f"mate-{uuid4().hex[:8]}@example.com"
    mate = as_user(add(lead, email).json()["user_id"])
    assert add(mate, f"x-{uuid4().hex[:8]}@example.com").status_code == 403  # members cannot add
    item = new_item(mate, team)
    assert mate.post(f"/items/{item['id']}/claim", json={"version": 1}).status_code == 200
    mate_id = mate.get("/me").json()["id"]
    r = lead.delete(f"/teams/{team}/members/{mate_id}")
    assert r.status_code == 200 and r.json()["released"] == 1
    assert lead.get(f"/items/{item['id']}").json()["assignee_id"] is None

    # The demo's own teams belong to the shared accounts, even for their lead.
    login("finn.okafor@newtonite.dev")
    risk = next(t for t in client.get("/me").json()["teams"] if t["name"] == "Risk")
    assert client.put(f"/teams/{risk['id']}/members", json={"email": email}).status_code == 403


def test_off_the_demo_a_lead_adds_only_people_who_already_have_an_account(client, login):
    login(ADMIN)
    team = client.post("/admin/teams", json={"name": f"Lead adds {uuid4().hex[:8]}"}).json()
    finn = login("finn.okafor@newtonite.dev")
    login(ADMIN)
    client.put(f"/admin/teams/{team['id']}/members/{finn['id']}", json={"role": "lead"})
    login("finn.okafor@newtonite.dev")
    url = f"/teams/{team['id']}/members"
    assert client.put(url, json={"email": "hugo.moreau@newtonite.dev"}).status_code == 200
    stranger = f"stranger-{uuid4().hex[:8]}@example.com"
    assert client.put(url, json={"email": stranger}).status_code == 404
    assert client.post("/teams", json={"name": "Not here"}).status_code == 404


def test_demo_accounts_cannot_be_changed(client, login, demo_on):
    """The target is a throwaway account on the demo domain, never one of the shared demo accounts,
    so even a broken guard cannot disturb the seed."""
    login(ADMIN)
    with db.pool.connection() as conn:
        target = conn.execute(
            "insert into users (email, name) values (%s, 'Guard target') returning id",
            (f"guard-target-{uuid4().hex[:8]}@newtonite.dev",),
        ).fetchone()["id"]
        team = conn.execute("select id from teams where name = 'Support'").fetchone()["id"]
        conn.execute(
            "insert into team_members (team_id, user_id, role) values (%s, %s, 'member')",
            (team, target),
        )
    sid, tid = str(target), str(team)
    assert client.patch(f"/admin/users/{sid}", json={"active": False}).status_code == 403
    assert client.put(f"/admin/teams/{tid}/members/{sid}", json={"role": "lead"}).status_code == 403
    assert client.delete(f"/admin/teams/{tid}/members/{sid}").status_code == 403
    with db.pool.connection() as conn:
        row = conn.execute(
            "select u.deactivated_at, m.role from users u join team_members m on m.user_id = u.id"
            " where u.id = %s",
            (sid,),
        ).fetchone()
    assert row == {"deactivated_at": None, "role": "member"}
    # A visitor's own invitee can still be changed: the demo stays explorable.
    invited = client.post(
        "/admin/users", json={"email": f"mine-{uuid4().hex[:8]}@example.com", "name": "Mine"}
    )
    assert invited.status_code == 201
    assert (
        client.patch(f"/admin/users/{invited.json()['id']}", json={"active": False}).status_code
        == 200
    )


def private_workspace():
    """A visitor's own team, lead and urgent item, as the demo makes them: both rows marked private."""
    tag = uuid4().hex[:8]
    with db.pool.connection() as conn:
        owner = conn.execute(
            "insert into users (email, name, private) values (%s, 'Owner', true) returning id",
            (f"owner-{tag}@gmail.com",),
        ).fetchone()["id"]
        team = conn.execute(
            "insert into teams (name, key, private, color) values (%s, %s, true, 0) returning id, name",
            (f"Private {tag}", f"PV{tag.upper()}"),
        ).fetchone()
        conn.execute(
            "insert into team_members (team_id, user_id, role) values (%s, %s, 'lead')",
            (team["id"], owner),
        )
    mine = as_user(owner)
    r = mine.post(
        "/items",
        json={"team_id": str(team["id"]), "kind": "task", "title": f"Secret {tag}", "priority": 0},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 201, r.text
    return {
        "owner": str(owner),
        "email": f"owner-{tag}@gmail.com",
        "team": str(team["id"]),
        "item": r.json(),
        "client": mine,
    }


def test_the_shared_admin_cannot_see_a_private_workspace_by_any_route(client, login):
    ws = private_workspace()
    item, team, owner = ws["item"], ws["team"], ws["owner"]
    assert ws["client"].get(f"/items/{item['id']}").status_code == 200  # the owner still can

    admin = login(ADMIN)
    assert client.get(f"/items/{item['id']}").status_code == 404
    assert client.get("/items", params={"team": team}).json()["items"] == []
    assert sum(client.get("/items/counts", params={"team": team}).json()["counts"].values()) == 0
    assert client.get("/items", params={"q": item["title"]}).json()["items"] == []
    assert client.get(f"/teams/{team}/members").status_code == 404
    created = client.post(
        "/items",
        json={"team_id": team, "kind": "task", "title": "Planted"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 404
    inbox = client.get("/inbox").json()
    assert item["id"] not in {i["id"] for b in inbox.values() for i in b["items"]}
    recent = client.get("/dashboard").json()["recent"]
    assert item["id"] not in {e["item_id"] for e in recent}
    assert stream.tick(str(uuid4()), admin, item["id"])["version"] is None

    assert team not in {t["id"] for t in client.get("/admin/teams").json()["teams"]}
    assert client.get("/admin/users", params={"q": ws["email"]}).json()["users"] == []
    assert client.get(f"/admin/teams/{team}/members").status_code == 404
    assert client.patch(f"/admin/users/{owner}", json={"active": False}).status_code == 404
    assert (
        client.put(f"/admin/teams/{team}/members/{admin['id']}", json={"role": "lead"}).status_code
        == 404
    )
    invite = {
        "email": f"x-{uuid4().hex[:8]}@example.com",
        "name": "X",
        "teams": [{"team_id": team, "role": "member"}],
    }
    assert client.post("/admin/users", json=invite).status_code == 404

    # And nobody can take the account over through the demo's password-free sign-in.
    client.cookies.clear()
    assert client.post("/auth/dev-login", json={"email": ws["email"]}).status_code == 404


def test_nightly_reset_needs_demo_mode_and_the_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.setattr(demo, "DEMO_MODE", False)
    assert (
        client.get("/internal/reset-demo", headers={"Authorization": "Bearer s3cret"}).status_code
        == 404
    )
    monkeypatch.setattr(demo, "DEMO_MODE", True)
    assert client.get("/internal/reset-demo").status_code == 404
    assert (
        client.get("/internal/reset-demo", headers={"Authorization": "Bearer wrong"}).status_code
        == 404
    )
    # The right secret is not sent here: it would reset the shared test database. See the next test.


def test_demo_reset_restores_a_clean_seed(client):
    name = "ops_demo_reset"
    admin = psycopg.connect(db.DATABASE_URL, autocommit=True)
    try:
        admin.execute(f"drop database if exists {name}")
        admin.execute(f"create database {name}")
        url = db.DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
        with ConnectionPool(url, min_size=1, max_size=2, kwargs={"row_factory": dict_row}) as fresh:
            db.migrate(fresh)
            demo.reset(fresh)
            with fresh.connection() as conn:
                conn.execute("insert into users (email, name) values ('junk@example.com', 'Junk')")
            demo.reset(fresh)  # a second night: the junk goes, the seed comes back whole
            with fresh.connection() as conn:
                counts = conn.execute(
                    "select (select count(*) from users) as users, (select count(*) from teams) as teams,"
                    " (select count(*) from work_items) as items,"
                    " (select count(distinct color) from teams) as colours,"
                    " (select count(*) from users where email = 'junk@example.com') as junk,"
                    " (select count(*) from work_items w where version <>"
                    "   (select count(*) from work_item_events e where e.item_id = w.id)) as broken"
                ).fetchone()
        assert counts == {
            "users": 40,
            "teams": 8,
            "items": 20000,
            "colours": 8,
            "junk": 0,
            "broken": 0,
        }
    finally:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.close()


def workspace(conn, tag: str, signed_in: str) -> dict:
    """A visitor's private workspace as the app leaves it: a lead last signed in `signed_in` ago, a
    teammate who never signed in, a viewer who only follows, an item with two changes, a watcher, a notification, a pending
    job and the admin log entry for the team."""
    lead, mate, viewer = (
        conn.execute(
            "insert into users (email, name, private) values (%s, %s, true) returning id",
            (f"{who}-{tag}@example.com", who),
        ).fetchone()["id"]
        for who in ("lead", "mate", "viewer")
    )
    conn.execute(
        "insert into sessions (user_id, created_at, expires_at)"
        " values (%s, now() - %s::interval, now() - %s::interval + interval '7 days')",
        (lead, signed_in, signed_in),
    )
    team = conn.execute(
        "insert into teams (name, key, private, color, next_number) values ('Mine', %s, true, 0, 1)"
        " returning id",
        (f"W{tag.upper()}",),
    ).fetchone()["id"]
    conn.execute(
        "insert into team_members values (%s, %s, 'lead'), (%s, %s, 'member'), (%s, %s, 'viewer')",
        (team, lead, team, mate, team, viewer),
    )
    item = conn.execute(
        "insert into work_items (team_id, kind, title, reporter_id, assignee_id, number, version)"
        " values (%s, 'task', 'Kept', %s, %s, 1, 2) returning id",
        (team, lead, mate),
    ).fetchone()["id"]
    events = [
        conn.execute(
            "insert into work_item_events (item_id, seq, actor_id, kind) values (%s, %s, %s, %s)"
            " returning id",
            (item, seq, actor, kind),
        ).fetchone()["id"]
        for seq, actor, kind in ((1, lead, "created"), (2, mate, "claimed"))
    ]
    conn.execute("insert into watchers values (%s, %s)", (item, lead))
    conn.execute("insert into notifications (user_id, event_id) values (%s, %s)", (lead, events[1]))
    conn.execute(
        "insert into outbox (kind, payload) values ('item_event', jsonb_build_object('item_id', %s::text))",
        (item,),
    )
    conn.execute(
        "insert into admin_events (actor_id, kind, target)"
        " values (%s, 'team_created', jsonb_build_object('team_id', %s::text))",
        (lead, team),
    )
    return {"lead": lead, "mate": mate, "viewer": viewer, "team": team, "item": item}


def test_the_nightly_reset_keeps_workspaces_in_use_and_clears_the_rest(client):
    name = "ops_demo_keep"
    admin = psycopg.connect(db.DATABASE_URL, autocommit=True)
    try:
        admin.execute(f"drop database if exists {name}")
        admin.execute(f"create database {name}")
        url = db.DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
        with ConnectionPool(url, min_size=1, max_size=2, kwargs={"row_factory": dict_row}) as fresh:
            db.migrate(fresh)
            demo.reset(fresh)
            with fresh.connection() as conn:
                used = workspace(conn, "a", "6 days")
                gone = workspace(conn, "b", "8 days")
                conn.execute("insert into users (email, name) values ('junk@example.com', 'Junk')")
            assert demo.reset(fresh) == {"users": 3, "teams": 1, "items": 1}
            assert demo.reset(fresh) == {"users": 3, "teams": 1, "items": 1}  # and the night after
            with fresh.connection() as conn:
                # The next change after a reset takes fresh ids, never one a kept row still holds.
                for table, cols, vals in (
                    (
                        "work_item_events",
                        "item_id, seq, actor_id, kind",
                        "%(i)s, 3, %(l)s, 'commented'",
                    ),
                    ("admin_events", "actor_id, kind, target", "%(l)s, 'team_colour_set', '{}'"),
                    ("outbox", "kind, payload", "'item_event', '{}'"),
                ):
                    conn.execute(
                        f"insert into {table} ({cols}) values ({vals})",
                        {"i": used["item"], "l": used["lead"]},
                    )
                conn.execute("delete from outbox where payload = '{}'")
                conn.execute("update work_items set version = 3 where id = %s", (used["item"],))

                def ids(q):
                    return {r["id"] for r in conn.execute(q)}

                assert ids("select id from users where private") == {
                    used["lead"],
                    used["mate"],
                    used["viewer"],
                }
                assert ids("select id from teams where private") == {used["team"]}
                kept = conn.execute(
                    "select (select count(*) from work_items where id = %(i)s and version = 3"
                    "   and assignee_id = %(m)s) as item,"
                    " (select count(*) from work_item_events where item_id = %(i)s) as events,"
                    " (select count(*) from team_members where team_id = %(t)s) as members,"
                    " (select count(*) from watchers where item_id = %(i)s) as watchers,"
                    " (select count(*) from notifications where user_id = %(l)s) as notifications,"
                    " (select count(*) from outbox where payload->>'item_id' = %(i)s::text) as jobs,"
                    " (select count(*) from sessions where user_id = %(l)s) as sessions,"
                    " (select count(*) from admin_events where actor_id = %(l)s) as logged,"
                    " (select count(*) from work_items where team_id = %(g)s) as gone_items,"
                    " (select count(*) from users) as users, (select count(*) from teams) as teams,"
                    " (select count(*) from work_items) as items,"
                    " (select count(*) from users where email = 'junk@example.com') as junk,"
                    " (select count(*) from work_items w where version <>"
                    "   (select count(*) from work_item_events e where e.item_id = w.id)) as broken",
                    {
                        "i": used["item"],
                        "m": used["mate"],
                        "t": used["team"],
                        "l": used["lead"],
                        "g": gone["team"],
                    },
                ).fetchone()
        assert kept == {
            "item": 1,
            "events": 3,
            "members": 3,
            "watchers": 1,
            "notifications": 1,
            "jobs": 1,
            "sessions": 1,
            "logged": 2,  # the kept entry, and the one made after the reset
            "gone_items": 0,
            "users": 43,
            "teams": 9,
            "items": 20001,
            "junk": 0,
            "broken": 0,
        }
    finally:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.close()


def test_respreading_colours_keeps_public_teams_apart_from_private_ones():
    """Migration 011 on the live site's shape: the eight demo teams plus a visitor's private team
    whose key falls between theirs. The public teams must end with eight different colours."""
    name = "ops_colour_spread"
    admin = psycopg.connect(db.DATABASE_URL, autocommit=True)
    try:
        admin.execute(f"drop database if exists {name}")
        admin.execute(f"create database {name}")
        url = db.DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
        with ConnectionPool(url, min_size=1, max_size=2, kwargs={"row_factory": dict_row}) as fresh:
            db.migrate(fresh)
            demo.reset(fresh)
            with fresh.connection() as conn:
                lead = conn.execute(
                    "insert into users (email, name, private) values ('v@example.com', 'V', true)"
                    " returning id"
                ).fetchone()["id"]
                team = conn.execute(
                    "insert into teams (name, key, private, color) values ('Data', 'DAT', true, 3)"
                    " returning id"
                ).fetchone()["id"]
                conn.execute(
                    "insert into team_members (team_id, user_id, role) values (%s, %s, 'lead')",
                    (team, lead),
                )
                # Scramble the colours the way one key order over every team did, then respread.
                conn.execute(
                    "with r as (select id, (row_number() over (order by key) - 1) % 8 as s from teams)"
                    " update teams t set color = r.s from r where r.id = t.id"
                )
                conn.execute((db.MIGRATIONS / "011_team_colours_by_scope.sql").read_text())
                public = conn.execute(
                    "select count(distinct color) as n from teams where not private"
                ).fetchone()["n"]
                private = conn.execute("select color from teams where id = %s", (team,)).fetchone()
        assert public == 8 and private["color"] == 0
    finally:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.close()


def test_each_inbox_tab_its_show_all_list_and_its_dashboard_tile_agree(client, demo_on):
    """One definition per question (items.inbox_views): in a small workspace, every inbox tab, its
    "Show all" list with its counts, and its dashboard tile name exactly the same items."""
    lead, _ = visitor()
    team = lead.post("/teams", json={"name": "Views"}).json()["id"]
    added = lead.put(f"/teams/{team}/members", json={"email": f"m-{uuid4().hex[:8]}@example.com"})
    mate = as_user(added.json()["user_id"])

    def act(c, path, body=None):
        r = c.post(path, json=body, headers={"Idempotency-Key": str(uuid4())})
        assert r.status_code < 300, r.text
        return r.json()

    def make(c, title, priority=2):
        return act(
            c, "/items", {"team_id": team, "kind": "task", "title": title, "priority": priority}
        )

    mine = make(lead, "Mine")
    act(lead, f"/items/{mine['id']}/claim", {"version": 1})
    asked = make(mate, "Needs sign-off")
    asked = act(mate, f"/items/{asked['id']}/claim", {"version": 1})
    act(
        mate,
        f"/items/{asked['id']}/transition",
        {"version": asked["version"], "to": "awaiting_approval"},
    )
    make(lead, "Urgent and nobody on it", priority=0)
    quiet = make(lead, "Gone quiet")
    with db.pool.connection() as conn:  # four days without a change
        conn.execute(
            "update work_items set last_activity_at = now() - interval '4 days' where id = %s",
            (quiet["id"],),
        )

    inbox = lead.get("/inbox").json()
    tiles = lead.get("/dashboard").json()["counts"]
    tile_of = {
        "assigned": "assigned",
        "approvals": "approvals",
        "unassigned_urgent": "urgent",
        "stale": "stale",
    }
    for view, tile in tile_of.items():
        tab = {i["id"] for i in inbox[view]["items"]}
        assert tab and not inbox[view]["capped"], view
        listed = {i["id"] for i in lead.get("/items", params={"view": view}).json()["items"]}
        counted = sum(lead.get("/items/counts", params={"view": view}).json()["counts"].values())
        assert listed == tab and counted == len(tab) == tiles[tile], view
    assert lead.get("/items", params={"view": "nonsense"}).status_code == 422


def test_a_team_colour_is_picked_by_its_lead_or_an_admin_and_every_screen_shows_it(
    client, login, demo_on
):
    lead, _ = visitor()
    other, _ = visitor()
    team = lead.post("/teams", json={"name": f"Colours {uuid4().hex[:6]}"}).json()
    url = f"/teams/{team['id']}"
    r = lead.patch(url, json={"color": 5})
    assert r.status_code == 200 and r.json()["color"] == 5
    item = new_item(lead, team["id"])
    assert lead.get(f"/items/{item['id']}").json()["team_color"] == 5
    seen = {t["id"]: t["color"] for t in lead.get("/me").json()["visible_teams"]}
    assert seen[team["id"]] == 5
    for bad in (8, -1, None):
        assert lead.patch(url, json={"color": bad}).status_code == 422

    mate = as_user(
        lead.put(f"{url}/members", json={"email": f"m-{uuid4().hex[:8]}@example.com"}).json()[
            "user_id"
        ]
    )
    assert mate.patch(url, json={"color": 1}).status_code == 403  # a member, not its lead
    assert other.patch(url, json={"color": 1}).status_code == 404  # an outsider learns nothing
    login(ADMIN)
    assert client.patch(url, json={"color": 1}).status_code == 404  # private, even to the admin

    # The demo's shared teams: not even their lead, but the admin may, as on the Admin page.
    login("finn.okafor@newtonite.dev")
    risk = next(t for t in client.get("/me").json()["teams"] if t["name"] == "Risk")
    assert client.patch(f"/teams/{risk['id']}", json={"color": 1}).status_code == 403
    login(ADMIN)
    try:
        r = client.patch(f"/teams/{risk['id']}", json={"color": (risk["color"] + 1) % 8})
        assert r.status_code == 200
        log = client.get("/admin/events", params={"limit": 1}).json()["events"][0]
        assert log["kind"] == "team_colour_set" and log["target"]["before"] == risk["color"]
    finally:
        client.patch(f"/teams/{risk['id']}", json={"color": risk["color"]})


def test_on_the_demo_password_free_sign_in_opens_only_the_shared_demo_people(
    client, login, demo_on
):
    """Someone the shared admin invites is a real person. On the demo their account opens only with
    their own Google sign-in, never from the sign-in cards' password-free route."""
    login(ADMIN)
    email = f"real-{uuid4().hex[:8]}@example.com"
    assert client.post("/admin/users", json={"email": email, "name": "Real"}).status_code == 201
    client.cookies.clear()
    assert client.post("/auth/dev-login", json={"email": email}).status_code == 404
    assert client.post("/auth/dev-login", json={"email": ADMIN}).status_code == 200
    assert client.post("/teams", json={"name": "  "}).status_code in (
        403,
        422,
    )  # never a blank team
