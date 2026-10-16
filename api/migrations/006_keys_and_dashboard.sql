-- 006: a short human key for every item (OPS-1234), and indexes the dashboard's counts land on.

alter table work_items add column number bigserial;
create unique index on work_items (number);

-- Overdue: open items on my teams whose due date has passed.
create index on work_items (team_id, due_at)
  where status not in ('resolved', 'closed') and due_at is not null;
-- Created per day, per team, for the trend.
create index on work_items (team_id, created_at);
-- Recent activity and resolved per day: newest events first.
create index on work_item_events (created_at desc);
