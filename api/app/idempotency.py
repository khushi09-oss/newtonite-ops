"""Idempotency-Key handling (D-03). The first request with a key runs the handler and stores the
response; a repeat with the same key and body replays it; the same key with a different body is 422."""

import hashlib
import json
from collections.abc import Callable

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb


def idempotent(
    conn,
    user: dict,
    key: str | None,
    scope: str,
    body,
    handler: Callable[[], dict],
    status: int = 200,
    required: bool = False,
) -> JSONResponse:
    """Run handler() at most once per (user, key). Must be called inside the caller's transaction so the
    stored response commits together with the write it describes."""
    if key is None:
        if required:
            raise HTTPException(422, "Idempotency-Key header is required")
        return JSONResponse(jsonable_encoder(handler()), status)
    digest = hashlib.sha256(
        json.dumps([scope, jsonable_encoder(body)], sort_keys=True).encode()
    ).hexdigest()
    # Two identical requests at the same moment: the second waits here until the first commits,
    # then finds the stored row instead of running the handler again.
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"{user['id']}:{key}",))
    stored = conn.execute(
        "select request_hash, status, body from idempotency_keys where key = %s and user_id = %s",
        (key, user["id"]),
    ).fetchone()
    if stored is not None:
        if stored["request_hash"] != digest:
            raise HTTPException(422, "Idempotency-Key was already used with a different request")
        return JSONResponse(stored["body"], stored["status"])
    result = jsonable_encoder(handler())
    conn.execute(
        "insert into idempotency_keys (key, user_id, request_hash, status, body) values (%s, %s, %s, %s, %s)",
        (key, user["id"], digest, status, Jsonb(result)),
    )
    return JSONResponse(result, status)
