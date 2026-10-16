"""Fixed-window rate limits counted in Postgres, so they hold across every API instance."""

from fastapi import HTTPException

from app import db


def check(key: str, limit: int, window_seconds: int = 60) -> None:
    """Count one hit for `key` in the current window; 429 with Retry-After once it passes `limit`.
    Runs on its own connection and commits at once, so a refused request still counts."""
    with db.pool.connection() as conn:
        row = conn.execute(
            "insert into rate_limits (key, window_start)"
            " values (%(key)s, date_bin(%(win)s::interval, now(), timestamptz 'epoch'))"
            " on conflict (key, window_start) do update set hits = rate_limits.hits + 1"
            " returning hits,"
            " ceil(extract(epoch from window_start + %(win)s::interval - now()))::int as retry",
            {"key": key, "win": f"{window_seconds} seconds"},
        ).fetchone()
    if row["hits"] > limit:
        raise HTTPException(
            429, "too many requests, slow down", headers={"Retry-After": str(max(row["retry"], 1))}
        )


def purge(conn) -> None:
    conn.execute("delete from rate_limits where window_start < now() - interval '1 hour'")
