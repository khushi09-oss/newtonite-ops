# Newtonite Ops

An internal work tracker for operational requests: customer issues, incidents, payments to
investigate, compliance requests, approvals. Built for the Newtonite Software Engineering
Challenge ("Operations Under Pressure"). It wears Newtonite's own mark, colours and type from
[newtonite.in](https://www.newtonite.in/) so it looks like a Newtonite tool; it is a challenge
submission, not an official Newtonite product, and the sign-in page says so.

The system is small on purpose. The effort went into the behaviours that break under real
use, each enforced by the server and each covered by a test named after the risk:

| What could go wrong | What happens instead | Where it is enforced |
|---|---|---|
| Two people take the same item | One claim wins; everyone else gets 409 naming the winner | One guarded `UPDATE` in `apply_change()` (D-02) |
| Someone edits an old copy | 409 with the current item and who changed what | `version` in every write's `WHERE` clause (D-02, D-10) |
| A request is sent twice | The stored first response is replayed; a reused key with a different body is refused | `Idempotency-Key` table and a per-key lock (D-03, D-12) |
| A workflow step is skipped | 422 with the moves that are allowed; approvers cannot approve their own request | Transition table as data (D-05) |
| A notification is lost or doubled | Queued in the same transaction, retried, dead-lettered, unique per user and event | Transactional outbox and worker (D-04, D-11) |
| An outsider probes for items | 404, the same answer as for an id that does not exist | Team membership joined inside every query (D-05) |

## Try the live demo

**https://newtonite-ops-vert.vercel.app**

The sign-in page offers two ways in.

- **Explore the demo.** Pick one of the demo people to see the app from their seat: the admin, the
  Risk lead, a Risk member, or a Compliance lead who can only view Risk. The data is made up and
  shared by everyone. Open the site in two browsers as two different people to see the conflict
  handling below.
- **Start your own workspace.** Sign in with Google and start from empty. Create a team, add
  teammates by their Google email, and run work through it from new to closed. It is private to
  you and the people you add; even the demo's admin cannot see it.

Everything is cleared every night at 04:00 Singapore time.

The demo runs with `DEMO_MODE=true`. Without it, the same code is invite-only: Google sign-in
admits only people an admin has invited, and there is no passwordless sign-in.

## Every feature, and why it works this way

Each part of the app in plain words: what it does, and why it was built that way. The `D-NN`
numbers point to the full reasoning in [ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md).

### Getting in

- **Two ways in on the demo.** "Explore the demo" signs you in as one of four demo people with
  one click. "Start your own workspace" signs you in with Google to an empty, private workspace.
  *Why:* a reviewer should see every role in a minute without making accounts, and should also
  be able to try the product the way a real team starts, from nothing (D-19, D-27).
- **Google sign-in.** The API checks Google's token itself (signature, audience, expiry, a
  verified email), then looks the person up. Off the demo, only invited emails get in. The first
  sign-in ties the email to that Google account, so a reassigned address cannot take it over.
  *Why:* no passwords to store or reset, and Google has already proved the address (D-16).
- **Sessions are rows in the database,** referenced by a signed cookie that lasts seven days.
  *Why:* signing out, or deactivating someone, ends their sessions at once. A stateless cookie
  would stay valid until it expired (D-15).
- **Privacy on the demo.** A visitor's own account and workspace are marked private. The shared
  demo admin cannot see them, and the password-free demo sign-in cannot reach them.
  *Why:* the demo's admin account is public. Without this, any visitor could read other visitors'
  email addresses or sign in as them (D-26). The public `/privacy` page says what is kept.

### Seeing what needs you

- **Dashboard.** It shows five counts: assigned to you, waiting for your approval, urgent and
  unclaimed, overdue, and quiet for three days. Below them are open work by team, created against
  resolved over 14 days, and recent activity. Each count links to the list behind it.
  *Why:* the first screen should answer "what needs me?", not show a list of everything. Each
  number is one indexed query scoped to your teams (D-22).
- **Inbox.** The same questions as tabs, each with its count, holding the items themselves.
  It opens on the first tab with work in it.
  *Why:* four small indexed queries are fast at any size, where one query over everything is not
  (D-13).
- **Notifications.** They are grouped by day, with the item's key on each. "Mark all as read"
  clears only what was on your screen.
  *Why:* a notification that arrives while you read should not be cleared unseen (D-25).

