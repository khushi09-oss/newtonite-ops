# Newtonite Ops

An internal work tracker for operational requests: customer issues, incidents, payments to
investigate, compliance requests, approvals. Built for the Newtonite Software Engineering
Challenge ("Operations Under Pressure").

The system is small on purpose. The effort went into the behaviours that break under real
use: two people claiming the same item, edits against a stale copy, a double-submitted form,
workflow rules that must hold server-side, and notifications that must not be lost or sent
twice.

## Run it

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---|---|
| API | http://localhost:8000 (OpenAPI at /docs) |
| Web | http://localhost:5173 (first start installs packages, about a minute) |
| Postgres | localhost:5432 |

A worker container polls the outbox and turns item events into notifications. Scale it with
`docker compose up -d --scale worker=2`; the rows are locked per worker, never shared.

The API runs its migrations on boot and seeds a demo dataset (users, teams, 20,000 work
items, their history) when the database is empty. Sign in with any seeded user from the
login screen; there is no password (see Assumptions). From the shell:

```bash
curl -c jar -X POST localhost:8000/auth/dev-login -H 'content-type: application/json' -d '{"email":"ava.okafor@newtonite.dev"}'
curl -b jar localhost:8000/me
```

Seeded emails are `first.last@newtonite.dev`; the first user is the global admin.

## Try the conflict

1. Open http://localhost:5173 in two browsers, or one normal and one private window.
2. Sign in as the Risk member in one and the Risk lead in the other, using the buttons on
   the sign-in screen.
3. Open the same unclaimed Risk item in both. Raise its priority as the lead.
4. Click Triage as the member without reloading. The change is rolled back and a strip says who
   changed what. Retry applies it on top of the new version.

## Run the tests

```bash
docker compose exec api python -m pytest -q
```

The image carries its own copy of `api/`, so after editing code or tests run
`docker compose up --build -d` before testing again.

Tests run against real Postgres, not SQLite, because the claim race and the outbox depend on
Postgres row locking.

## Layout

| Path | What it is |
|---|---|
| `api/app/` | FastAPI application: routes, authorization, the single `apply_change()` write path |
| `api/migrations/` | Plain SQL, applied in order on boot |
| `api/seed.sql` | The demo dataset, generated inside Postgres on first boot |
| `api/worker.py` | The outbox worker: notifications, retries, dead-lettering |
| `api/app/idempotency.py` | `Idempotency-Key` handling: stored responses, replay, mismatch refusal |
| `api/app/notifications.py` | Watchers, the notification handler, the notification routes |
| `api/tests/` | Tests named after the risk they guard |
| `web/` | React + Vite + TanStack Query frontend: inbox, items list, item page, notifications |
| `ENGINEERING_DECISIONS.md` | The six decisions and their trade-offs |
| `LIMITATIONS.md` | What is known to be missing or weak |

## Assumptions

- Single sign-on is out of scope. Users are seeded and a dev login issues a signed session
  cookie. All the effort is in authorization, which is enforced in the database query, not in
  the UI.
- Notifications are stored in a table and shown in the app. Email or chat delivery would be
  one more outbox handler.
- One company, many teams. A user holds a role per team (viewer, member, lead) and may be a
  global admin.
