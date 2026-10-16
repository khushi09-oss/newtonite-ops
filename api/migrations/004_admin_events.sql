-- 004: an append-only record of every admin action: invites, role changes, deactivations, teams.

create table admin_events (
  id bigserial primary key,
  actor_id uuid not null references users,
  kind text not null,
  target jsonb not null,
  created_at timestamptz not null default now()
);
create index on admin_events (created_at desc);

-- One guard for every append-only table, naming whichever table was touched.
create or replace function raise_append_only() returns trigger language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name;
end $$;
create trigger admin_events_no_update before update on admin_events
  for each row execute function raise_append_only();
create trigger admin_events_no_delete before delete on admin_events
  for each row execute function raise_append_only();