### Working on items

- **Item keys.** Every item has a short key from its team, like `RSK-1204`, counted per team with
  no gaps. You can search by key.
  *Why:* people quote keys in chat. A per-team counter on the team row keeps them in order even
  under concurrent creates (D-23).
- **Items list.** Items are grouped by workflow status, each group with its exact count, and
  finished work starts folded. You can filter by team, priority, your own work and overdue, and
  search in full text.
  *Why:* the grouping shows where work stands at a glance. Each group pages on its own cursor,
  so nothing uses offset paging and nothing slows down as data grows (D-06, D-24).
- **The workflow.** New, triaged, in progress, awaiting approval, approved, resolved, closed.
  Only the moves in the transition table are allowed. A lead approves, never their own request.
  Rejecting needs a note. Claiming new or triaged work starts it; claiming anything else only
  takes ownership, so an item waiting for approval stays waiting.
  *Why:* the rules live in one table on the server, so the UI cannot skip a step. A refused move
  answers 422 with the moves that are allowed (D-05).
- **Claiming.** Claiming is one guarded database statement. When two people claim at once,
  exactly one wins and the other is told who did.
  *Why:* a read-then-write in the app would let both win (D-02).
- **Conflicts.** Every change carries the version it was based on. If someone changed the item
  first, your change is rolled back and a strip says who changed what, with Reload and Retry.
  *Why:* silently overwriting someone's work is the bug this brief is about (D-02, D-10, D-14).
- **Safe retries.** Every write carries an idempotency key. Sent again with the same key (a double
  click, or a retry after a lost answer), it returns the first answer instead of running twice or
  reporting a conflict with yourself. The few writes that are harmless to repeat, like watching an
  item, are listed with their reason, and a test fails on any new write that is neither.
  *Why:* operational work is often entered under pressure on bad connections (D-03, D-12).
- **The item page.** A Next step card says where the item stands and gives one main button for
  the move that takes it forward. A Details card lists the facts, and the activity feed sets
  comments apart from status changes.
  *Why:* the next action should be obvious, and the buttons are drawn from what the server says
  you may do, never guessed by the page.
- **History.** Every change writes one history event in the same transaction, and the database
  refuses to edit or delete history.
  *Why:* the item's state can always be explained, and nobody can quietly rewrite it (D-01, D-09).
- **Live updates.** The open item and the notification count update within about two seconds
  without a reload. If the live stream drops, polling every 15 seconds takes over.
  *Why:* two people working the same item should see each other's changes (D-20).

### Teams and people

- **Roles per team.** A viewer follows the work, a member does it, and a lead approves and
  manages the team. Admins manage everyone, and see every team that is not a private workspace.
  The sidebar lists the teams you are on, and for an admin also "Other teams".
  *Why:* the same person often works on one team and only watches another. Which teams you can see,
  and where you may create items, is one rule on the server that `/me` hands to every screen, so the
  sidebar, filters and dialogs always match the data (D-29).
- **Team page.** Anyone on a team sees who else is on it. The lead adds people by email,
  changes roles and removes people. Removing someone releases their open items, and each release
  is in that item's history.
  *Why:* leads know their own team best. Off the demo, admins still decide who gets an account
  at all (D-17, D-27).
- **Invites are a message you send.** After a lead adds someone, the app shows a ready message
  with a Copy invite button. Nothing is emailed.
  *Why:* sending email to anyone needs a domain you own, verified with an email provider. This
  project has no domain, and a `vercel.app` address cannot be verified. On a public demo,
  emailing any address a visitor types would also need anti-spam limits first (D-28).
- **Admin.** Four tabs: people, teams, background jobs and an admin log that cannot be edited.
  *Why:* every admin action is recorded, and failed jobs are visible and retryable instead of lost
  (D-17, D-21).

### Behind the scenes

- **Notifications go through an outbox.** The change and its notification job commit together.
  A worker delivers them, retries with growing waits, and stops after eight failures, keeping the
  error for an admin.
  *Why:* a notification is never lost when the change commits, and never sent for a change that
  did not (D-04, D-11).
- **On Vercel there is no worker process.** Each API instance drains the outbox after it answers,
  and a daily job sweeps the rest.
  *Why:* Vercel runs functions, not long-lived processes (D-18).
- **The demo resets every night** at 04:00 Singapore time, back to the same seed of 40 people,
  8 teams and 20,000 items.
  *Why:* strangers share it, so it must not drift or fill up (D-19).

