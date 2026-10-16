# Engineering decisions

Append-only. Each entry: the situation, the decision, the trade-off accepted. Newest last.

## D-01 Current state in the row, history in an append-only table, written in one transaction

**Situation.** The brief wants every change to be visible later: who changed the owner, when,
from what. Two ways to get that: full event sourcing (the event log is the truth, tables are
projections rebuilt from it) or a plain row plus an audit table.

**Decision.** `work_items` holds the current state and is what every read uses. Every mutation
also appends a `work_item_events` row, in the same transaction, through one function
(`apply_change`). Database triggers refuse UPDATE and DELETE on the events table.

**Trade-off.** No rebuild-from-history and no replay. A code path that writes the row without
the event would be a silent hole, so there is exactly one write path and a test asserts that
the event count moves with every mutation. Chosen over event sourcing because the projection
and divergence-repair machinery is a week of work, not a day, and the audit guarantee is what
the brief actually asks for.

## D-02 Optimistic versioning on every write, and the claim as one atomic statement

**Situation.** Two users claim the same item; one user edits a copy that another has already
changed.

**Decision.** `work_items.version` increments on every write. Every mutating request carries
the version the client last saw; the UPDATE's WHERE clause includes `version = :expected`.
Zero rows updated means the client was stale: the API re-reads the row and returns 409 with
the current item and the last events, so the client can show what changed. Claiming is a single
`UPDATE ... WHERE assignee_id IS NULL AND version = :expected RETURNING *`, so there is no
read-then-write window.

**Trade-off.** Users will see conflicts. That is the point; the alternative is last write wins
and a silent overwrite. Pessimistic locks were rejected because a browser tab cannot hold a
database lock.

## D-03 Idempotency keys stored server-side

**Situation.** A user clicks twice, or a request times out and the client retries, and two
comments or two items appear.

**Decision.** POST and action routes accept an `Idempotency-Key` header. The key is stored
per user with a hash of the body and the response. A repeat with the same key and body returns
the stored response without running the handler. The same key with a different body is a 422.
The frontend generates the key when the mutation is first attempted and reuses it on retry.

**Trade-off.** One extra row per write and a 24 hour retention job. Rejected the alternative
of making every operation naturally idempotent, because "add a comment" is not.

## D-04 Transactional outbox and a polling worker, no broker

**Situation.** Notifications and other secondary work should not slow down or fail the primary
action, but must not be lost, and must not be sent twice.

**Decision.** `apply_change` inserts an `outbox` row in the same transaction as the item change.
A worker polls with `SELECT ... FOR UPDATE SKIP LOCKED`, runs the handler, marks the row done.
Failures increment `attempts` and back off exponentially; after eight attempts the row is
marked `dead` with the error stored. Handlers are idempotent: notifications have a unique
`(user_id, event_id)` and insert with `ON CONFLICT DO NOTHING`.

**Trade-off.** Up to two seconds of delay, and dead rows need a human. Rejected a queue
broker because Postgres already gives the two properties that matter, atomic enqueue with the
business write and safe concurrent consumers, and a broker would add a second system to run
and reason about.

## D-05 Authorization as a query predicate, 404 for outsiders

**Situation.** Roles differ per team. The brief says authorization must be enforced by the
server, not by hiding buttons.

**Decision.** Every item read and write joins `team_members` for the caller inside the query.
A user who is not on the item's team gets 404, not 403, so they cannot discover which ids
exist. Field-level rules (an assignee may change status but not priority) live in one function,
`may(actor, item, action)`, and the transition table is data, not code spread across routes.
The item response carries `allowed_actions` computed by the same function, so the UI draws
buttons from the server's answer rather than guessing.

**Trade-off.** The matrix is fixed in code; adding a role is a code change. A role editor was
out of scope for a day.

## D-06 Keyset pagination and Postgres full-text search

**Situation.** Tens of thousands of items and a growing history; the brief forbids loading
everything into the browser or into memory.

**Decision.** List endpoints page by `(updated_at, id)` cursor, never by offset. Filters map to
one composite index. Search is a generated `tsvector` column with a GIN index. The item
history pages by sequence number. Every capped list says `capped: true` so the UI can say
"showing the first 50" instead of implying that is all.

**Trade-off.** No jump-to-page, no fuzzy search, no relevance tuning. Rejected a search engine
because it would be the first thing to drift from the database and the brief's scale does not
need it.

## D-07 Seed data generated inside Postgres, loaded on boot into an empty database

**Situation.** The demo needs tens of thousands of items with a real history so that lists,
search, pagination and the inbox can be judged at scale, and the tests need known users and
teams to sign in with.

**Decision.** One SQL file, `api/seed.sql`, builds 40 users, 8 teams, 20,000 items and about
150,000 events with `generate_series` and `setseed`, so the shape is the same on every machine.
The API runs it on boot inside one transaction, under a table lock, only when `users` is empty.
It is not a migration, so it is never recorded as applied and never blocks schema changes.
Each item's `version` equals its event count and its last status event matches its status, so
the invariants that `apply_change()` will enforce already hold on day one.

**Trade-off.** Reseeding means dropping the Postgres volume. Generating the rows in Python was
rejected because pushing 150,000 inserts over the wire takes minutes; inside Postgres it takes
seconds and ships no extra code.

## D-08 A signed, stateless session cookie for dev login

**Situation.** Sign-on is out of scope, but every authorization check needs a trusted actor.

**Decision.** `POST /auth/dev-login` accepts a seeded email and sets an HttpOnly, SameSite=Lax
cookie holding the user id signed with `SESSION_SECRET` (itsdangerous, seven day expiry). One
dependency, `current_user`, verifies the signature and loads the user; every later route uses
it. Unknown emails get 404, a missing or forged cookie gets 401. The API refuses to start
without `SESSION_SECRET` rather than fall back to a guessable default.

**Trade-off.** No revocation and no password. The cookie is as trustworthy as the secret, which
is fine for a demo where anyone may be anyone; the effort goes into what a user may do, not
into proving who they are.
