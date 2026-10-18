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

## D-16 Google ID tokens verified by the API, invitations decide who gets in

**Situation.** Real users need real sign-in. The options were a hosted identity service (Clerk,
Neon Auth), passwords stored by the app, or signing in with Google directly.

**Decision.** The page shows Google's own button. Google returns a signed ID token, and the API
verifies it against Google's published keys, checking issuer, audience, expiry and that Google
has verified the email. The email must belong to an invited, active user; emails in
`ADMIN_EMAILS` become admins on first sign-in so a new deployment has someone to invite
everyone else. The first sign-in records Google's stable account id against the user, and a
later token for the same email from a different account is refused. A successful sign-in
issues the same database session as before, so revocation and rate limits apply unchanged.

**Trade-off.** Google only: no passwords and no other providers. Accepted because it stores no
credentials and needs no email delivery for resets or verification. Rejected Clerk because a
production instance needs a domain we own and adds a second user store to keep in step.
Rejected app-stored passwords because resets and verification need email delivery we do not
have.

## D-17 Admins manage people and teams, and leaving releases work through the normal write path

**Situation.** With real sign-in, someone has to decide who gets in and on which teams. People
also leave teams and the company while they still own open items.

**Decision.** A global admin invites people by email with their teams and roles, creates
teams, changes roles, removes members, and deactivates or reactivates people. Non-admins get
404 on every admin route. Admins cannot demote or deactivate themselves, so the acting admin
always remains. Deactivation revokes every session at once. Removing someone from a team, or
deactivating them, releases each open item they own through `apply_change()`, so the release
is a normal history event with a note saying why. Every admin action is written to an
append-only `admin_events` table, guarded by the same trigger as item history.

**Trade-off.** Team leads cannot manage their own team, which puts every change through an
admin. Accepted for a first version because it keeps one place to look when access is wrong.
Releasing work on removal surprises nobody who reads the history, but it means someone has to
pick the items up again; the alternative, leaving them owned by someone who can no longer see
them, hides them instead.

## D-18 On Vercel the outbox is drained by the API itself, after each response

**Situation.** D-04 assumed a worker process polling every two seconds. Vercel runs code only
while a request is in flight, and on the free plan scheduled jobs run at most once a day.

**Decision.** The outbox design is unchanged: the event and its outbox row are still written in
the same transaction as the change. What changes is who processes it. With
`DRAIN_OUTBOX_IN_REQUESTS=true`, each API instance runs one batch after it has sent a response,
at most once a second, using the same `process_batch` as the worker: `FOR UPDATE SKIP LOCKED`,
savepoints, backoff and dead letters all apply, and concurrent instances never take the same
row. A daily scheduled call to `/api/internal/drain`, which must present `CRON_SECRET`, sweeps
anything left while nobody used the app. Startup (pool, migrations) is idempotent and runs on
the first request too, so it does not depend on the platform's startup hook. Locally the worker
process still runs.

**Trade-off.** Delivery and retries now ride on traffic. With users active, a notification
lands right after the change; with none, a failed job can wait for the daily sweep. Accepted
because a worker elsewhere would be a second platform to run and pay for, and Vercel Queues
would replace a design whose guarantees are already tested. A paid plan's per-minute cron
would close the gap without code changes.

## D-19 One deployment, run as a public demo, with the real sign-in still on show

**Situation.** Reviewers need to use the app without being invited, but they should still see the
real Google sign-in, and one visitor must not be able to break the demo for everyone else.

**Decision.** The live site runs with `DEMO_MODE=true`. Any verified Google account gets in and
lands as a member of Support and a viewer of Risk; the one-click demo people on the sign-in page
cover the other roles, including the admin. The seeded demo accounts cannot be changed, demoted,
deactivated or removed from teams, though visitors can invite and manage people of their own.
Every page carries a banner saying the data is shared and made up. A nightly scheduled call,
behind `CRON_SECRET` and only in demo mode, empties every table and loads the seed again. With
`DEMO_MODE` off, the same code is invite-only and its history is never deleted.

**Trade-off.** One site cannot also hold real data: in demo mode anyone can act as the admin.
Accepted because the assignment needs a site to try, and a second, empty production deployment
would show reviewers nothing. Running real work means a second deployment with demo mode off,
which the README describes.

## D-20 Live updates are server-sent events fed by a short indexed check, with polling behind them

**Situation.** Polling every 15 seconds meant someone could act on a change made seconds earlier
and only learn about it from a 409. The build plan listed live push as the first extra.

