-- 003: tie each user to the Google account that first signed in with their email.
-- If a company later gives that address to a different Google account, sign-in is refused.

alter table users add column google_sub text unique;