## When things fail

What happens in each failure the system was built for, and where the reasoning is. The tests
that guard these are listed under "Run the tests", and most rows can be tried against the running
stack.

| Failure | What happens | Why |
|---|---|---|
| Two people change the same item | The version check lets one win. The other gets 409 with who changed what; the page rolls back and offers Reload or Retry. | D-02, D-14 |
| A write is sent twice (a double click, or a retry after a lost answer) | The same key and body replay the first answer; the same key with a different body is 422. Writes that are harmless to repeat are listed in a test. | D-03, D-34 |
| Bad input (a `null`, a wrong type, an unknown status) | 422 naming the field. A test sends `null` in every field of every write and fails on any 500. | `test_guards.py` |
| The database is down or waking up | A request waits up to 10 seconds, then gets 503 "The database is not answering". Every write is one transaction, so nothing is half-saved. The next request after it is back works. | D-35 |
| The database drops idle connections (Neon does) | A dead connection is replaced at once instead of being handed to a request. | D-30 |
| The worker is killed mid-batch | Its transaction rolls back and the row locks go with it; the next poll picks the rows up again. Handlers are idempotent, so nobody is notified twice. | D-04, D-11 |
| A notification job keeps failing | Retried after 2, 4, 8 and so on up to 128 seconds. On the eighth failure it stops with its error and waits on the Admin page, where Retry is safe. | D-04, D-21 |
| Nobody uses the app on Vercel, so no worker runs | Each response drains the outbox; a daily scheduled sweep catches the rest. | D-18 |
| The live-updates stream drops | The browser reconnects by itself, and pages poll every 15 seconds until it is back. | D-20 |
| Someone outside a team asks for its item | 404, the same answer as for an id that does not exist. | D-05 |
| A person is signed out or deactivated elsewhere | Their next request is 401 and their live stream closes. Deactivating someone also releases the work they owned, each release in that item's history. | D-15, D-17, D-20 |

## Run it

You need Docker with Compose. Python 3 is optional, for the demo script.

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---|---|
| Web | http://localhost:5173 |
| API | http://localhost:8000 (interactive docs at /docs) |
| Postgres | localhost:5432, user and password `ops` |

On first start the API applies its migrations and seeds 40 users, 8 teams, 20,000 work items
and about 156,000 history events. That takes a few seconds. The web container installs its
packages on first start, which takes about a minute. After that, starts are quick.

Sign in from the web page with the buttons for four useful roles: the admin, the Risk lead, a
Risk member, and a Compliance lead who can only view Risk. There is no password (see
Assumptions). Any other seeded person can be signed in from the shell with `/auth/dev-login`, as
in the curl example below; seeded emails are `first.last@newtonite.dev`.

## See it work

**In the browser.** Open http://localhost:5173 in two windows that do not share cookies, for
example one normal and one private.

1. Sign in as the Risk member in one and the Risk lead in the other.
2. Open the same unclaimed Risk item in both. Raise its priority as the lead.
3. Click Triage as the member without reloading. The change is rolled back and a strip says
   who changed what. Retry applies it on top of the new version.

**From the shell.** With the stack running:

```bash
python scripts/demo_race.py
```

It creates a fresh item and shows three things. Twenty claim requests from the five Risk
members arrive at the same instant, and one wins. One comment sent twice with the same key is stored once. An edit from an old
copy is refused and the title is unchanged. It exits 0 when all three hold. Use
`--claimers 50` for a bigger race.

**With curl.**

```bash
curl -c jar -X POST localhost:8000/auth/dev-login -H 'content-type: application/json' -d '{"email":"ava.okafor@newtonite.dev"}'
curl -b jar localhost:8000/me
curl -b jar localhost:8000/inbox
```

## Deploy to Vercel

The repository deploys as one Vercel project with two services (`vercel.json`): the Vite build
serves the pages, and FastAPI answers under `/api`. Functions run in Singapore (`sin1`), next to
the Neon database. There is no worker process on Vercel; with `DRAIN_OUTBOX_IN_REQUESTS=true` each
API instance processes the outbox after it answers a request, and a daily scheduled call to
`/api/internal/drain` sweeps anything left.

```bash
vercel link                       # once, on your own account
vercel integration add neon       # Postgres; sets DATABASE_URL
vercel env add SESSION_SECRET     # and the other production settings below
vercel deploy --prod              # or connect the repository and merge to main
```

