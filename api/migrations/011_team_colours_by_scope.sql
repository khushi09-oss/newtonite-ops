-- 011: spread team colours again, within what each person sees. Migration 010 spread every team in one
-- key order, so a visitor's private workspace team took a slot among the public teams and two public
-- teams ended up sharing a colour. The rule for new teams is "least used among the teams the creator
-- can see"; this applies the same idea to teams that already exist.

-- Public teams: everyone who sees them sees them together, so they share one spread.
with public_teams as (
  select id, (row_number() over (order by key) - 1) % 8 as slot from teams where not private
)
update teams t set color = p.slot from public_teams p where p.id = t.id;

-- Private teams: spread within each workspace, which is the teams its lead leads.
with workspace as (
  select t.id, (row_number() over (partition by m.user_id order by t.key) - 1) % 8 as slot
    from teams t
    join lateral (
      select user_id from team_members where team_id = t.id and role = 'lead' order by user_id limit 1
    ) m on true
   where t.private
)
update teams t set color = w.slot from workspace w where w.id = t.id;
