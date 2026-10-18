import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useOutletContext, useSearchParams } from "react-router-dom";
import { api, useRetryKey } from "../api";
import { Icon } from "../icons";
import { Avatar, LoadError, RosterFilter, Time, rosterQuery, teamColor } from "../ui";

const ROLES = ["viewer", "member", "lead"];

function useAdminMutation(fn) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => qc.invalidateQueries(),
  });
}

const TABS = [
  ["people", "People"],
  ["teams", "Teams"],
  ["jobs", "Background jobs"],
  ["log", "Admin log"],
];

export default function Admin() {
  const me = useOutletContext();
  const [params, setParams] = useSearchParams();
  const tab = TABS.some(([k]) => k === params.get("tab")) ? params.get("tab") : "people";
  // Failed jobs are the one thing here that needs action, so their count shows on the tab.
  const jobs = useQuery({
    queryKey: ["admin", "jobs", "dead"],
    queryFn: () => api("/admin/jobs?status=dead"),
    enabled: me.is_admin,
  });
  if (!me.is_admin) return <Navigate to="/inbox" replace />;
  const failed = jobs.data?.counts.dead || 0;

  return (
    <div className="page">
      <header className="page-head tabbed">
        <div>
          <h1>Admin</h1>
          <p className="muted">Invite people, set their teams and roles, and keep background work healthy.</p>
        </div>
      </header>
      <div className="tabs" role="tablist" aria-label="Admin">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" className="tab" aria-selected={k === tab} onClick={() => setParams({ tab: k })}>
            {label}
            {k === "jobs" && failed > 0 && <span className="tab-count hot">{failed}</span>}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "people" && <People me={me} />}
        {tab === "teams" && <Teams />}
        {tab === "jobs" && <Jobs />}
        {tab === "log" && <AdminLog />}
      </div>
    </div>
  );
}

function People({ me }) {
  const [q, setQ] = useState("");
  const [inviting, setInviting] = useState(false);
  const people = useQuery({ queryKey: ["admin", "users", q], queryFn: () => api(`/admin/users?q=${encodeURIComponent(q)}`) });
  const change = useAdminMutation(({ id, ...body }) => api(`/admin/users/${id}`, { method: "PATCH", body }));
  return (
    <>
      <div className="filter-bar">
        <label className="search">
          <Icon name="search" />
          <input type="search" aria-label="Search people" placeholder="Search by name or email" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <button className="primary" onClick={() => setInviting(true)}>
          <Icon name="plus" /> Invite person
        </button>
      </div>
      {change.isError && <p className="error">{change.error.message}</p>}
      {people.isError && <p className="error">{people.error.message}</p>}
      <div className="table">
        <div className="trow thead people-row">
          <span>Person</span>
          <span>Teams</span>
          <span>Status</span>
          <span />
        </div>
        {people.data?.users.length === 0 && <div className="group-note">Nobody matches that search.</div>}
        {people.data?.users.map((u) => (
          <div key={u.id} className={`trow people-row ${u.deactivated_at ? "off" : ""}`}>
            <span className="tperson">
              <Avatar name={u.name} size={28} />
              <span className="tperson-text">
                <span>
                  <strong>{u.name}</strong>
                  {u.id === me.id && <span className="muted"> (you)</span>}
                  {u.is_admin && <span className="tag">Admin</span>}
                </span>
                <span className="muted small">{u.email}</span>
              </span>
            </span>
            <span className="tteams">
              {u.teams.length === 0 && <span className="muted small">No teams</span>}
              {u.teams.map((t) => (
                <span key={t.team_id} className="team-chip">
                  <span className="team-swatch" style={{ background: teamColor(t.color) }} />
                  {t.team}
                  <span className="role">{t.role}</span>
                </span>
              ))}
            </span>
            <span className="muted small">
              {u.deactivated_at ? "Deactivated" : u.signed_in ? <>Signed in <Time iso={u.last_sign_in} /></> : "Invited, not signed in yet"}
            </span>
            <span className="tactions">
              {u.id !== me.id && (
                <>
                  <button className="ghost small-btn" disabled={change.isPending} onClick={() => change.mutate({ id: u.id, is_admin: !u.is_admin })}>
                    {u.is_admin ? "Remove admin" : "Make admin"}
                  </button>
                  <button className="ghost small-btn" disabled={change.isPending} onClick={() => change.mutate({ id: u.id, active: Boolean(u.deactivated_at) })}>
                    {u.deactivated_at ? "Reactivate" : "Deactivate"}
                  </button>
                </>
              )}
            </span>
          </div>
        ))}
      </div>
      {people.data?.capped && <p className="muted small more-note">Showing the first 50. Search to narrow it down.</p>}
      {/* Show whoever was just invited, even when the full list runs past its first page. */}
      {inviting && <InviteDialog onDone={() => setInviting(false)} onInvited={setQ} />}
    </>
  );
}

