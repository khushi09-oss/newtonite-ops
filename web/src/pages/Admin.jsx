import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, useOutletContext } from "react-router-dom";
import { api } from "../api";
import { Time } from "../ui";

const ROLES = ["viewer", "member", "lead"];

function useAdminMutation(fn) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
}

export default function Admin() {
  const me = useOutletContext();
  const [team, setTeam] = useState(null);
  if (!me.is_admin) return <Navigate to="/inbox" replace />;
  return (
    <div className="page">
      <header className="page-head">
        <h1>Admin</h1>
        <p className="muted">Invite people, set their teams and roles, and see every admin change.</p>
      </header>
      <People me={me} />
      <Teams selected={team} onSelect={setTeam} />
      {team && <Roster team={team} onClose={() => setTeam(null)} />}
      <AdminLog />
    </div>
  );
}

function People({ me }) {
  const [q, setQ] = useState("");
  const people = useQuery({ queryKey: ["admin", "users", q], queryFn: () => api(`/admin/users?q=${encodeURIComponent(q)}`) });
  const change = useAdminMutation(({ id, ...body }) => api(`/admin/users/${id}`, { method: "PATCH", body }));
  return (
    <section className="bucket">
      <h2>People</h2>
      {/* Show whoever was just invited, even when the full list runs past its first page. */}
      <InviteForm onInvited={setQ} />
      <div className="toolbar">
        <input type="search" placeholder="Search by name or email" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {change.isError && <p className="error">{change.error.message}</p>}
      {people.isError && <p className="error">{people.error.message}</p>}
      <div className="rows">
        {people.data?.users.map((u) => (
          <div key={u.id} className="row admin-row">
            <span className="row-title">
              <strong>{u.name}</strong> <span className="muted">{u.email}</span>
              {u.is_admin && <span className="tag">admin</span>}
            </span>
            <span className="row-team">
              {u.teams.length ? u.teams.map((t) => `${t.team} ${t.role}`).join(", ") : "No teams"}
            </span>
            <span className="muted small">
              {u.deactivated_at ? "Deactivated" : u.signed_in ? <>Signed in <Time iso={u.last_sign_in} /></> : "Invited, not signed in yet"}
            </span>
            <span className="admin-actions">
              {u.id !== me.id && (
                <>
                  <button className="link" disabled={change.isPending} onClick={() => change.mutate({ id: u.id, is_admin: !u.is_admin })}>
                    {u.is_admin ? "Remove admin" : "Make admin"}
                  </button>
                  <button className="link" disabled={change.isPending} onClick={() => change.mutate({ id: u.id, active: Boolean(u.deactivated_at) })}>
                    {u.deactivated_at ? "Reactivate" : "Deactivate"}
                  </button>
                </>
              )}
            </span>
          </div>
        ))}
      </div>
      {people.data?.capped && <p className="muted small">Showing the first 50. Search to narrow it down.</p>}
    </section>
  );
}

