# Known limitations

Kept current with every PR. Each line says what is missing and why it was left out.

- No real sign-on. Dev login against seeded users; the session cookie is signed but there is
  no password, no SSO, no session revocation list.
- No live push. The item page polls every 15 seconds; a change by someone else shows up on
  the next poll or on the next write (as a 409).
- Notifications are in-app only. No email or chat delivery.
- No attachments, no bulk edits, no soft delete, no role editor. Roles are seeded.
- Full-text search is Postgres `tsvector` with English stemming. No fuzzy matching, no
  relevance tuning.
- Dead-lettered outbox rows need a human; there is no retry button yet.
- Idempotency keys are kept for 24 hours. A retry later than that runs the request again.
- Notifications arrive up to two seconds after the change, when the worker next polls, and
  the recipients are worked out then, not at the moment of the change. Someone who starts
  watching an item in that window is told about the change that preceded it.
- A replayed idempotent response is the same JSON as the first one, but not the same bytes:
  Postgres stores jsonb with its own key order.
- The seed only loads into an empty database. To reseed, drop the Postgres volume
  (`docker compose down -v`) and start again.
