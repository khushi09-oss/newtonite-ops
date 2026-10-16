-- 001: core schema. Applied once; later changes go in new numbered files.

create table if not exists schema_migrations (
  name text primary key,
  applied_at timestamptz not null default now()
);

create table users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  name text not null,
  is_admin boolean not null default false
);

create table teams (
  id uuid primary key default gen_random_uuid(),
  name text unique not null
);

create table team_members (
  team_id uuid not null references teams,
  user_id uuid not null references users,
  role text not null check (role in ('viewer', 'member', 'lead')),
  primary key (team_id, user_id)
);
create index on team_members (user_id);

create table work_items (
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references teams,
  kind text not null check (kind in ('incident', 'customer_issue', 'payment', 'compliance', 'task')),
  title text not null,
  description text not null default '',
  status text not null default 'new'
    check (status in ('new', 'triaged', 'in_progress', 'awaiting_approval', 'approved', 'resolved', 'closed')),
  priority smallint not null default 2 check (priority between 0 and 3),
  reporter_id uuid not null references users,
  assignee_id uuid references users,
  approval_requested_by uuid references users,
  due_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_activity_at timestamptz not null default now(),
  search tsvector generated always as (
    setweight(to_tsvector('english', title), 'A') ||
    setweight(to_tsvector('english', description), 'B')
  ) stored
);
-- The list endpoint's filters and its keyset cursor all land on this one index.
create index on work_items (team_id, status, priority, updated_at desc, id desc);
create index on work_items (assignee_id) where status not in ('resolved', 'closed');
create index on work_items (team_id, last_activity_at) where status not in ('resolved', 'closed');
create index on work_items using gin (search);

create table work_item_events (
  id bigserial primary key,
  item_id uuid not null references work_items,
  seq integer not null,
  actor_id uuid not null references users,
  kind text not null,
  before jsonb,
  after jsonb,
  note text,
  request_id text,
  created_at timestamptz not null default now(),
  unique (item_id, seq)
);

-- History is append-only. Nothing, including a bug, may rewrite it.
create or replace function raise_append_only() returns trigger language plpgsql as $$
begin
  raise exception 'work_item_events is append-only';
end $$;
create trigger events_no_update before update on work_item_events
  for each row execute function raise_append_only();
create trigger events_no_delete before delete on work_item_events
  for each row execute function raise_append_only();

create table idempotency_keys (
  key text not null,
  user_id uuid not null references users,
  request_hash text not null,
  status integer not null,
  body jsonb not null,
  created_at timestamptz not null default now(),
  primary key (key, user_id)
);

create table outbox (
  id bigserial primary key,
  kind text not null,
  payload jsonb not null,
  status text not null default 'pending' check (status in ('pending', 'done', 'dead')),
  attempts integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  last_error text,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);
create index on outbox (next_attempt_at) where status = 'pending';

create table notifications (
  id bigserial primary key,
  user_id uuid not null references users,
  event_id bigint not null references work_item_events,
  read_at timestamptz,
  created_at timestamptz not null default now(),
  unique (user_id, event_id)
);
create index on notifications (user_id) where read_at is null;

create table watchers (
  item_id uuid not null references work_items,
  user_id uuid not null references users,
  primary key (item_id, user_id)
);
