-- Demo dataset. Runs once, on boot, only when there are no users. Deterministic: setseed below.
-- 40 users, 8 teams, 20,000 work items, roughly 150,000 history events.
-- Every item's version equals its event count, which is what apply_change() will keep true.

select setseed(0.42);

create temp table seed_users on commit drop as
select n,
       gen_random_uuid() as id,
       first_names[(n - 1) % 10 + 1] as first_name,
       last_names[(n - 1) / 10 + 1] as last_name
  from generate_series(1, 40) as n,
       (select array['Ava', 'Ben', 'Chloe', 'Dev', 'Esha', 'Finn', 'Gia', 'Hugo', 'Ira', 'Jun'] as first_names,
               array['Okafor', 'Mehta', 'Lindqvist', 'Moreau'] as last_names) as names;

insert into users (id, email, name, is_admin)
select id, lower(first_name || '.' || last_name || '@newtonite.dev'), first_name || ' ' || last_name, n = 1
  from seed_users;

create temp table seed_teams on commit drop as
select n, gen_random_uuid() as id, team_names[n] as name, team_keys[n] as key
  from generate_series(1, 8) as n,
       (select array['Payments', 'Compliance', 'Support', 'Incidents', 'Onboarding', 'Risk', 'Platform', 'Billing'] as team_names,
               array['PAY', 'CMP', 'SUP', 'INC', 'ONB', 'RSK', 'PLT', 'BIL'] as team_keys) as t;

-- Teams take turns, so each one ends with 20,000 / 8 items and its counter there.
insert into teams (id, name, key, next_number) select id, name, key, 2500 from seed_teams;

-- User n is lead of team ((n-1) % 8)+1 when n <= 8, otherwise a member of it, and a viewer of a second team.
-- Each team ends up with 1 lead, 4 members, 5 viewers.
insert into team_members (team_id, user_id, role)
select t.id, u.id, case when u.n <= 8 then 'lead' else 'member' end
  from seed_users u join seed_teams t on t.n = (u.n - 1) % 8 + 1
union all
select t.id, u.id, 'viewer'
  from seed_users u join seed_teams t on t.n = (u.n + 3) % 8 + 1;

-- Items. Teams take turns (i % 8) and titles advance once per round (i / 8), so every team gets
-- every title and a search for any title phrase finds rows on every team.
-- Items. Status is drawn with a skew toward closed; the status index decides how many transition events exist.
create temp table seed_items on commit drop as
with base as (
  select i,
         gen_random_uuid() as id,
         (i - 1) % 8 + 1 as team_n,
         (i - 1) % 5 + 1 as kind_n,
         random() as r_status,
         random() as r_priority,
         floor(random() * 5)::int as r_reporter,
         1 + floor(random() * 4)::int as r_assignee,
         floor(random() * 8)::int as comments,
         now() - (random() * interval '180 days') as created_at
    from generate_series(1, 20000) as i
), shaped as (
  select *,
         case when r_status < 0.10 then 0   -- new
              when r_status < 0.25 then 1   -- triaged
              when r_status < 0.45 then 2   -- in_progress
              when r_status < 0.55 then 3   -- awaiting_approval
              when r_status < 0.60 then 4   -- approved
              when r_status < 0.75 then 5   -- resolved
              else 6 end as status_n,     -- closed
         case when r_priority < 0.05 then 0 when r_priority < 0.25 then 1 when r_priority < 0.75 then 2 else 3 end as priority
    from base
)
select s.i, s.id, s.team_n, s.kind_n, t.id as team_id,
       (array['incident', 'customer_issue', 'payment', 'compliance', 'task'])[s.kind_n] as kind,
       (array['new', 'triaged', 'in_progress', 'awaiting_approval', 'approved', 'resolved', 'closed'])[s.status_n + 1] as status,
       s.status_n, s.priority, s.comments,
       1 + s.status_n + s.comments as event_count,
       reporter.id as reporter_id,
       case when s.status_n >= 2 then assignee.id end as assignee_id,
       lead.id as lead_id,
       s.created_at,
       -- Open work was touched recently (about one item in seven has gone quiet for three days);
       -- finished work stopped somewhere between its creation and now.
       case when s.status_n < 5
            then greatest(s.created_at, now() - (-ln(random())) * interval '1.5 days')
            else s.created_at + (now() - s.created_at) * random() end as updated_at,
       -- Open work is mostly due in the coming weeks, a few days overdue at worst for some.
       case when random() < 0.4 then
            case when s.status_n < 5 then now() + (random() * 30 - 4) * interval '1 day'
                 else s.created_at + random() * interval '30 days' end end as due_at
  from shaped s
  join seed_teams t on t.n = s.team_n
  join seed_users reporter on reporter.n = s.team_n + 8 * s.r_reporter
  join seed_users assignee on assignee.n = s.team_n + 8 * s.r_assignee
  join seed_users lead on lead.n = s.team_n;

