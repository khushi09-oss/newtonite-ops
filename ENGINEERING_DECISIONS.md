# Engineering decisions

Append-only. Each entry: the situation, the decision, the trade-off accepted. Newest last.

If you read five, read these. The rest refine them as the build went on.

- **D-01** History lives in an append-only table written in the same transaction as the change.
- **D-02** Every write checks a version, and a claim is one atomic statement.
- **D-03** Repeated requests are answered from stored responses.
- **D-04** Secondary work goes through a transactional outbox and a polling worker, not a broker.
- **D-05** Authorization is part of every query, and outsiders get 404.

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

## D-09 One write function that reads the row first, then updates under the version check

**Situation.** D-01 and D-02 need every write to produce an honest event (what the field was,
what it became) and every stale write to come back with the current row. Doing that with a
single UPDATE means either a second query on the failure path or a RETURNING of old values,
which Postgres 16 does not offer.

**Decision.** `apply_change()` selects the row, compares `version` to the client's, runs the
UPDATE with `version = :expected` (and any extra guard, such as `assignee_id is null` for a
claim) in the WHERE clause, then appends the event with `before` and `after` taken from the two
rows. Zero rows from the UPDATE raises a conflict that already carries the row it read. The same
function inserts when there is no id, so creation leaves a `created` event by the same path. The
event's `seq` is the new `version`, which keeps "version equals event count" true by construction.

**Trade-off.** One extra SELECT per write. It is not a race: if another writer slips in between
the SELECT and the UPDATE, the version in the WHERE clause no longer matches and the UPDATE
touches nothing, so the SELECT only ever decides which error to return, never whether the write
is safe. Rejected `SELECT ... FOR UPDATE` because the version check already gives the guarantee
and a lock would hold other writers for the length of the request.

## D-10 A stale version is answered before permission, and comments bump the version too

**Situation.** Twenty people click claim on the same item. One wins. The other nineteen hold a
row that no longer exists. Some of their requests reach the server before the winner commits,
some after. The brief wants the loser told who won, every time.

**Decision.** Every mutating route checks the client's `version` against the row before it
checks whether the action is allowed. A mismatch is 409 with the current row and recent events,
whatever the caller was trying to do. Only when the version matches does the permission check
run, so a 403 or 422 always describes a row the caller has actually seen. Comments go through
the same write path and bump the version, with the row locked for the duration since the client
sends no version for them; that is why comments show up in the history and in the stale-edit
banner like any other change.

**Trade-off.** Someone editing a title will get a 409 because a colleague commented. The
banner shows exactly that and offers retry, which is a smaller cost than history that skips
comments or a version number that lies about what changed.

## D-11 The worker declares the kinds it handles, and each row runs in a savepoint

**Situation.** The outbox will carry more than one kind of job over time, the tests share a
database with a live worker, and a handler that fails halfway must not leave half its writes
behind while the row is retried.

**Decision.** `claim_batch` selects only rows whose kind is in the worker's handler table, with
`FOR UPDATE SKIP LOCKED`, so a second worker takes different rows and a row of an unknown kind is
left for a worker that knows it. Every handler runs inside its own savepoint: on failure the
savepoint rolls back, the outer transaction records the attempt, the backoff and the error, and
marks the row dead on the eighth failure. A worker killed mid-batch loses its uncommitted
transaction, the locks vanish, and the next poll picks the rows up again; that is safe because
handlers are idempotent. Once a minute the worker logs any pending rows of a kind nobody handles,
so a misspelt kind cannot wait in silence.

**Trade-off.** A dead row needs a person to look at `last_error` and reset it. Rejected
retrying forever because a poison row would then hide every row behind it in the log.

## D-12 Idempotency stores only successes, under a lock per user and key

**Situation.** Two identical requests can arrive in the same millisecond, and a request can fail
for a reason the client will fix before retrying with the same key.

**Decision.** `idempotent()` takes a transaction-scoped advisory lock on the user and key, then
looks for a stored response. A hit with the same body hash is replayed; a hit with a different
hash is 422. Otherwise the handler runs, and the response is stored in the same transaction as
the write it describes. A handler that raises stores nothing, so the retry runs for real.
`POST /items` requires the header; transitions and comments honour it when sent.

**Trade-off.** One stored row per keyed write, purged after 24 hours by the worker. Keys are
scoped to the user, so two users cannot collide and one user cannot replay another's write.

## D-13 The inbox is four indexed queries with team ids passed in, not one query over everything

**Situation.** "What needs my attention" is the page people open most, against tens of thousands
of items, and it must stay fast as the history grows.

**Decision.** `GET /inbox` runs four small queries, one per bucket, each shaped to land on one
index: assigned to me (partial index on assignee for open items), awaiting my approval and
unassigned urgent (the team, status, priority index), and stale (the partial index on team and
last activity for open items). The caller's team ids are fetched first and passed as arrays, so
each query is a plain index condition rather than a join the planner has to reason about. The
open-state predicate is written exactly as the partial indexes define it. Each bucket is capped
at 50 and says so.

**Trade-off.** Five round trips instead of one, each under a millisecond on this data. No
paging inside a bucket; the list page with filters is where to go for more than 50.

## D-14 The UI is optimistic, and a 409 becomes a sentence instead of an error

**Situation.** Most clicks succeed, so waiting for the server makes the UI feel slow. But when
a click loses (someone else claimed it, changed the priority, commented), the user must see
what happened and decide, not be told "something went wrong".

**Decision.** Every item mutation runs through one TanStack Query mutation. `onMutate` applies
the expected result to the cached item and keeps the previous copy. Success replaces the cache
with the server's response. Any error restores the previous copy. A 409 shows a strip above the
item built from the server's newest event: "Changed by (name) just now: priority P1 to P0. Your
change was not saved." with Reload and Retry on the new version. Buttons come from
`allowed_actions`, so the UI never offers what the server would refuse. The browser talks to
`/api` on its own origin and Vite forwards it, so the session cookie needs no CORS rules. The
item, inbox and notification count refetch every 15 seconds.

**Trade-off.** For about 100 milliseconds a user can see a result that the server then rolls
back. That is the honest cost of feeling fast; the strip explains the rollback. Polling is
simple and survives restarts, at the price of up to 15 seconds of staleness between writes.

## D-15 Sessions live in the database, demo conveniences are off by default, and limits are shared

**Situation.** The app is going to real users on a public URL, on a platform that runs many
short-lived instances. The cookie held a signed user id, so signing out only asked the browser
to forget it (which an HttpOnly cookie cannot do from JavaScript), and nothing could end a
session early. Dev sign-in and the demo seed were on unless switched off.

**Decision.** The cookie now carries a signed id for a row in `sessions`. `current_user` accepts
it only while the session is unrevoked, unexpired and its user is not deactivated, so sign-out
and deactivation take effect on the next request. Dev sign-in and the seed are off unless a
setting turns them on, and `.env.example` turns them on for local work. Writes from a page
whose `Origin` is not ours are refused before any route runs. Writes are rate limited per user
in `current_user`, so no route can forget it, with the counters in Postgres so every instance
shares them. Migrations take a transaction-scoped advisory lock, so instances that start
together cannot race, and the lock works through a connection pooler.

**Trade-off.** One extra indexed lookup and one counter upsert per request. Accepted because a
cookie that cannot be revoked and a limit each instance counts separately are both false
security. Rejected in-memory rate limits because on many instances each would allow the full
limit.
