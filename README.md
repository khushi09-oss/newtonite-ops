# Newtonite Ops

An internal work tracker for operational requests: customer issues, incidents, payments to
investigate, compliance requests, approvals. Built for the Newtonite Software Engineering
Challenge ("Operations Under Pressure").

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

Sign in with any Google account, which puts you on the Support team, or pick one of the demo
people on the sign-in page to see other roles: the admin, the Risk lead, a Risk member, and a
Compliance lead who can only view Risk. The data is made up, shared by everyone, and reset every
night at 04:00 Singapore time. Open the site in two browsers as two different people to see the
conflict handling below.

The demo runs with `DEMO_MODE=true`. Without it, the same code is invite-only: Google sign-in
admits only people an admin has invited, and there is no passwordless sign-in.

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

Sign in from the web page with any seeded email, or use the buttons for four useful roles: the
admin, the Risk lead, a Risk member, and a Compliance lead who can only view Risk. There is no
password (see Assumptions). Seeded emails are `first.last@newtonite.dev`.

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
vercel deploy --prod
```

Production settings: `DEV_LOGIN=false`, `SEED_ON_BOOT=false`, `COOKIE_SECURE=true`,
`DRAIN_OUTBOX_IN_REQUESTS=true`, `DB_POOL_MAX=5`, a random `SESSION_SECRET` and `CRON_SECRET`,
plus `GOOGLE_CLIENT_ID` and `ADMIN_EMAILS`. The site's own URLs are allowed origins automatically.
Migrations run on the first request after each deploy.

## Run the tests

```bash
docker compose exec api python -m pytest -q
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
| Lists slow down as data grows | `test_list_uses_index`, `test_inbox_queries_use_indexes` (EXPLAIN shows no sequential scan) |

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
- **Lists stay fast.** Nothing uses offset paging. The item list and each item's history page
  by cursor. Every list is capped and says `capped: true` when there is more. Filters land on one composite index. Search uses a
  Postgres full-text index. The inbox is four small indexed queries.
- **Async work is reliable.** The worker polls every two seconds and locks a batch so several
  workers can run side by side. It retries with exponential backoff and marks a job dead after
  eight failures, with the error stored. Each handler is safe to run twice. Run two workers
  with `docker compose up -d --scale worker=2`.
- **The UI is optimistic.** Actions show their result at once and roll back if the server
  refuses. A 409 becomes a sentence about who changed what, with Reload and Retry. The item
  page, inbox and notification count refresh every 15 seconds.

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
| `api/app/auth.py` | Dev login, the signed session cookie, `GET /me` |
| `api/app/outbox.py` | Outbox processing: batches, retries, dead letters, the drain used on Vercel |
| `api/worker.py` | The worker process: polls the outbox every two seconds |
| `vercel.json` | The Vercel project: two services, region, the daily sweep |
| `api/migrations/` | Plain SQL, applied in order on boot |
| `api/seed.sql` | The demo dataset, generated inside Postgres on first boot |
| `api/tests/` | Tests named after the risk they guard |
| `web/` | React, Vite and TanStack Query: sign-in, inbox, items list, item page, notifications |
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
| `DEMO_MODE` | not set | `true` only for a public demo: any Google account gets in, demo accounts are protected, data resets nightly. |
| `CRON_SECRET` | not set | A random string; the daily sweep must present it. |
| `DB_POOL_MAX` | `20` | Connections per API instance. `5` on Vercel. |
| `GOOGLE_CLIENT_ID` | empty, so Google sign-in is hidden | The OAuth client ID from Google Cloud. |
| `ADMIN_EMAILS` | empty | The first admins, comma separated. They become admins on their first Google sign-in. |

### Inviting people

Sign in as an admin and open **Admin** in the sidebar. Enter the person's Google email, their
name, and a team and role, then choose **Invite**. Tell them to sign in with Google; nothing is
emailed. From the same page you can create teams, change roles, remove people from a team, and
deactivate or reactivate anyone. Removing or deactivating someone releases their open items,
and each release appears in that item's history. Every admin change is listed at the bottom of
the page and cannot be edited afterwards.

### Setting up Google sign-in

1. In Google Cloud Console, open **APIs & Services**, then **OAuth consent screen**. Choose
   **External**, fill in the app name and support email, and add yourself as a test user.
   While the app is in testing, only test users can sign in; publish it to open it up.
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

- **Live updates.** Server-sent events from the outbox, so a change shows up at once rather
  than on the next poll. Polling stays as the fallback.
- **Operations.** A screen to inspect and retry dead outbox jobs, a production build of the
  web app, and metrics on queue depth and conflict rate.
- **At larger scale.** Partition the history table by time, move search to a dedicated engine
  only if relevance becomes the complaint, and replace the dev login with real sign-on.