insert into work_items (id, team_id, number, kind, title, description, status, priority, reporter_id, assignee_id,
                        approval_requested_by, due_at, version, created_at, updated_at, last_activity_at)
select id, team_id, (i - 1) / 8 + 1, kind,
       -- One template per kind and round, filled with a company, an order number or a week, so the
       -- titles read like real requests and still repeat enough for search to find each phrase.
       replace(replace(replace(replace(
         (array[
           ['Elevated 5xx on the checkout API in {r}', 'Webhook deliveries delayed for {c}',
            'Card authorisation latency above two seconds in {r}', 'Settlement file missing for week {w}',
            'Status page shows degraded for {c}', 'Spike in failed sign-ins from {r}'],
           ['Refund not received for order #{n}', '{c} cannot sign in after a password reset',
            'Invoice {n} shows the wrong total', '{c} asks to close their account',
            'Duplicate email receipts for order #{n}', 'Card declined at checkout for {c}'],
           ['Payout to {c} delayed by three days', 'Duplicate charge on order #{n}',
            'Chargeback opened by {c}', 'Refund stuck in pending for #{n}',
            'Exchange rate mismatch on payout {n}', 'Unmatched incoming transfer from {c}'],
           ['KYC documents rejected for {c}', 'Sanctions screening hit on {c}',
            'Data export request from {c}', 'Annual AML review for {c}',
            'Erasure request #{n}', 'Proof of address expired for {c}'],
           ['Rotate API keys for {c}', 'Approve a limit increase for {c}',
            'Move {c} to the new pricing plan', 'Reconcile the ledger for week {w}',
            'Update the refund wording for {c}', 'Migrate {c} to the {r} data region']
         ])[kind_n][((i - 1) / 40) % 6 + 1],
         '{c}', (array['Northwind Retail', 'Aster Labs', 'Bluebell Foods', 'Kestrel Travel', 'Orbit Media',
                       'Juniper Health', 'Harbor Logistics', 'Quill Studio', 'Maple Pharmacy', 'Summit Gear',
                       'Lumen Energy', 'Fable Books'])[(i * 7) % 12 + 1]),
         '{n}', (40000 + i)::text),
         '{w}', ((i % 52) + 1)::text),
         '{r}', (array['eu-west', 'us-east', 'ap-south', 'eu-central'])[(i / 5) % 4 + 1]),
       (array[
         'Customers started reporting this through support. Logs and the affected accounts are linked in the thread.',
         'Raised by the customer success team after two follow-ups. The customer expects an update today.',
         'Spotted in the daily reconciliation. The amounts and references are in the attached report.',
         'Flagged by the compliance desk. The regulator deadline is in the due date.',
         'Routine work from the weekly ops review. Check the runbook before starting.'
       ])[kind_n] || ' Owner: the ' ||
       (array['Payments', 'Compliance', 'Support', 'Incidents', 'Onboarding', 'Risk', 'Platform', 'Billing'])[team_n] ||
       ' team.',
       status, priority, reporter_id, assignee_id,
       case when status = 'awaiting_approval' then assignee_id end,
       due_at, event_count, created_at, updated_at, updated_at
  from seed_items
 order by i;  -- keys follow i, so neighbouring keys always differ in kind and title

-- History. seq 1 is creation, seq 2..status_n+1 are the transitions along the chain, the rest are comments.
insert into work_item_events (item_id, seq, actor_id, kind, before, after, note, created_at)
select s.id, e.seq,
       case when e.seq = 1 or e.seq = 2 then s.reporter_id
            when e.seq <= s.status_n + 1 and e.seq in (5, 7) then s.lead_id   -- approved, closed: a lead
            when e.seq <= s.status_n + 1 then s.assignee_id
            else (array[s.reporter_id, coalesce(s.assignee_id, s.lead_id), s.lead_id])[1 + (e.seq % 3)] end,
       case when e.seq = 1 then 'created' when e.seq <= s.status_n + 1 then 'status' else 'comment' end,
       case when e.seq = 1 or e.seq > s.status_n + 1 then null
            else jsonb_build_object('status', chain[e.seq - 1]) end,
       case when e.seq = 1 then jsonb_build_object('status', 'new', 'priority', s.priority, 'kind', s.kind)
            when e.seq > s.status_n + 1 then null
            when e.seq = 3 then jsonb_build_object('status', chain[e.seq], 'assignee_id', s.assignee_id)
            else jsonb_build_object('status', chain[e.seq]) end,
       case when e.seq > s.status_n + 1
            then (array['Checked with the customer.', 'Waiting on the ledger team.', 'Escalated to the lead.',
                        'Customer confirmed the fix.', 'Needs a second look.'])[1 + (e.seq % 5)] end,
       s.created_at + (s.updated_at - s.created_at) * (e.seq - 1) / greatest(s.event_count - 1, 1)
  from seed_items s
  cross join lateral generate_series(1, s.event_count) as e(seq),
       (select array['new', 'triaged', 'in_progress', 'awaiting_approval', 'approved', 'resolved', 'closed'] as chain) as c;