**Decision.** Each browser tab opens one server-sent events stream at `/stream`, naming the item
it has open. Every 1.5 seconds the server runs one query that re-checks the session and the
item access and reads the item's version and the user's newest notification id, and sends an
event when either moved. The browser then refetches through the same queries as before, so the
stream carries no data of its own and cannot drift from the API. Signing out, deactivation or
leaving the team ends the stream. It closes itself at 240 seconds, under Vercel's 300 second
limit, and the browser reconnects; while it is up, pages poll once a minute instead of every 15
seconds.

**Trade-off.** A check per open tab every 1.5 seconds, instead of a push from the database.
Rejected Postgres LISTEN because Neon's transaction pooler does not carry it, and a direct
connection held open for every tab would grow connections with the number of readers rather
than with the work being done. Rejected WebSockets because nothing flows from the browser to the
server on this channel.

## D-21 Failed jobs wait for a person, and housekeeping runs wherever the outbox is drained

**Situation.** D-11 parks a job as dead after eight failures, and until now only the database
could show or retry one. Separately, the cleanup of idempotency keys and rate-limit counters ran
only in the worker process, so on Vercel, where there is no worker, it never ran at all.

**Decision.** Admins see background jobs by status on the Admin page, with the error, the item
the job was about, and a Retry button. A retry is one conditional update from dead to pending
with a fresh set of attempts, so two admins retrying together cannot both succeed; the second
gets 409. Each retry is in the admin log with the error it cleared. Retrying is safe because
every handler is idempotent. All cleanup now lives in one `housekeeping` function: expired
idempotency keys, old rate-limit windows, and finished jobs older than seven days. The worker
calls it every minute and the daily scheduled sweep calls it on Vercel. Failed jobs are never
purged.

**Trade-off.** A person has to notice and act; there is no alert yet and no automatic retry
after the eighth failure. Accepted because a job that has failed eight times with backoff usually
needs something fixed first, and retrying it blindly would only fail again.

## D-22 The dashboard is a few indexed queries, scoped to the caller's teams

**Situation.** The home page should say at a glance what needs me and how my teams' work is
moving, without a reporting pipeline and without scanning 20,000 items and 150,000 events.

**Decision.** `GET /dashboard` runs five queries, each shaped for an index: counts of what needs
me, open work by team and status, items created per day, items resolved per day (status events
on an index by time), and the latest events on my teams. Migration 006 adds the indexes they
need, and a test runs EXPLAIN on each to make sure none scans a whole table. Every item also
gets a sequential number shown as `OPS-1234`, which people can say aloud and search for.

**Trade-off.** Counts are computed on every load instead of maintained as totals, which keeps
them always correct and adds no write cost. If the tables grew by orders of magnitude, the
counts would move to totals kept by `apply_change()` in the same transaction as each change.

## D-23 Item keys count per team, from a counter on the team row

**Situation.** One company-wide `OPS-` sequence made every team's items look alike, and an
item's key said nothing about whose it was. People expect `RSK-1204` to be Risk's, and each
team's numbers to run on without gaps.

**Decision.** Every team has a short code and a `next_number`. `apply_change()` takes the next
number for the item's team with `update teams set next_number = next_number + 1 ... returning`,
in the same transaction that inserts the item, so a rolled-back create gives its number back and
keys stay gapless. Items are unique on `(team_id, number)`. Migration 007 renumbered existing
items within their team in creation order. Search reads `RSK-1204` as that team's item 1204 and a
bare `1204` as that number on any team. A new team's code is the first three letters of its name,
numbered when taken.

**Trade-off.** The row lock on the team means creates on one team take turns. A Postgres sequence
per team would not block, but it skips numbers on every rollback and needs DDL for every new
team. People create items far slower than that lock is held, so gapless keys won.

## D-24 The items list is grouped by status, each group with its own cursor

**Situation.** One flat list of 20,000 items sorted by last change mixed new work with closed work,
so nobody could see where things stood. Grouping a single paged list in the browser would not fix
it: groups would grow and shift as pages arrived, and their sizes would be wrong until the end.

**Decision.** The page asks `GET /items/counts` for how many items match the filters in each
status, then shows one group per status that has any. Each open group fetches its own first ten
rows with `status=` added, on the same composite index the list already uses, and pages further on
its own cursor. Finished work starts folded and is fetched only when opened. A search opens every
group, since the item you typed may well be closed. The counts and the list share one function
for their WHERE clause, so a count can never include an item the list would hide.

