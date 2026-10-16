# Known limitations

Kept current with every PR. Each line says what is missing and why it was left out.

- The live site is a public demo. Anyone can sign in as any demo person, including the admin, and
  everyone shares the same data until the nightly reset. It shows the system; it is not a place for
  real work. A real deployment leaves `DEMO_MODE` and `DEV_LOGIN` off.
- In demo mode the nightly reset empties every table, history included. Outside demo mode history is
  never deleted.
- Sign-in is Google only. There is no email-and-password sign-in and no other provider. A
  user without a Google account for their invited email cannot get in.
- Local development signs in as any seeded user with no password. That sign-in is off unless
  `DEV_LOGIN=true`, so a deployment never offers it by accident.
- Sign-in rate limits key on the client address from `X-Forwarded-For`. That is trustworthy
  behind a platform that sets the header itself, but not when the API is exposed directly.
- Cross-site writes are refused by checking the `Origin` header against `ALLOWED_ORIGINS`,
  together with a `SameSite=Lax` cookie. There is no separate CSRF token.
- Rate limits are fixed windows per user (writes) counted in Postgres. A burst at the edge of
  two windows can briefly allow up to twice the limit.
- No frontend unit tests. The UI's risky behaviour is the conflict across two sessions, which
  was checked by hand in two browsers; the server rules it depends on are tested.
- On Vercel there is no always-on worker. Each API instance drains the outbox after it answers a
  request, so notifications and retries advance whenever someone uses the app; the scheduled
  sweep runs once a day on the free plan. An idle app can leave a failed job waiting until then.
- Google sign-in works on the production URL and localhost only. Preview deployments get a new
  URL each time, which is not registered with Google.
- No live push. The item page polls every 15 seconds; a change by someone else shows up on
  the next poll or on the next write (as a 409).
- Notifications are in-app only. No email or chat delivery.
- No attachments, no bulk edits, no soft delete.
- Invitations are not emailed. The admin creates the person and tells them to sign in with
  Google; there is no email delivery in the system.
- Only admins manage people and teams. A team lead cannot invite someone to their own team.
- Full-text search is Postgres `tsvector` with English stemming. No fuzzy matching, no
  relevance tuning.
- Dead-lettered outbox rows need a human; there is no retry button yet.
- The inbox's stale threshold is fixed at three days and its buckets show the first 50 only;
  there is no paging inside a bucket, the list page with filters is the way to see more.
- Idempotency keys are kept for 24 hours. A retry later than that runs the request again.
- Notifications arrive up to two seconds after the change, when the worker next polls, and
  the recipients are worked out then, not at the moment of the change. Someone who starts
  watching an item in that window is told about the change that preceded it.
- A replayed idempotent response is the same JSON as the first one, but not the same bytes:
  Postgres stores jsonb with its own key order.
- The seed only loads into an empty database. To reseed, drop the Postgres volume
  (`docker compose down -v`) and start again.
