from app import db


def test_seed_runs_once_and_never_duplicates(client):
    # The app has already seeded on boot; a second call must be a no-op, not a second copy.
    assert db.seed_if_empty() is False
    with db.pool.connection() as conn:
        c = conn.execute(
            "select (select count(*) from users) as users, (select count(*) from teams) as teams,"
            " (select count(*) from work_items) as items, (select count(*) from work_item_events) as events"
        ).fetchone()
    assert (c["users"], c["teams"]) == (40, 8) and c["items"] >= 20000  # tests create items; nothing can delete them
    assert c["events"] > 100_000


def test_seeded_version_equals_event_count(client):
    # apply_change() will hold this invariant for every later write, so the seed must start from it.
    with db.pool.connection() as conn:
        bad = conn.execute(
            "select count(*) from work_items w"
            " where version <> (select count(*) from work_item_events e where e.item_id = w.id)"
        ).fetchone()["count"]
    assert bad == 0
