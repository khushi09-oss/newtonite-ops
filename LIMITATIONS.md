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