function InviteForm({ onInvited }) {
  const teams = useQuery({ queryKey: ["admin", "teams"], queryFn: () => api("/admin/teams") });
  const blank = { email: "", name: "", team_id: "", role: "member", is_admin: false };
  const [form, setForm] = useState(blank);
  const send = useAdminMutation(() =>
    api("/admin/users", {
      method: "POST",
      body: {
        email: form.email.trim(),
        name: form.name.trim(),
        is_admin: form.is_admin,
        teams: form.team_id ? [{ team_id: form.team_id, role: form.role }] : [],
      },
    }),
  );
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <form
      className="new-item"
      onSubmit={(e) => {
        e.preventDefault();
        send.mutate(undefined, {
          onSuccess: (person) => {
            setForm(blank);
            onInvited(person.email);
          },
        });
      }}
    >
      <div className="new-item-row">
        <input type="email" required placeholder="Their Google email" value={form.email} onChange={set("email")} />
        <input required placeholder="Name" value={form.name} onChange={set("name")} />
        <select value={form.team_id} onChange={set("team_id")}>
          <option value="">No team yet</option>
          {teams.data?.teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={form.role} onChange={set("role")} disabled={!form.team_id}>
          {ROLES.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <label className="check">
          <input type="checkbox" checked={form.is_admin} onChange={set("is_admin")} /> Admin
        </label>
        <button className="primary" disabled={send.isPending}>
          Invite
        </button>
      </div>
      {send.isError && <p className="error">{send.error.message}</p>}
      {send.isSuccess && <p className="muted small">Invited. They can now sign in with Google using that email.</p>}
    </form>
  );
}

function Teams({ selected, onSelect }) {
  const teams = useQuery({ queryKey: ["admin", "teams"], queryFn: () => api("/admin/teams") });
  const [name, setName] = useState("");
  const create = useAdminMutation(() => api("/admin/teams", { method: "POST", body: { name: name.trim() } }));
  return (
    <section className="bucket">
      <h2>Teams</h2>
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(undefined, { onSuccess: () => setName("") });
        }}
      >
        <input required placeholder="New team name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" disabled={create.isPending || !name.trim()}>
          Create team
        </button>
      </form>
      {create.isError && <p className="error">{create.error.message}</p>}
      <div className="chips">
        {teams.data?.teams.map((t) => (
          <button key={t.id} className={`chip ${selected?.id === t.id ? "chip-on" : ""}`} onClick={() => onSelect(t)}>
            {t.name} <span className="role">{t.members}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function Roster({ team, onClose }) {
  const roster = useQuery({ queryKey: ["admin", "roster", team.id], queryFn: () => api(`/admin/teams/${team.id}/members`) });
  const people = useQuery({ queryKey: ["admin", "users", ""], queryFn: () => api("/admin/users") });
  const [adding, setAdding] = useState("");
  const setRole = useAdminMutation(({ userId, role }) =>
    api(`/admin/teams/${team.id}/members/${userId}`, { method: "PUT", body: { role } }),
  );
  const remove = useAdminMutation((userId) => api(`/admin/teams/${team.id}/members/${userId}`, { method: "DELETE" }));
  const onTeam = new Set(roster.data?.members.map((m) => m.id));
  const error = setRole.error || remove.error;
  return (
    <section className="bucket">
      <h2>
        {team.name}
        <button className="link small" onClick={onClose}>
          Close
        </button>
      </h2>
      <div className="toolbar">
        <select value={adding} onChange={(e) => setAdding(e.target.value)}>
          <option value="">Add someone to {team.name}</option>
          {people.data?.users
            .filter((u) => !onTeam.has(u.id) && !u.deactivated_at)
            .map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} ({u.email})
              </option>
            ))}
        </select>
        <button
          disabled={!adding || setRole.isPending}
          onClick={() => setRole.mutate({ userId: adding, role: "member" }, { onSuccess: () => setAdding("") })}
        >
          Add as member
        </button>
      </div>
      {error && <p className="error">{error.message}</p>}
      {remove.isSuccess && remove.data.released > 0 && (
        <p className="muted small">Released {remove.data.released} open item(s) they owned; each release is in that item's history.</p>
      )}
      <div className="rows">
        {roster.data?.members.map((m) => (
          <div key={m.id} className="row admin-row">
            <span className="row-title">
              <strong>{m.name}</strong> <span className="muted">{m.email}</span>
              {m.deactivated_at && <span className="tag">deactivated</span>}
            </span>
            <select value={m.role} onChange={(e) => setRole.mutate({ userId: m.id, role: e.target.value })} disabled={setRole.isPending}>
              {ROLES.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
            <span />
            <span className="admin-actions">
              <button className="link" disabled={remove.isPending} onClick={() => remove.mutate(m.id)}>
                Remove from team
              </button>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

const KIND = {
  invited: "invited",
  user_changed: "changed a person",
  team_created: "created a team",
  role_set: "set a role",
  member_removed: "removed someone from a team",
};

function AdminLog() {
  const log = useQuery({ queryKey: ["admin", "events"], queryFn: () => api("/admin/events?limit=20") });
  return (
    <section className="bucket">
      <h2>Recent admin changes</h2>
      {log.data?.events.length === 0 && <p className="empty">No admin changes yet.</p>}
      <ol className="events">
        {log.data?.events.map((e) => (
          <li key={e.id} className="event">
            <span className="event-line">
              <strong>{e.actor_name}</strong> {KIND[e.kind] || e.kind}
              {e.target.email ? `: ${e.target.email}` : e.target.name ? `: ${e.target.name}` : ""} <Time iso={e.created_at} />
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
