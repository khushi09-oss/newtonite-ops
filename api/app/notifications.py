"""Watchers, the outbox handler that turns item events into notifications, and the notification routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app import db
from app.auth import current_user
from app.items import require_access

router = APIRouter()
LIST_CAP = 50


def notify(conn, payload: dict) -> None:
    """Outbox handler for item_event. One notification per watcher, assignee and reporter, never the
    actor. Safe to run twice: (user_id, event_id) is unique and the insert does nothing on conflict."""
    conn.execute(
        "insert into notifications (user_id, event_id)"
        " select distinct r.user_id, %(event_id)s from ("
        "   select user_id from watchers where item_id = %(item_id)s"
        "   union select assignee_id from work_items where id = %(item_id)s"
        "   union select reporter_id from work_items where id = %(item_id)s"
        " ) r where r.user_id is not null and r.user_id <> %(actor_id)s"
        " on conflict do nothing",
        payload,
    )


@router.post("/items/{item_id}/watch", status_code=204)
def watch(item_id: UUID, user: dict = Depends(current_user)):
    with db.pool.connection() as conn:
        require_access(conn, user, item_id)
        conn.execute(
            "insert into watchers (item_id, user_id) values (%s, %s) on conflict do nothing",
            (item_id, user["id"]),
        )


@router.delete("/items/{item_id}/watch", status_code=204)
def unwatch(item_id: UUID, user: dict = Depends(current_user)):
    with db.pool.connection() as conn:
        require_access(conn, user, item_id)
        conn.execute(
            "delete from watchers where item_id = %s and user_id = %s", (item_id, user["id"])
        )


@router.get("/notifications")
def list_notifications(
    user: dict = Depends(current_user),
    unread: bool = False,
    limit: int = Query(LIST_CAP, ge=1, le=LIST_CAP),
):
    cond = " and n.read_at is null" if unread else ""
    with db.pool.connection() as conn:
        rows = conn.execute(
            "select n.id, n.read_at, n.created_at, e.kind, e.note, e.seq, e.item_id, w.title,"
            " u.name as actor_name"
            " from notifications n join work_item_events e on e.id = n.event_id"
            " join work_items w on w.id = e.item_id join users u on u.id = e.actor_id"
            f" where n.user_id = %s{cond} order by n.id desc limit %s",
            (user["id"], limit + 1),
        ).fetchall()
    return {"notifications": rows[:limit], "capped": len(rows) > limit}


@router.post("/notifications/{notification_id}/read", status_code=204)
def mark_read(notification_id: int, user: dict = Depends(current_user)):
    with db.pool.connection() as conn:
        done = conn.execute(
            "update notifications set read_at = coalesce(read_at, now()) where id = %s and user_id = %s",
            (notification_id, user["id"]),
        ).rowcount
    if not done:
        raise HTTPException(404, "notification not found")