The project is connected to this GitHub repository. A merge to `main` deploys production, and
every other pushed branch gets its own preview deploy. Preview deploys have no `DATABASE_URL`,
so they cannot touch the live database: their pages load but their API does not. Changes are
tested locally before they merge.

Settings for a real deployment: `DEV_LOGIN=false`, `SEED_ON_BOOT=false`, `COOKIE_SECURE=true`,
`DRAIN_OUTBOX_IN_REQUESTS=true`, `DB_POOL_MAX=5`, a random `SESSION_SECRET` and `CRON_SECRET`,
plus `GOOGLE_CLIENT_ID` and `ADMIN_EMAILS`. The site's own URLs are allowed origins automatically.
Migrations run on the first request after each deploy. The live demo differs on purpose: it
also sets `DEMO_MODE`, `DEV_LOGIN` and `SEED_ON_BOOT` to `true`, so the role cards work and the
seed comes back each night.

## Run the tests

```bash
docker compose exec api python -m pytest -q
```

Browser tests drive two real browsers as two people, with Playwright. With the stack running:

```bash
cd e2e && npm install && npx playwright install chromium
npx playwright test                                              # against the local stack
BASE_URL=https://newtonite-ops-vert.vercel.app npx playwright test  # live: read-only smoke only
```

Tests run against the real Postgres in the stack, not SQLite, because the claim race, the
idempotency lock and the outbox all depend on Postgres row locking. The API image carries its
own copy of the code, so after changing code or tests run `docker compose up --build -d`
before testing again.

| Risk | Test |
|---|---|
| Two claimers both win | `test_claim_race_exactly_one_winner` (20 threads, one 200, nineteen 409, one event) |
| A stale edit overwrites a newer one | `test_stale_version_rejected_with_current_state` |
| A double submit creates two of something | `test_idempotency_same_key_same_response`, `test_idempotency_concurrent_duplicates_run_once` |
| A reused key runs a different request | `test_idempotency_different_body_rejected` |
| A refused request poisons its key | `test_idempotency_does_not_store_failures` |
| The workflow can be skipped | `test_transition_table_rejects_unlisted_and_tells_allowed` |
| Someone approves their own request | `test_approver_cannot_be_requester` |
| Outsiders learn which ids exist | `test_nonmember_gets_404_on_every_item_route` (all ten routes) |
| A viewer acts | `test_viewer_cannot_claim`, `test_viewer_sees_but_cannot_create` |
| A change leaves no history | `test_every_mutation_writes_exactly_one_event` |
| A new route writes around the history | `test_only_apply_change_writes_work_items` |
| History is rewritten | `test_events_table_rejects_update_and_delete` |
| A notification is sent twice | `test_outbox_handler_twice_one_notification` |
| A failing job retries forever or leaves partial writes | `test_outbox_dead_after_max_attempts` |
| Two workers take the same job | `test_two_workers_skip_locked_no_double_processing` |
| A crash loses queued work | `test_outbox_rows_survive_a_crash_mid_batch` |
| Someone acts on a stale page and is not told why | Browser: `a stale edit is rolled back and the conflict strip names who changed what` |
| Two people claiming in the UI both think they won | Browser: `two people claim the same item and exactly one of them wins` |
| A change by someone else goes unseen | Browser: `a change by someone else appears on an open page without a reload` |
| Lists slow down as data grows | `test_list_uses_index`, `test_inbox_queries_use_indexes` (EXPLAIN shows no sequential scan) |
| The shared demo admin reads a visitor's workspace | `test_the_shared_admin_cannot_see_a_private_workspace_by_any_route` |
| Someone outside a workspace sees into it | `test_a_workspace_is_seen_only_by_its_lead_and_the_people_they_add` |
| A shared demo account joins or makes a workspace | `test_workspace_rules_keep_the_shared_demo_accounts_out` |
| A screen breaks for one of the roles on the live site | Browser: `smoke.spec.js`, read-only, every screen as each demo person |
| Text is drawn over other text, or a page scrolls sideways | Browser: `layout.spec.js`, read-only, every screen and dialog as each demo person, desktop and phone |
| The workflow breaks somewhere between new and closed | Browser: `workflow.spec.js`, one item through every status with a member, the lead, a viewer and an outsider |

## How it is built

