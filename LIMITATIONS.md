# Known limitations

Kept current with every PR. Each entry says what is missing, **why** it was left out, and what
adding it would take. The reasoning behind what *was* built is in
[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md).

## The public demo

- **Anyone can sign in as any demo person, the admin included.**
  Why: the demo exists so a reviewer can see every role in a minute, without accounts. The
  shared accounts cannot be changed by anyone (D-19), and a visitor's own account and workspace
  are hidden from them (D-26).
  A real deployment leaves `DEMO_MODE` and `DEV_LOGIN` off, and then only invited Google accounts
  get in.
- **Everything is deleted every night at 04:00 Singapore time.** This includes history, visitors'
  workspaces and their accounts.
  Why: strangers share the demo. A nightly wipe keeps the seed clean, stops data from piling up,
  and keeps the privacy page true without a retention policy per row. It is also the only time
  history is ever deleted: outside demo mode it is never removed.
  To keep workspaces longer, the reset would clear only the demo's own teams. Workspaces would
  then need their own expiry.
- **The shared admin card can learn that an email address has signed in.** Inviting that
  address answers "already invited".
  Why: telling an admin that an address is taken is normal. On the demo the admin is shared, so
  it leaks one bit. They still cannot see the account, its teams or its work.
  A generic answer for private accounts would close it.
- **A visitor's workspace is a set of teams they lead.** It has no name, no admin of its own and
  no billing.
  Why: everything a person sees was already filtered by team membership, so private teams gave
  isolation without a new layer (D-27). Real organisations would put an organisation id on every
  table and a check in every query, where one miss leaks one company's data to another.
- **Team keys are unique across the whole site.** So a visitor's "Payments" team reads `PAY2`,
  because the demo already has `PAY`.
  Why: search by key (`PAY-12`) looks the key up directly, so it must be unique.
  Keys scoped to a workspace would need workspaces as a real layer first.

## Signing in and invitations

- **Invitations are not emailed.** Nobody gets an email when an admin invites them or a lead
  adds them.
  Why: sending email to anyone needs a domain you own, verified with an email provider such as
  Resend by adding DNS records. A `vercel.app` address cannot be verified, and without a
  verified domain a provider only delivers to the account owner's own inbox. On the public demo
  there is a second reason: anyone can sign in and type any address, so the site would need
  limits per sender and per recipient before it could send mail without becoming a spam relay.
  Instead, after a lead adds someone, the app shows a ready message with a Copy invite button
  for the lead to send themselves. An admin who invites someone tells them to sign in with Google.
  To add it: a domain, the Resend integration, and an `invite_email` outbox handler. The outbox
  gives retries and a unique key per invite, so a retry never sends twice (D-28).
- **Notifications are in the app only.** No email, chat or push.
  Why: same as above. Delivery would be one more outbox handler beside the one that writes
  in-app notifications.
- **Sign-in is Google only.** There are no passwords and no other provider. Someone without a
  Google account for their invited email cannot get in.
  Why: passwords bring resets, breach handling and storage of secrets. Google proves the email
  is real at no cost (D-16).
  Another provider would be a second token check feeding the same invite rules.
- **Off the demo, only admins let new people in.** A lead can add someone to their own team only
  if that person already has an account.
  Why: in a real company, who gets access is an admin's call. Who is on a team is the lead's.
- **The password-free sign-in exists at all.** It covers local development, the tests and the
  demo's role cards.
  Why: it lets anyone try a role in one click. It is off unless `DEV_LOGIN=true`, and it refuses
  any real person's account (D-26), so it can never sign in as a visitor.
- **Google sign-in works on the production URL and localhost only.** Preview deploys get a new
  URL each time, and Google only accepts addresses registered in advance.
- **The sign-in rate limit trusts `X-Forwarded-For`.** That is safe behind Vercel, which sets the
  header itself, but not if the API is exposed directly.
- **There is no separate CSRF token.** Cross-site writes are refused by checking `Origin` against
  `ALLOWED_ORIGINS`, together with a `SameSite=Lax` cookie.
  Why: both are enforced by the browser for every request that matters here, and a token adds a
  round trip and state for no extra protection on a same-origin app.
- **Write limits use fixed one-minute windows** counted in Postgres. A burst at the edge of two
  windows can briefly allow up to twice the limit.
  Why: one upsert per request is shared across every API instance and needs no extra service.
  A sliding window would cost a range query per write.

## Running on Vercel

