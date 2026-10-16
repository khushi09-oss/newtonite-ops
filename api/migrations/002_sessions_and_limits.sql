-- 002: server-side sessions (so sign-out and deactivation take effect at once) and rate-limit counters.

alter table users add column deactivated_at timestamptz;

create table sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz
);
create index on sessions (user_id) where revoked_at is null;

-- One row per key per window. Counted in Postgres so the limit holds across every API instance.
create table rate_limits (
  key text not null,
  window_start timestamptz not null,
  hits integer not null default 1,
  primary key (key, window_start)
);
