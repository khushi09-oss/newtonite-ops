import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { TeamBars, TrendChart } from "../charts";
import { Icon } from "../icons";
import { pollEvery, useLiveConnected } from "../live";
import { Avatar, ItemKey, Time, describeEvent, teamColor } from "../ui";
import { AddMemberForm, InviteNote, NewTeamForm } from "./Team";

const FEED_SIZE = 8;

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const TILES = [
  { key: "assigned", label: "Assigned to you", icon: "inbox", hint: "Open and yours", to: "/inbox?tab=assigned" },
  { key: "approvals", label: "Waiting for your approval", icon: "shield", hint: "On teams where you approve", to: "/inbox?tab=approvals" },
  { key: "urgent", label: "Urgent and unclaimed", icon: "bell", hint: "Urgent or high, no owner", to: "/inbox?tab=unassigned_urgent", alert: true },
  { key: "overdue", label: "Overdue", icon: "clock", hint: "Open and past due", to: "/items?overdue=true", alert: true },
  { key: "stale", label: "Quiet for 3 days", icon: "users", hint: "Open, no activity", to: "/inbox?tab=stale" },
];

export default function Dashboard() {
  const me = useOutletContext();
  const live = useLiveConnected();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api("/dashboard"), refetchInterval: pollEvery(live) });
  const first = me.name.split(" ")[0];
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>
            {greeting()}, {first}
          </h1>
          <p className="muted">
            {today}.{" "}
            {me.visible_teams.length === 0
              ? "Your workspace is empty. Start with a team."
              : `Here is what needs you across ${me.visible_teams.length} team${me.visible_teams.length === 1 ? "" : "s"}.`}
          </p>
        </div>
      </header>

      {q.isError && <p className="error">Could not load the dashboard: {q.error.message}</p>}
      {q.data?.failed_jobs > 0 && (
        <Link to="/admin?tab=jobs" className="alert-strip">
          <strong>
            {q.data.failed_jobs} background job{q.data.failed_jobs === 1 ? "" : "s"} failed.
          </strong>{" "}
          The notifications they carry have not been delivered. Open Admin to see why and retry.
        </Link>
      )}
      {q.isPending && <div className="page-loading">Loading the dashboard</div>}
      {/* A visitor's own workspace starts empty: walk its owner through it. Someone added to another
          person's team has nothing to set up. */}
      {me.private && q.data && (me.teams.length === 0 || me.teams.some((t) => t.role === "lead")) && (
        <Setup me={me} hasItems={q.data.recent.length > 0} />
      )}
      {me.private && me.teams.length > 0 && params.get("new-team") && (
        <section className="card setup" aria-label="New team">
          <div className="card-head">
            <div>
              <h2>New team</h2>
              <p>You become its lead, and only the people you add can see it.</p>
            </div>
          </div>
          <div className="card-body">
            <NewTeamForm autoFocus onCreated={(t) => navigate(`/teams/${t.id}`)} />
          </div>
        </section>
      )}
      {q.data && me.visible_teams.length > 0 && (
        <>
          <section className="tiles" aria-label="What needs you">
            {TILES.map((t) => {
              const n = q.data.counts[t.key];
              const body = (
                <>
                  <span className="tile-label">
                    <Icon name={t.icon} /> {t.label}
                  </span>
                  <div className="tile-value">{n.toLocaleString()}</div>
                  <div className="tile-hint">{t.hint}</div>
                </>
              );
              const cls = `tile tile-${t.key} ${t.alert && n > 0 ? "alert" : ""}`;
              return t.to ? (
                <Link key={t.key} to={t.to} className={cls}>
                  {body}
                </Link>
              ) : (
                <div key={t.key} className={cls}>
                  {body}
                </div>
              );
            })}
          </section>

          <div className="dash-grid">
            <div className="dash-col">
              <section className="card">
                <div className="card-head">
                  <div>
                    <h2>Open work by team</h2>
                    <p>
                      {q.data.by_team_capped ? `The ${q.data.by_team.length} busiest of ${q.data.teams_with_open_work} teams. ` : ""}
                      Bar length is the team's open items; segments follow the workflow, earliest lightest.
                    </p>
                  </div>
                </div>
                <div className="card-body">
                  {q.data.by_team.length ? <TeamBars teams={q.data.by_team} /> : <p className="empty">No open work on your teams.</p>}
                  {q.data.by_team_capped && (
                    <p className="muted small more-note">
                      {q.data.teams_with_open_work - q.data.by_team.length} more teams have open work.{" "}
                      <Link to="/items">See all open items</Link>
                    </p>
                  )}
                </div>
              </section>
              <section className="card">
                <div className="card-head">
                  <div>
                    <h2>Created and resolved</h2>
                    <p>Items per day on your teams, last {q.data.trend.length} days.</p>
                  </div>
                </div>
                <div className="card-body">
                  <TrendChart trend={q.data.trend} />
                </div>
              </section>
            </div>

            <section className="card">
              <div className="card-head">
                <div>
                  <h2>Recent activity</h2>
                  <p>The latest changes on your teams.</p>
                </div>
              </div>
              <ul className="feed" style={{ marginTop: 10 }}>
                {q.data.recent.length === 0 && <li className="empty">Nothing has happened yet.</li>}
                {q.data.recent.slice(0, FEED_SIZE).map((e) => (
                  <li key={e.id}>
                    <Avatar name={e.actor_name} size={24} />
                    <div style={{ minWidth: 0 }}>
                      <div className="feed-line">
                        <span>
                          <strong>{e.actor_name}</strong> {describeEvent(e)}
                        </span>
                        <Time iso={e.created_at} />
                      </div>
                      <Link to={`/items/${e.item_id}`} className="feed-item" title={`${e.team} team`}>
                        <span className="team-swatch" style={{ background: teamColor(e.team_color) }} />
                        <ItemKey teamKey={e.team_key} number={e.number} />
                        <span className="feed-title">{e.title}</span>
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function Setup({ me, hasItems }) {
  const navigate = useNavigate();
  const [invite, setInvite] = useState(null);
  const team = me.teams.find((t) => t.role === "lead");
  const members = useQuery({
    queryKey: ["members", team?.id],
    queryFn: () => api(`/teams/${team.id}/members`),
    enabled: Boolean(team),
  });
  const steps = [
    { title: "Create your first team", done: Boolean(team), hint: "You become its lead. Items on it get the team's own key, like PAY-1." },
    {
      title: "Add a teammate",
      done: (members.data?.members.length || 0) > 1,
      hint: "By their Google email. Leads approve work, members do it, viewers follow it.",
    },
    { title: "Create your first item", done: hasItems, hint: "Then claim it, request approval, and close it." },
  ];
  // Wait for the roster, or a finished workspace flashes its setup card on every load.
  if (team && members.isPending) return null;
  if (steps.every((s) => s.done)) return null;
  const current = steps.findIndex((s) => !s.done);

  return (
    <section className="card setup" aria-label="Set up your workspace">
      <div className="card-head">
        <div>
          <h2>Set up your workspace</h2>
          <p>Three steps to run real work through it. Only you and the people you add can see it.</p>
        </div>
        <span className="muted small">
          {steps.filter((s) => s.done).length} of {steps.length} done
        </span>
      </div>
      <ol className="steps">
        {steps.map((s, i) => (
          <li key={s.title} className={s.done ? "done" : i === current ? "current" : ""}>
            <span className="step-mark" aria-hidden="true">
              {s.done ? "✓" : i + 1}
            </span>
            <div className="step-body">
              <strong>{s.title}</strong>
              <span className="muted small">{s.hint}</span>
              {i === current && i === 0 && <NewTeamForm autoFocus />}
              {/* The step completes as soon as someone is added; the message to send them stays. */}
              {i === 1 && invite && <InviteNote invite={invite} />}
              {i === current && i === 1 && (
                <>
                  <AddMemberForm team={team} demo onAdded={setInvite} />
                  <button className="link small" onClick={() => navigate("/items?new=1")}>
                    Skip for now and create an item
                  </button>
                </>
              )}
              {i === current && i === 2 && (
                <button className="primary" onClick={() => navigate("/items?new=1")}>
                  New item
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
