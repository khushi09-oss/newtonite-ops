"""Outbox processing (D-04, D-11), shared by the worker process and the API. Locks a batch with FOR UPDATE SKIP LOCKED so several
workers can run side by side, runs each handler inside a savepoint, retries with exponential backoff and
marks a row dead after MAX_ATTEMPTS with the error stored. A kill mid-batch rolls the transaction back
and the rows are simply picked up again; handlers are idempotent, so that is safe."""

import logging

from app import db
from app.notifications import notify

log = logging.getLogger("outbox")

HANDLERS = {"item_event": notify}
MAX_ATTEMPTS = 8
BATCH = 20


def claim_batch(conn, kinds, limit: int = BATCH) -> list[dict]:
    """Lock up to `limit` due rows of the kinds this worker knows. Rows another worker holds are skipped,
    not waited for. Must run inside a transaction; the locks last until it ends."""
    return conn.execute(
        "select id, kind, payload, attempts from outbox"
        " where status = 'pending' and next_attempt_at <= now() and kind = any(%s)"
        " order by id limit %s for update skip locked",
        (list(kinds), limit),
    ).fetchall()


def process_batch(conn, handlers=HANDLERS, limit: int = BATCH) -> int:
    """One transaction: claim a batch, run each handler in its own savepoint, record the outcome per row.
    Returns how many rows were claimed."""
    with conn.transaction():
        rows = claim_batch(conn, handlers, limit)
        for row in rows:
            try:
                with conn.transaction():
                    handlers[row["kind"]](conn, row["payload"])
                conn.execute(
                    "update outbox set status = 'done', processed_at = now() where id = %s",
                    (row["id"],),
                )
            except Exception as exc:
                log.exception(
                    "outbox row %s (%s) failed on attempt %s",
                    row["id"],
                    row["kind"],
                    row["attempts"] + 1,
                )
                conn.execute(
                    "update outbox set attempts = attempts + 1, last_error = %s,"
                    " next_attempt_at = now() + make_interval(secs => power(2, attempts + 1)),"
                    " status = case when attempts + 1 >= %s then 'dead' else 'pending' end"
                    " where id = %s",
                    (f"{type(exc).__name__}: {exc}"[:2000], MAX_ATTEMPTS, row["id"]),
                )
    return len(rows)


def purge_idempotency_keys(conn) -> None:
    conn.execute("delete from idempotency_keys where created_at < now() - interval '24 hours'")


def warn_about_unknown_kinds(conn, handlers=HANDLERS) -> None:
    """Rows of a kind no worker handles would wait forever in silence. Say so in the log instead."""
    for row in conn.execute(
        "select kind, count(*) from outbox where status = 'pending' and kind <> all(%s) group by kind",
        (list(handlers),),
    ):
        log.warning("%s pending outbox rows of kind %r have no handler", row["count"], row["kind"])


def drain(max_batches: int = 1) -> int:
    """Process up to `max_batches` batches on a pooled connection. Used by the API after a response
    and by the daily sweep, where no worker process runs. Failures are logged, never swallowed."""
    done = 0
    try:
        with db.pool.connection() as conn:
            for _ in range(max_batches):
                n = process_batch(conn, HANDLERS)  # looked up now, so tests can swap handlers
                done += n
                if n < BATCH:
                    break
    except Exception:
        log.exception("outbox drain failed; the rows stay pending and the next drain retries them")
    return done