**Trade-off.** The first load makes up to six requests instead of one. They are small, run in
parallel and each hits an index, and in return every number on the screen is exact.

## D-25 Mark all as read stops at the newest notification the reader saw

**Situation.** With many unread notifications, clicking each one is the only way to clear the
count. A plain "mark everything read" would also clear a notification that arrived after the page
loaded, which the reader never saw.

**Decision.** `POST /notifications/read` takes `up_to`, the id of the newest notification on the
reader's screen, and marks that one and every older unread one for that user. Ids only grow, so
anything that arrives later stays unread and keeps the count honest.

**Trade-off.** The client must send the id it saw instead of calling a bare endpoint. That is one
field, and it is the only way the server can know what the reader actually looked at. Ids are
handed out when a row is inserted, not when it commits, so a notification inserted just before the
newest one but committed after the page loaded can still be marked. That window is one worker
transaction long.

## D-26 On the demo, what a visitor brings is private, even from the admin

**Situation.** The public demo lets anyone sign in as the shared admin with one click. Admins
skipped the membership check everywhere, so the Admin page listed the real email address of every
visitor who had signed in with Google. The password-free demo sign-in also accepted any email, so
anyone who knew a visitor's address could sign in as them. Own workspaces for visitors, coming
next, would have been readable by anyone in the same way.

**Decision.** Users and teams get a `private` flag. On the demo, a Google visitor's account is
private, and later so are their workspace teams and the people they invite.
- Admins skip the membership check only for teams that are not private. This is one fragment in the
  item queries, plus the team lists behind the dashboard, inbox and live stream.
- Every admin endpoint leaves private people and teams out, or answers 404 for them, the same as
  for something that does not exist.
- The password-free sign-in refuses private accounts, so it only reaches the shared demo people.

Off the demo nothing is ever marked private, so admins see everything as before.

**Trade-off.** One more condition in each admin path, and a test that walks all of them. An
admin of a real deployment cannot see a private team either, but nothing outside demo mode creates
one. A separate admin role scoped to "public" data was considered. It would touch the same queries
and add a concept that only the demo needs.

## D-27 A visitor's own workspace is the teams they lead, not a new tenant

**Situation.** The demo let visitors explore shared, seeded data. Nobody could try the real path:
start empty, make a team, bring people in, do the work. Real organisations would mean an
organisation id on every table and a new check in every query, where one miss leaks one company's
data to another.

**Decision.** On the demo, signing in with Google opens an empty workspace instead of joining the
seeded teams. The visitor creates teams (`POST /teams`), each private (D-26) with them as its lead.
A lead adds people by email (`PUT /teams/{id}/members`), which on the demo creates a private
account they sign in to with Google. Everything a person sees was already filtered by team
membership, so the workspace needed no new isolation layer. The shared demo accounts cannot create
teams or be added to one, since anything they touch is visible to every visitor. The same lead
action works off the demo for people who already have an account, which removes a limitation:
leads can now manage their own team.

**Trade-off.** A workspace has no name of its own, no admin and no billing, and the nightly reset
clears it. That is the right size for a demo that shows the product. A real multi-company service
would add organisations as their own layer.

## D-28 No email yet; a lead copies an invite message instead

**Situation.** Adding someone to a team gave them an account, but nothing told them. Real invite
emails need a sending domain verified with a provider such as Resend (DNS records the owner
adds). This project has no domain, and a `vercel.app` address cannot be verified. Without one,
a provider only delivers to the account owner's own inbox. The public demo adds a second
problem: anyone can sign in and type any address, so sending mail safely needs limits per sender
and per recipient first.

**Decision.** Send nothing. After a lead adds someone, the app shows a ready message: the team,
the role, the address to sign in with, and the sign-in link. A Copy invite button puts it on the
clipboard for the lead to paste into chat or their own email.

**Trade-off.** The lead does one more step, and nobody is told automatically. When a domain
exists, the email becomes an `invite_email` outbox handler. It would be queued in the same
transaction as the membership, retried, and sent with a unique key per invite so a retry never
sends twice. The copy button stays as a fallback.

## D-29 One rule decides which teams a person sees, and every screen reads it

**Situation.** The admin's dashboard and recent activity covered every team, as the permission rules
allow, but the sidebar listed only the two teams she belonged to. The New item dialog offered only
Payments, and the items filter and the team page also knew only her memberships. Each screen had
worked out "which teams" on its own from the user's memberships, so they disagreed with the server
and with each other. Any future admin, and any team added later, would have hit the same gap.

