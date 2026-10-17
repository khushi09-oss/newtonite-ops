-- 009: team names are unique among public teams only. Two visitors may each call their own
-- workspace team "Marketing", and neither should learn from an error that the other exists.

alter table teams drop constraint teams_name_key;
create unique index teams_public_name_idx on teams (name) where not private;
