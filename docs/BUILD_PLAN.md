# Build plan

The plan this repository is built to, in order. Tick items as they land. The reasoning behind
each choice is in `ENGINEERING_DECISIONS.md`; this file is the what and the order.

## The five behaviours this system must demonstrate

1. Two users claim the same item: exactly one wins, the other gets 409 with the winner's name.
2. Stale edits: every mutation carries `version`; a mismatch returns 409 with the current item.
3. Repeated requests: an `Idempotency-Key` header returns the stored first response; same key
   with a different body is rejected.
4. Workflow rules: a transition table enforced server-side; approval needs a team lead who is
   not the requester.
5. Reliable async: notifications go through an outbox in the same transaction, a worker with
   retries and a dead-letter state, and a handler that cannot double-send.

Plus authorization as a query predicate, with 404 for non-members.

## Permission matrix

Roles are per team. Admin is global.

| Action | viewer | member | lead | admin |
|---|---|---|---|---|
| read, comment, watch | yes | yes | yes | yes |
| create, edit own report, claim unassigned | no | yes | yes | yes |
| reassign, change priority, release another's claim | no | assignee only | yes | yes |
| request approval | no | assignee | yes | yes |
| approve or reject | no | no | yes, not the requester | yes, not the requester |
| close | no | no | yes | yes |

Non-members of the item's team get 404 on every route.

## Transition table

```
new → triaged                  member
triaged → in_progress          assignee
in_progress → awaiting_approval assignee or lead
awaiting_approval → approved   lead or admin, not the requester
awaiting_approval → triaged    lead or admin ("rejected", note required)
approved → resolved            assignee or lead
resolved → closed              lead or admin
closed → triaged               lead or admin ("reopen")
any open state → in_progress   on claim
```

Anything not listed is 422 with `allowed` in the body. The item response carries
`allowed_actions` from the same function so the UI draws its buttons from the server.

## The claim, one statement

```sql
update work_items
   set assignee_id = :me, status = 'in_progress', version = version + 1,
       updated_at = now(), last_activity_at = now()
 where id = :id and assignee_id is null and version = :expected
returning *;
```

Zero rows: re-read and return 409 with the current row. Same shape for every edit
(`where id = :id and version = :expected`).

## API

Keyset pagination everywhere. Never `offset`.

```
POST /auth/dev-login {email}                      session cookie, seeded users only
GET  /me                                          user, teams, roles

GET  /items?team=&status=&priority=&assignee=&q=&cursor=&limit=50
POST /items                                       Idempotency-Key required
GET  /items/{id}                                  item + allowed_actions + last 20 events
PATCH /items/{id}                                 {version, title?, description?, priority?, due_at?}
POST /items/{id}/claim                            {version}
POST /items/{id}/release                          {version}
POST /items/{id}/assign                           {version, assignee_id}
POST /items/{id}/transition                       {version, to, note?}   Idempotency-Key honoured
POST /items/{id}/comments                         {body}                 Idempotency-Key honoured
GET  /items/{id}/events?before_seq=&limit=50
POST /items/{id}/watch | DELETE /items/{id}/watch

GET  /inbox                                       four buckets, each capped at 50
GET  /notifications?unread=1
POST /notifications/{id}/read
GET  /health
```

Inbox buckets: assigned to me and open; awaiting my approval (lead of the team, not the
requester); unassigned p0 or p1 in my teams; stale (open, no activity for 3 days, in my teams).
Four indexed queries, not one scan.

## Outbox and worker

`apply_change()` inserts the row change, the event, and one outbox row in one transaction.
Worker every 2 s: `select ... from outbox where status='pending' and next_attempt_at <= now()
order by id limit 20 for update skip locked`. Handler `item_event` inserts one notification per
watcher, assignee, reporter with `on conflict do nothing`. Failure: `attempts + 1`,
`next_attempt_at = now() + 2^attempts seconds`, `dead` after 8 with the error stored.

## Frontend

React + Vite + TanStack Query. Three routes:

- `/inbox`: the four buckets.
- `/items`: filters held in the URL, infinite scroll on the cursor, search box.
- `/items/:id`: timeline, action buttons drawn from `allowed_actions`, comment box, refetch
  every 15 s. Every mutation sends `version` and is optimistic; on 409 roll back and show
  "Changed by <name> <time> ago: <field> <before> → <after>" with reload and retry.
  Idempotency key generated in `onMutate` and reused on retry.

## Tests, named after the risk

- `test_claim_race_exactly_one_winner` (20 threads, one 200, nineteen 409, one event)
- `test_stale_version_rejected_with_current_state`
- `test_idempotency_same_key_same_response`, `test_idempotency_different_body_rejected`
- `test_transition_table_rejects_unlisted_and_tells_allowed`
- `test_approver_cannot_be_requester`
- `test_nonmember_gets_404_not_403`
- `test_viewer_cannot_claim`
- `test_outbox_handler_twice_one_notification`
- `test_outbox_dead_after_max_attempts`
- `test_two_workers_skip_locked_no_double_processing`
- `test_every_mutation_writes_exactly_one_event`
- `test_events_table_rejects_update_and_delete`
- `test_list_uses_index` (EXPLAIN shows no Seq Scan on work_items)

## Order of work

| Step | Deliverable | Done when |
|---|---|---|
| 0 | [x] Scaffold: compose, migration 001, health, docs skeleton | stack boots, triggers verified |
| 1 | [x] Seed script (SQL generate_series: 40 users, 8 teams, 20k items, 150k events), dev login, `/me` | `curl /me` with a seeded user works |
| 2 | [x] `apply_change()`, `require_access`, transition table, `/items` list + get | every route writes through `apply_change`; EXPLAIN shows index scans |
| 3 | [ ] claim, release, assign, patch, transition, comments, events page, all version-checked | race test passes |
| 4 | [ ] Idempotency dependency, outbox insert, worker, notifications, `/inbox` | worker survives a kill mid-batch and a second worker alongside |
| 5 | [ ] React: inbox, list, detail, optimistic mutations, 409 banner, polling | two browser windows show the conflict |
| 6 | [ ] Remaining tests | all green |
| 7 | [ ] Docs pass: decisions, README, limitations, `scripts/demo_race.py` | a stranger can run it from the README |
| buffer | [ ] SSE instead of polling, saved filters | only after everything above is green |

## Deliberately not built

Real SSO, websockets, attachments, role editor UI, email delivery, bulk edits, soft delete.
Each is one line in `LIMITATIONS.md` with the reason.