**Decision.** `visible_teams()` in `app/items.py` is the one rule. It returns every team the caller
can see (their own, plus every public team for an admin), their role on each ("admin" when they
see it only as an admin), and whether they may create items there. Creating an item and reading a
team roster use it, and `/me` returns it, so every screen builds its team lists from it: the
sidebar, the items filter, the Members button, the New item dialog and button, the team page and
the dashboard's team count. Labels that said "on teams you lead" now say "where you approve",
which is true for leads and admins alike.

**Trade-off.** `/me` returns a second list beside the memberships. In return, a picker can never
offer a team the server would refuse, or hide one it would accept. A test builds new accounts of
every kind and checks that what `/me` offers is exactly what creating an item does. A browser test
checks that every list on screen matches `/me` for each role.

## D-30 The connection pool drops dead connections at once, instead of handing them out

**Situation.** On the live site, clicking a demo person after a quiet spell sometimes answered
"Request failed 500", and the next click worked. The logs showed `psycopg.OperationalError: SSL
connection has been closed unexpectedly`. Neon closes idle connections and suspends an idle
database, which drops every connection at once, but the pool still held them and gave the next
request a dead one. Any first request after a pause could fail, not only signing in.

**Decision.** `db.LivePool` overrides the pool's public `getconn`, the one route every request
takes to the database. It tests each connection with an empty query as it hands it out. A dead one
is returned so the pool discards it, and the next is taken at once, until a live or brand-new
connection comes. Idle connections are also retired after a minute (`max_idle=60`), before the
database closes them itself.

**Trade-off.** One tiny round trip per request, a few milliseconds next to Neon in the same
region. The library's own `check=` option was tried first. It does test connections, but it waits
longer after each dead one (1 s, 2 s, 4 s and on), so a pool full of dead connections took many
seconds, or timed out entirely in the test. A test fills the pool, has the database end every
connection, and requires the next requests to succeed within three seconds.

## D-31 Each team stores its colour, picked when it is created

**Situation.** A team's colour was worked out on each screen from a hash of its name, with a
hard-coded list giving the eight demo teams one colour each. That was a patch for the demo's teams
only: any team created later could share a colour with another, and two screens could only agree
because they ran the same hash.

**Decision.** `teams.color` holds one of eight palette slots (migration 010, `not null`, checked to
lie between 0 and 7). A new team, from the Admin page or a visitor's workspace, takes the slot least
used among the teams its creator can see (`next_team_colour`, built on `visible_teams` from D-29),
lowest first on a tie. The colour travels with the team in every response that names one, and
every screen draws it from there. Existing teams and the demo seed are spread across the slots in
key order, so a reseed never changes a colour.

**Trade-off.** One more column, returned with every team. "Least used among what you can see" means
two people can see the same team in the same colour while their other teams differ, which is fine.
The column has no default, on purpose: a new way of creating teams that forgets to choose a
colour fails at once rather than painting every team the same.

## D-32 Existing team colours are spread within what each person sees

**Situation.** Migration 010 gave existing teams their colours in one key order over every team.
On the live demo a visitor's private workspace team sorted between the demo teams, took a slot, and
left two public teams sharing a colour. The local check missed it: the fresh database the reset
test uses has no private teams.

**Decision.** Migration 011 spreads the colours again by scope, matching the rule for new teams in
D-31. Public teams are spread on their own in key order, and each private workspace is spread
within itself. A test builds the live site's shape on a throwaway database, the eight demo teams
plus a private team keyed between them, and requires eight different public colours.

**Trade-off.** None worth noting. A migration that has already run is never edited, so the fix is
a second, small one.

## D-33 A claim starts new work, and otherwise only takes ownership

**Situation.** A claim always set the status to in progress, from any open status. The transition
table did not know about it, so a claim was a second way to move status. A new item skipped triage
without saying so, and an item waiting for approval, or already approved, that had lost its owner
went back to in progress when someone claimed it, losing its place in the approval queue. An
external review found it. The README said only the table's moves were allowed.

**Decision.** `CLAIM_STARTS` sits next to the transition table: claiming new or triaged work moves
it to in progress, because claiming means "I am doing this now". At any other open status a claim
only takes ownership, and the status and the approval request stay. Each item reports this as
`claim_to`, and the page reads it to show the claim before the server answers and to choose its
main button. An unowned item waiting for approval offers Approve first, and Claim beside it.

**Trade-off.** A claim on a new item still skips triage, on purpose: the person claiming it has
looked at it. A test claims at every open status and requires that nothing leaves approval.