```
  browser (React, TanStack Query)
     |  /api, same origin, so the session cookie needs no CORS setup
     v
  Vite dev server  ---->  FastAPI (api/app)  ---->  Postgres 16
                              |                       ^
                              | one transaction:      | FOR UPDATE SKIP LOCKED
                              | row + event + outbox  |
                              v                       |
                          apply_change()          worker.py  ---->  notifications
```

- **One write path.** Every change to a work item goes through `apply_change()`. It updates
  the row only if the client's version still matches, appends exactly one history event, and
  queues one outbox row, all in one transaction. History rows cannot be updated or deleted;
  database triggers refuse it.
- **Authorization in the query.** Every item query joins the caller's team membership, so an
  outsider's request finds nothing and gets 404. What a member may do is one function,
  `allowed_actions()`. Routes check it and the UI draws its buttons from it.
- **A dashboard per person.** `GET /dashboard` returns what needs you (assigned, awaiting your
  approval, urgent and unclaimed, overdue, quiet), open work by team and status, created against
  resolved over 14 days, and recent activity, all scoped to your teams and each on an index.
  Every item has a short key from its team, such as `RSK-1204` or `CMP-311`; searching for it finds
  the item directly. The item list can also show only overdue work.
- **Lists stay fast.** Nothing uses offset paging. The item list and each item's history page
  by cursor. The items page groups by status: each group pages on its own cursor, and the counts
  in its headers come from `GET /items/counts`, one grouped count on the same filters.
  Every list is capped and says `capped: true` when there is more. Filters land on one composite index. Search uses a
  Postgres full-text index. The inbox is four small indexed queries.
- **Async work is reliable.** The worker polls every two seconds and locks a batch so several
  workers can run side by side. It retries with exponential backoff and marks a job dead after
  eight failures, with the error stored. Each handler is safe to run twice. Run two workers
  with `docker compose up -d --scale worker=2`.
- **The UI is optimistic.** Actions show their result at once and roll back if the server
  refuses. A 409 becomes a sentence about who changed what, with Reload and Retry. The item
  page and the notification count update live: each tab holds one server-sent events stream,
  and polling every 15 seconds takes over if the stream drops.

The reasoning behind each choice, and what was traded away, is in
[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md). What is missing or weak is in
[LIMITATIONS.md](LIMITATIONS.md).

## Layout

| Path | What it is |
|---|---|
| `api/app/items.py` | Item routes, `apply_change()`, `require_access()`, the transition table, `allowed_actions()` |
| `api/app/idempotency.py` | `Idempotency-Key` handling: stored responses, replay, mismatch refusal |
| `api/app/notifications.py` | Watchers, the notification handler, the notification routes |
| `api/app/inbox.py` | The four inbox buckets |
| `api/app/dashboard.py` | The dashboard: counts, open work by team, the 14-day trend, recent activity |
| `api/app/teams.py` | What a lead does for their team, and a visitor's own workspace on the demo |
| `api/app/admin.py` | People, teams, background jobs and the admin log, for admins |
| `api/app/auth.py` | Sessions, the password-free demo sign-in, `GET /me` |
| `api/app/google.py` | Google sign-in: token checks and the invite rules |
| `api/app/demo.py` | Demo mode: visitors, protected demo accounts, the nightly reset |
| `api/app/stream.py` | Live updates as server-sent events |
| `api/app/outbox.py` | Outbox processing: batches, retries, dead letters, the drain used on Vercel |
| `api/worker.py` | The worker process: polls the outbox every two seconds |
| `vercel.json` | The Vercel project: two services, region, the daily sweep |
| `api/migrations/` | Plain SQL, applied in order on boot |
| `api/seed.sql` | The demo dataset, generated inside Postgres on first boot |
| `api/tests/` | Tests named after the risk they guard |
| `web/` | React, Vite and TanStack Query: sign-in, dashboard, inbox, items, item page, notifications, team page, admin, privacy |
| `e2e/` | Browser tests with two people in two real browsers |
| `scripts/demo_race.py` | The three guarantees, shown against the running stack |
| `docs/BUILD_PLAN.md` | The plan this was built to, step by step |

## Settings

Set in `.env` locally, or as environment variables wherever the API runs.

