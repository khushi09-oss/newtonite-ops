"""The outbox worker process: poll every two seconds, process a batch, purge old keys and counters.
Run several side by side; FOR UPDATE SKIP LOCKED keeps them on different rows."""

import logging
import time

from app import db, limits
from app.outbox import (
    BATCH,
    HANDLERS,
    process_batch,
    purge_idempotency_keys,
    warn_about_unknown_kinds,
)

log = logging.getLogger("worker")
POLL_SECONDS = 2


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    db.pool.open()
    log.info("worker started; handling %s", sorted(HANDLERS))
    tick = 0
    while True:
        claimed = 0
        try:
            with db.pool.connection() as conn:
                claimed = process_batch(conn)
                if tick % 30 == 0:
                    purge_idempotency_keys(conn)
                    limits.purge(conn)
                    warn_about_unknown_kinds(conn)
        except Exception:
            log.exception("worker loop failed; retrying after the poll interval")
        tick += 1
        if claimed < BATCH:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
