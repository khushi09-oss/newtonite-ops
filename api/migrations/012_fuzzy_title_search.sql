-- 012: search forgives typos in titles. Full-text search matches whole words and their stems, so
-- "refnd" found nothing. pg_trgm compares runs of three letters; a GIN index on the title lets a
-- word-similarity match use an index like the full-text one does. The match threshold is set per
-- search, in the query's own transaction (app/items.py: open_search).

create extension if not exists pg_trgm;
create index if not exists work_items_title_trgm_idx on work_items using gin (title gin_trgm_ops);