| Setting | Local default in `.env.example` | In production |
|---|---|---|
| `SESSION_SECRET` | `change-me-locally` | A long random string. The API refuses to start without one. |
| `DEV_LOGIN` | `true` | `false`. Sign in as anyone with no password; off unless switched on. |
| `SEED_ON_BOOT` | `true` | `false`. Fills an empty database with demo people and items. |
| `COOKIE_SECURE` | `false` | `true`, so the session cookie only travels over HTTPS. |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | The site's own URL. Writes from any other page are refused. |
| `WRITE_LIMIT_PER_MINUTE` | `300` | Writes per user per minute before 429. |
| `DRAIN_OUTBOX_IN_REQUESTS` | not set | `true` on Vercel, where no worker process runs. |
| `DEMO_MODE` | not set | `true` only for a public demo: any Google account gets its own private workspace, demo accounts are protected, data resets nightly. |
| `CRON_SECRET` | not set | A random string; the daily sweep must present it. |
| `DB_POOL_MAX` | `20` | Connections per API instance. `5` on Vercel. |
| `GOOGLE_CLIENT_ID` | empty, so Google sign-in is hidden | The OAuth client ID from Google Cloud. |
| `ADMIN_EMAILS` | empty | The first admins, comma separated. They become admins on their first Google sign-in. |

### Inviting people

**As an admin.** Open **Admin**, then **Invite person**. Enter their Google email, name, and a
team and role. Tell them to sign in with Google. The Admin page also creates teams, changes
roles, removes people from teams, deactivates or reactivates anyone, retries failed background
jobs, and keeps a log of every admin change that cannot be edited.

**As a team lead.** Open your team's items, then **Members**, and add someone by email. You get
a ready invite message with a **Copy invite** button to send them yourself. Off the demo, a lead
can only add people who already have an account.

Nothing is emailed in either case. Sending email needs a domain verified with an email provider,
which this project does not have; see [LIMITATIONS.md](LIMITATIONS.md). Removing or deactivating
someone releases their open items, and each release appears in that item's history.

### Setting up Google sign-in

1. In Google Cloud Console, open **APIs & Services**, then **OAuth consent screen**. Choose
   **External**, fill in the app name and support email, and add yourself as a test user.
   While the app is in testing, only test users can sign in; publish it to open it up.
   Publishing asks for a homepage URL and a privacy policy URL: use the site's own URL and
   `<site>/privacy`, a public page that says what the app keeps and for how long.
2. Open **Credentials**, then **Create credentials**, then **OAuth client ID**, type **Web
   application**.
3. Under **Authorized JavaScript origins** add `http://localhost:5173` and the site's own URL.
   No redirect URI is needed: the sign-in button returns a token to the page directly.
4. Put the client ID in `GOOGLE_CLIENT_ID` and your own email in `ADMIN_EMAILS`. Nothing else
   from Google is secret or needed.

Only invited people can sign in. Everyone else sees "has not been invited". The first time an
invited person signs in, their email is tied to that Google account, so if the address is ever
given to a different Google account, sign-in is refused.

## If something goes wrong

- **A port is taken.** Ports 5173, 8000 and 5432 must be free. Stop whatever holds them, or
  change the left-hand port in `docker-compose.yml`.
- **The web page does not load.** Give the web container its first-start minute. Watch it
  with `docker compose logs -f web`.
- **Everything says 401 after an update.** Sessions moved to the database, so cookies from
  before that change no longer work. Sign in again.
- **Start from a clean dataset.** `docker compose down -v` drops the database. The next
  `docker compose up` seeds it again.

## Assumptions

- People sign in with Google, and only invited emails get in. Locally, a dev sign-in lets you
  be any seeded user without Google. Authorization is enforced by the server in its queries.
- Notifications are stored in a table and shown in the app. Email or chat delivery would be
  one more outbox handler.
- One company, many teams. A user holds a role per team (viewer, member, lead) and may be a
  global admin.

## What comes next

- **Email.** With a domain of our own: invite emails and notification emails as outbox handlers,
  with limits per sender and per recipient so the public demo cannot be used to send spam.
- **Organisations.** A demo workspace is a set of private teams. Real organisations, with a
  name, their own admins and billing, mean scoping every team, item and query to one, with tests
  that try to cross between organisations on every route.
- **Operations.** Alerts when a background job fails for good, and metrics on queue depth and
  conflict rate.
- **At larger scale.** Partition the history table by time, give each preview deploy its own
  database copy, and move search to a dedicated engine only if relevance becomes the complaint.