const TEAM_FILTER = { label: "Find a team", placeholder: "Find a team by name or code", note: "Showing the first 100 teams. Search to find any other." };

function InviteDialog({ onDone, onInvited }) {
  const dialog = useRef(null);
  const [findTeam, setFindTeam] = useState("");
  const teams = useQuery({
    queryKey: ["admin", "teams", findTeam],
    queryFn: () => api(rosterQuery("/admin/teams", findTeam)),
    placeholderData: keepPreviousData,
  });
  const [form, setForm] = useState({ email: "", name: "", team_id: "", role: "member", is_admin: false });
  const retry = useRetryKey();
  const send = useAdminMutation(() =>
    api("/admin/users", {
      method: "POST",
      key: retry.key(),
      body: {
        email: form.email.trim(),
        name: form.name.trim(),
        is_admin: form.is_admin,
        teams: form.team_id ? [{ team_id: form.team_id, role: form.role }] : [],
      },
    }),
  );
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  // A native modal: focus is trapped, Escape closes it, and the page behind is inert.
  useEffect(() => {
    dialog.current.showModal();
    dialog.current.querySelector("input").focus();
  }, []);

  return (
    <dialog ref={dialog} className="modal" onClose={onDone} aria-labelledby="invite-title">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send.mutate(undefined, {
            onSuccess: (person) => {
              retry.done();
              onInvited(person.email);
              dialog.current.close();
            },
          });
        }}
      >
        <div className="modal-head">
          <h2 id="invite-title">Invite a person</h2>
          <button type="button" className="ghost" aria-label="Close (Esc)" onClick={() => dialog.current.close()}>
            Esc
          </button>
        </div>
        <p className="muted small">They sign in with Google using this email. Nothing is sent to them.</p>
        <div className="modal-fields two">
          <label>
            Google email
            <input type="email" required value={form.email} onChange={set("email")} />
          </label>
          <label>
            Name
            <input required value={form.name} onChange={set("name")} />
          </label>
          <RosterFilter data={teams.data} value={findTeam} onChange={setFindTeam} {...TEAM_FILTER} />
          <label>
            Team
            <select value={form.team_id} onChange={set("team_id")}>
              <option value="">No team yet</option>
              {teams.data?.teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Role on that team
            <select value={form.role} onChange={set("role")} disabled={!form.team_id}>
              {ROLES.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
        </div>
        <label className="check">
          <input type="checkbox" checked={form.is_admin} onChange={set("is_admin")} /> Make them an admin too
        </label>
        {send.isError && <p className="error">{send.error.message}</p>}
        <div className="modal-foot">
          <button type="button" onClick={() => dialog.current.close()}>
            Cancel
          </button>
          <button className="primary" disabled={send.isPending}>
            Invite
          </button>
        </div>
      </form>
    </dialog>
  );
}

function Teams() {
  const [findTeam, setFindTeam] = useState("");
  const teams = useQuery({
    queryKey: ["admin", "teams", findTeam],
    queryFn: () => api(rosterQuery("/admin/teams", findTeam)),
    placeholderData: keepPreviousData,
  });
  const [selected, setSelected] = useState(null);
  const [name, setName] = useState("");
  const retry = useRetryKey();
  const create = useAdminMutation(() => api("/admin/teams", { method: "POST", key: retry.key(), body: { name: name.trim() } }));
  const list = teams.data?.teams || [];
  const team = list.find((t) => t.id === selected) || list[0];
  if (teams.isError) return <LoadError query={teams} />;
  return (
    <div className="teams-grid">
      <div className="teams-side">
        <form
          className="team-new"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(undefined, {
              onSuccess: (t) => {
                retry.done();
                setName("");
                setSelected(t.id);
              },
            });
          }}
        >
          <input required aria-label="New team name" placeholder="New team name" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="primary" disabled={create.isPending || !name.trim()}>
            Create
          </button>
        </form>
        {create.isError && <p className="error">{create.error.message}</p>}
        <RosterFilter data={teams.data} value={findTeam} onChange={setFindTeam} {...TEAM_FILTER} />
        <div className="team-list">
          {list.map((t) => (
            <button key={t.id} className={`team-pick ${t.id === team?.id ? "on" : ""}`} aria-pressed={t.id === team?.id} onClick={() => setSelected(t.id)}>
              <span className="team-swatch" style={{ background: teamColor(t.color) }} />
              <span className="grow">{t.name}</span>
              <span className="item-key">{t.key}</span>
              <span className="count-inline">{t.members}</span>
            </button>
          ))}
        </div>
      </div>
      {team && <Roster key={team.id} team={team} />}
    </div>
  );
}

