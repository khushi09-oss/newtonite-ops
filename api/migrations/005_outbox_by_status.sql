-- 005: the admin job screen lists and counts the outbox by status, newest first.

create index on outbox (status, id desc);