- **There is no always-on worker.** Each API instance drains the outbox after it answers a
  request, so notifications and retries move whenever someone uses the app. The scheduled sweep
  runs once a day, the most the free plan allows. An idle app can leave a failed job waiting until
  then.
  Why: Vercel runs functions, not long-lived processes (D-18). Locally the worker runs every two
  seconds.
- **The first request after a quiet spell is slower,** by about a second, while Neon wakes the
  suspended database. Connections it dropped are replaced at once, so that request still succeeds
  (D-30).
  Why: the free Neon plan suspends an idle database to save cost. Keeping it awake would need a
  paid plan or a request every few minutes.
- **Preview deploys have no database.** Their pages load, but their API does not.
  Why: the only database is the production one, and previews must never write to it. Neon can
  give each preview its own branch of the database; that setting is not turned on yet.
- **Live updates check every 1.5 seconds per open tab** instead of being pushed by the database.
  Only the open item and the notification count update live. The inbox and the list refresh on
  those events or once a minute.
  Why: Postgres `LISTEN/NOTIFY` does not pass through Neon's connection pooler (D-20). Each
  check is one small indexed query.

## Data and lists

- **No attachments, bulk edits or soft delete.**
  Why: none of them is needed to show the hard parts of the brief: conflicts, retries, history
  and authorization. Each would go through `apply_change()` like every other write.
- **The dashboard counts are computed on every request,** not kept as running totals.
  Why: each count is an index scan and stays quick at this size, and computed counts can never
  drift. At millions of items they would become counters updated inside `apply_change()`.
- **Creating items on one team takes turns** on that team's counter row, because item keys count
  per team (`RSK-1204`).
  Why: the keys stay gapless and in order (D-23). People create items far slower than the lock
  is held. A bulk import into one team would queue.
- **A team's code cannot be edited** after the team is created.
  Why: every item key on the team is built from it, and links and messages quote those keys.
  Renaming would need the old code to keep working as an alias.
- **Team colours repeat after eight teams.** Each team stores one of eight colours, picked when it
  is created as the one least used among the teams its creator can see, so anyone's first eight
  teams all look different. A ninth shares a colour, and nobody can pick a colour by hand yet.
  Why: eight is as many categorical colours as stay apart for colour-blind readers (D-31).
- **Search is Postgres full-text with English stemming.** There is no fuzzy matching and no
  relevance tuning.
  Why: it needs no extra service and is indexed (D-06). A dedicated engine is worth it only if
  relevance becomes the complaint.
- **The inbox shows the first 50 of each tab,** and "gone quiet" is fixed at three days.
  Why: the inbox is for what needs you now. The items list, with filters and paging, is the way
  to see everything.
- **The seed only loads into an empty database.** To reseed locally, drop the Postgres volume
  (`docker compose down -v`) and start again.
  Why: seeding over real data would be destructive. On the demo the nightly reset reseeds.

## Background jobs

- **A job that fails eight times stops** and waits for an admin to retry it from the Admin page.
  Nothing alerts anyone. The count shows only on that page's tab.
  Why: retrying forever hides a broken handler. Stopping makes the failure visible (D-21).
  Alerting needs somewhere to send the alert, which is the same missing email setup.
- **Finished jobs are deleted after seven days.** Failed ones are kept until retried.
- **Idempotency keys are kept for 24 hours.** A retry later than that runs the request again.
  Why: clients retry within seconds or minutes. A day covers that with a small table.
- **Notifications arrive up to two seconds after the change,** and who receives them is worked
  out then, not at the moment of the change. Someone who starts watching an item in that window
  is told about the change just before.
- **A replayed idempotent response** is the same JSON as the first one, but not the same bytes,
  because Postgres stores `jsonb` with its own key order.

## Testing

- **The browser tests cover what only a browser shows:** a stale edit, a claim race, live
  updates, one item's whole workflow across four people, and a layout check of every screen and
  dialog at desktop and phone width. The layout check catches text drawn over text, sideways
  scroll and labels too long for their buttons. It cannot judge whether a screen looks good, so
  that is still checked by eye from its screenshots. There are no component tests.
  Why: the server rules are tested directly against real Postgres, more than a hundred tests.
- **The workspace flow is not in the automated browser tests.**
  Why: it starts with a real Google sign-in, which a test cannot do. It was driven by hand in a
  real browser, and its rules are covered by API tests.
- **Tests never run against the live site's data.**
  Why: the demo is shared, and nothing can be deleted, so a test item would sit in every
  visitor's lists until the nightly reset.
