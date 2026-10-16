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
- The browser tests cover the three behaviours that matter most across two people: a stale edit,
  a claim race, and live updates. Other screens are checked by hand. There are no component tests.
- On Vercel there is no always-on worker. Each API instance drains the outbox after it answers a
  request, so notifications and retries advance whenever someone uses the app; the scheduled
  sweep runs once a day on the free plan. An idle app can leave a failed job waiting until then.
- Google sign-in works on the production URL and localhost only. Preview deployments get a new
  URL each time, which is not registered with Google.
- Preview deployments have no database, so only their pages can be reviewed there. Giving each
  preview its own copy of the database is a Neon setting not yet turned on.
- Live updates check for changes every 1.5 seconds per open tab rather than being pushed by the
  database, because Postgres notifications do not pass through Neon's pooler. Each open tab costs
  one small query per check. Only the open item and the notification count update live; the
  inbox and the list refresh on those events or once a minute.
- Notifications are in-app only. No email or chat delivery.
- No attachments, no bulk edits, no soft delete.
- Invitations are not emailed. The admin creates the person and tells them to sign in with
  Google; there is no email delivery in the system.
- Only admins manage people and teams. A team lead cannot invite someone to their own team.
- The dashboard counts are computed on every request rather than kept as running totals. Each
  is an index scan, so they stay quick at this size; at millions of items they would move to
  counters updated in `apply_change()`.
- Item keys count per team (`RSK-1204`), so creating items on one team takes turns on that team's
  counter row. That is invisible at the pace people type; a bulk import into one team would queue.
- A team's code is set when the team is created and cannot be edited yet.
- Full-text search is Postgres `tsvector` with English stemming. No fuzzy matching, no
  relevance tuning.
- A background job that fails eight times stops and waits for an admin to retry it from the Admin
  page. Nothing alerts anyone that it happened; the count is only seen there.
- Finished background jobs are deleted after seven days. Failed ones are kept until retried.
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
