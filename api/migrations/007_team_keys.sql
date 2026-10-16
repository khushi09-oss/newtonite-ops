-- 007: keys per team. Every team gets a short code and its own counter, so an item reads as
-- RSK-1204 or CMP-311 instead of one company-wide OPS- sequence.

alter table teams add column key text;
alter table teams add column next_number bigint not null default 0;

update teams set key = case name
  when 'Payments' then 'PAY' when 'Compliance' then 'CMP' when 'Support' then 'SUP'
  when 'Incidents' then 'INC' when 'Onboarding' then 'ONB' when 'Risk' then 'RSK'
  when 'Platform' then 'PLT' when 'Billing' then 'BIL' end;

-- Any other team: the first three letters of its name, numbered if that code is already taken.
with others as (
  select id,
         upper(left(regexp_replace(name, '[^A-Za-z]', '', 'g') || 'XXX', 3)) as base,
         row_number() over (
           partition by upper(left(regexp_replace(name, '[^A-Za-z]', '', 'g') || 'XXX', 3)) order by name
         ) as n
    from teams where key is null
)
update teams t
   set key = o.base || case when o.n = 1 and not exists (select 1 from teams x where x.key = o.base)
                            then '' else o.n::text end
  from others o where t.id = o.id;

alter table teams alter column key set not null;
create unique index on teams (key);

-- Renumber items within their team, keeping creation order (the old global number was that order).
drop index if exists work_items_number_idx;
alter table work_items alter column number drop default;
drop sequence if exists work_items_number_seq;
update work_items w set number = r.rn
  from (select id, row_number() over (partition by team_id order by number) as rn from work_items) r
 where w.id = r.id;
alter table work_items alter column number set not null;
create unique index on work_items (team_id, number);
update teams t set next_number = coalesce((select max(number) from work_items where team_id = t.id), 0);
