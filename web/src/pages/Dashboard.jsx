import { useQuery } from "@tanstack/react-query";
import { Link, useOutletContext } from "react-router-dom";
import { api } from "../api";
import { TeamBars, TrendChart } from "../charts";
import { Icon } from "../icons";
import { pollEvery, useLiveConnected } from "../live";
import { Avatar, ItemKey, Time, describeEvent, teamSwatch } from "../ui";

const FEED_SIZE = 8;

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const TILES = [
  { key: "assigned", label: "Assigned to you", icon: "inbox", hint: "Open and yours", to: "/inbox" },
  { key: "approvals", label: "Waiting for your approval", icon: "shield", hint: "On teams you lead", to: "/inbox" },
  { key: "urgent", label: "Urgent and unclaimed", icon: "bell", hint: "Urgent or high, no owner", to: "/inbox", alert: true },
  { key: "overdue", label: "Overdue", icon: "clock", hint: "Open and past due", to: "/items?overdue=true", alert: true },
  { key: "stale", label: "Quiet for 3 days", icon: "users", hint: "Open, no activity", to: "/inbox" },
];

export default function Dashboard() {
  const me = useOutletContext();
  const live = useLiveConnected();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api("/dashboard"), refetchInterval: pollEvery(live) });
  const first = me.name.split(" ")[0];
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>
            {greeting()}, {first}
          </h1>
          <p className="muted">
            {today}. Here is what needs you across {me.is_admin ? "every team" : `${me.teams.length} team${me.teams.length === 1 ? "" : "s"}`}.
          </p>
        </div>
      </header>

      {q.isError && <p className="error">Could not load the dashboard: {q.error.message}</p>}
      {q.isPending && <div className="page-loading">Loading the dashboard</div>}
      {q.data && (
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
              const cls = `tile ${t.alert && n > 0 ? "alert" : ""}`;
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
                    <p>Bar length is the team's open items; segments follow the workflow, earliest lightest.</p>
                  </div>
                </div>
                <div className="card-body">
                  {q.data.by_team.length ? <TeamBars teams={q.data.by_team} /> : <p className="empty">No open work on your teams.</p>}
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
                        <span className="team-swatch" style={{ background: teamSwatch(e.team) }} />
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
