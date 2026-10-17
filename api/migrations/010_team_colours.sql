-- 010: a colour stored with each team, one of the eight palette slots (0 to 7). Every screen reads it,
-- so a team looks the same everywhere and no screen works it out from the team's name. A new team
-- takes the slot least used among the teams its creator can see (app/items.py: next_team_colour).
-- Existing teams are spread across the slots in key order.

alter table teams add column color smallint;
with ranked as (select id, (row_number() over (order by key) - 1) % 8 as slot from teams)
update teams t set color = ranked.slot from ranked where ranked.id = t.id;
alter table teams alter column color set not null;
alter table teams add constraint teams_color_slot check (color between 0 and 7);