function Roster({ team }) {
  const [filter, setFilter] = useState("");
  const roster = useQuery({
    queryKey: ["admin", "roster", team.id, filter],
    queryFn: () => api(rosterQuery(`/admin/teams/${team.id}/members`, filter)),
    placeholderData: keepPreviousData,
  });
  // Search the server as you type, so anyone can be added, not only the first page of people.
  const [find, setFind] = useState("");
  const people = useQuery({
    queryKey: ["admin", "users", find],
    queryFn: () => api(`/admin/users?q=${encodeURIComponent(find)}`),
  });
  const setRole = useAdminMutation(({ userId, role }) =>
    api(`/admin/teams/${team.id}/members/${userId}`, { method: "PUT", body: { role } }),
  );
  const remove = useAdminMutation((userId) => api(`/admin/teams/${team.id}/members/${userId}`, { method: "DELETE" }));
  const onTeam = new Set(roster.data?.members.map((m) => m.id));
  const candidates = (people.data?.users || []).filter((u) => !onTeam.has(u.id) && !u.deactivated_at);
  const pick = candidates.find((u) => u.email === find.trim()) || (candidates.length === 1 ? candidates[0] : null);
  const error = setRole.error || remove.error;
  return (
    <section className="card roster">
      <div className="card-head">
        <h2>
          <span className="team-swatch" style={{ background: teamColor(team.color) }} /> {team.name}
          <span className="item-key">{team.key}</span>
        </h2>
        <p className="muted small">
          {team.members} member{team.members === 1 ? "" : "s"}. Items read {team.key}-1, {team.key}-2 and on.
        </p>
      </div>
      <form
        className="roster-add"
        onSubmit={(e) => {
          e.preventDefault();
          setRole.mutate({ userId: pick.id, role: "member" }, { onSuccess: () => setFind("") });
        }}
      >
        <input
          type="search"
          list={`people-${team.id}`}
          aria-label={`Add someone to ${team.name}`}
          placeholder={`Add someone to ${team.name} by name or email`}
          value={find}
          onChange={(e) => setFind(e.target.value)}
        />
        <datalist id={`people-${team.id}`}>
          {candidates.map((u) => (
            <option key={u.id} value={u.email}>
              {u.name}
            </option>
          ))}
        </datalist>
        <button disabled={!pick || setRole.isPending}>{pick ? `Add ${pick.name}` : "Add as member"}</button>
      </form>
      {error && <p className="error roster-note">{error.message}</p>}
      {remove.isSuccess && remove.data.released > 0 && (
        <p className="muted small roster-note">
          Released {remove.data.released} open item(s) they owned; each release is in that item's history.
        </p>
      )}
      {setRole.isSuccess && setRole.data.released > 0 && (
        <p className="muted small roster-note">
          Viewers do no work, so {setRole.data.released} open item(s) they owned were released; each release is in that
          item's history.
        </p>
      )}
      <RosterFilter data={roster.data} value={filter} onChange={setFilter} />
      <LoadError query={roster} />
      {roster.data?.members.length === 0 && (
        <div className="group-note">{filter ? "Nobody on this team matches." : "Nobody is on this team yet."}</div>
      )}
      {roster.data?.members.map((m) => (
        <div key={m.id} className={`trow roster-row ${m.deactivated_at ? "off" : ""}`}>
          <span className="tperson">
            <Avatar name={m.name} size={28} />
            <span className="tperson-text">
              <span>
                <strong>{m.name}</strong>
                {m.deactivated_at && <span className="tag">Deactivated</span>}
              </span>
              <span className="muted small">{m.email}</span>
            </span>
          </span>
          <select aria-label={`Role for ${m.name}`} value={m.role} onChange={(e) => setRole.mutate({ userId: m.id, role: e.target.value })} disabled={setRole.isPending}>
            {ROLES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <button className="ghost small-btn" disabled={remove.isPending} onClick={() => remove.mutate(m.id)}>
            Remove
          </button>
        </div>
      ))}
    </section>
  );
}

const JOB_TABS = [
  ["dead", "Failed"],
  ["pending", "Waiting"],
  ["done", "Done"],
];

function Jobs() {
  const [status, setStatus] = useState("dead");
  const jobs = useQuery({ queryKey: ["admin", "jobs", status], queryFn: () => api(`/admin/jobs?status=${status}`) });
  const retry = useAdminMutation((id) => api(`/admin/jobs/${id}/retry`, { method: "POST" }));
  const counts = jobs.data?.counts;
  return (
    <>
      <p className="muted small tab-hint">
        Notifications are sent by background jobs. A job that fails eight times stops and waits here. Retrying is
        safe: nobody can be notified twice about the same change.
      </p>
      <div className="segmented" role="group" aria-label="Job status">
        {JOB_TABS.map(([value, label]) => (
          <button key={value} aria-pressed={status === value} onClick={() => setStatus(value)}>
            {label} <span className="count-inline">{counts ? counts[value] : ""}</span>
          </button>
        ))}
      </div>
      {retry.isError && <p className="error">{retry.error.message}</p>}
      <LoadError query={jobs} />
      {jobs.data?.jobs.length === 0 ? (
        <div className="empty-state">
          <strong>{status === "dead" ? "No failed jobs." : "None right now."}</strong>
          {status === "dead" && <span className="muted">Everything has been delivered.</span>}
        </div>
      ) : (
        <div className="table">
          {jobs.data?.jobs.map((j) => (
            <div key={j.id} className="trow job-row">
              <span className="tperson-text">
                <strong>{j.event_kind ? `Notify about a ${j.event_kind}` : j.kind}</strong>
                {j.item_id && (
                  <Link to={`/items/${j.item_id}`} className="muted small">
                    {j.item_title}
                  </Link>
                )}
              </span>
              <span className={`small ${j.last_error ? "late-text" : "muted"}`}>{j.last_error || "No error"}</span>
              <span className="muted small">
                {j.attempts} attempt{j.attempts === 1 ? "" : "s"}, <Time iso={j.processed_at || j.created_at} />
              </span>
              <span className="tactions">
                {j.status === "dead" && (
                  <button className="small-btn" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>
                    Retry
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      {jobs.data?.capped && <p className="muted small more-note">Showing the newest 50.</p>}
    </>
  );
}

// One sentence per admin change, naming who and where when the log row carries it.
function describeAdmin(e) {
  const t = e.target || {};
  const who = e.target_user_name || "someone";
  const team = e.target_team_name || "a team";
  switch (e.kind) {
    case "invited":
      return `invited ${t.email}`;
    case "team_created":
      return `created the team ${t.name}`;
    case "role_set":
      return t.before ? `changed ${who} on ${team} from ${t.before} to ${t.after}` : `added ${who} to ${team} as ${t.after}`;
    case "team_colour_set":
      return `changed the colour of ${team}`;
    case "member_removed":
      return `removed ${who} from ${team}`;
    case "user_changed":
      if ("active" in t) return `${t.active ? "reactivated" : "deactivated"} ${who}`;
      if ("is_admin" in t) return t.is_admin ? `made ${who} an admin` : `removed admin from ${who}`;
      return `changed ${who}`;
    case "job_retried":
      return "retried a failed background job";
    default:
      return e.kind;
  }
}

function AdminLog() {
  const log = useQuery({ queryKey: ["admin", "events"], queryFn: () => api("/admin/events?limit=50") });
  if (log.isError) return <LoadError query={log} />;
  if (log.data?.events.length === 0) {
    return (
      <div className="empty-state">
        <strong>No admin changes yet.</strong>
        <span className="muted">Invites, role changes and retries will be listed here.</span>
      </div>
    );
  }
  return (
    <>
      <p className="muted small tab-hint">Every change an admin makes, newest first. Nothing here can be edited.</p>
      <div className="table">
        {log.data?.events.map((e) => (
          <div key={e.id} className="trow log-row">
            <Avatar name={e.actor_name} size={24} />
            <span className="log-line">
              <strong>{e.actor_name}</strong> {describeAdmin(e)}
            </span>
            <Time iso={e.created_at} />
          </div>
        ))}
      </div>
      {log.data?.capped && <p className="muted small more-note">Showing the newest 50.</p>}
    </>
  );
}
