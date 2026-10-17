-- 008: private teams and people. On the public demo, the admin account is shared by every visitor,
-- so anything a visitor brings (their Google account, a workspace and the people in it) is marked
-- private, and admins skip the membership check only for teams that are not. Off the demo nothing
-- is ever marked, so both flags stay false and admins see everything as before.

alter table users add column private boolean not null default false;
alter table teams add column private boolean not null default false;
