import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { api, useRetryKey } from "../api";
import { Icon } from "../icons";
import { Avatar, RosterFilter, TEAM_COLOUR_NAMES, rosterQuery, teamColor } from "../ui";

const ROLES = ["viewer", "member", "lead"];
const ROLE_HINT = {
  viewer: "Follows the work, cannot change it",
  member: "Creates, claims and works items",
  lead: "Approves work and manages the team",
  admin: "You see it as an admin, and change who is on it from the Admin page",
};

// A visitor's own team on the demo. Used by the dashboard's setup and the sidebar.
export function NewTeamForm({ onCreated, autoFocus }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const retry = useRetryKey();
  const create = useMutation({
    mutationFn: () => api("/teams", { method: "POST", key: retry.key(), body: { name: name.trim() } }),
    onSuccess: (team) => {
      retry.done();
      setName("");
      qc.invalidateQueries({ queryKey: ["me"] });
      onCreated?.(team);
    },
  });
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <input
        aria-label="Team name"
        placeholder="Team name, like Payments or Support"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={80}
        autoFocus={autoFocus}
        required
      />
      <button className="primary" disabled={create.isPending || !name.trim()}>
        Create team
      </button>
      {create.isError && <p className="error">{create.error.message}</p>}
    </form>
  );
}

// Nothing is emailed (there is no sending domain), so the lead gets a message to send themselves.
export function InviteNote({ invite }) {
  const [copied, setCopied] = useState(null);
  const text =
    `I've added you to ${invite.team} on Newtonite Ops as a ${invite.role}. ` +
    `Sign in with Google as ${invite.email}: ${window.location.origin}/login`;
  return (
    <div className="invite-note">
      <p className="small">
        Added <strong>{invite.email}</strong>. Nothing is emailed, so send them this yourself:
      </p>
      <textarea readOnly rows={2} value={text} aria-label="Invite message" onFocus={(e) => e.target.select()} />
      <div className="invite-actions">
        <button
          type="button"
          onClick={() =>
            // The clipboard can be refused (an insecure page, a denied permission); the text stays selectable.
            navigator.clipboard.writeText(text).then(
              () => setCopied(true),
              () => setCopied(false),
            )
          }
        >
          {copied ? "Copied" : "Copy invite"}
        </button>
        {copied === false && <span className="muted small">Could not copy. Select the text above instead.</span>}
      </div>
    </div>
  );
}

// Add someone by email, or change the role of someone already on the team.
export function AddMemberForm({ team, demo, onAdded }) {
  const qc = useQueryClient();
  const blank = { email: "", name: "", role: "member" };
  const [form, setForm] = useState(blank);
  const add = useMutation({
    mutationFn: () => api(`/teams/${team.id}/members`, { method: "PUT", body: { ...form, name: form.name.trim() || null } }),
    onSuccess: (added) => {
      setForm(blank);
      onAdded?.({ ...added, team: team.name });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["members", team.id] }),
  });
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  return (
    <form
      className="add-member"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <input type="email" required aria-label="Email" placeholder="Their Google email" value={form.email} onChange={set("email")} />
      <input aria-label="Name" placeholder="Name (optional)" value={form.name} onChange={set("name")} maxLength={120} />
      <select aria-label="Role" value={form.role} onChange={set("role")}>
        {ROLES.map((r) => (
          <option key={r}>{r}</option>
        ))}
      </select>
      <button className="primary" disabled={add.isPending || !form.email}>
        Add
      </button>
      {add.isSuccess && !onAdded ? (
        <InviteNote invite={{ ...add.data, team: team.name }} />
      ) : (
        <p className={`small ${add.isError ? "error" : "muted"}`}>
          {add.isError
            ? add.error.message
            : demo
              ? "After adding them, you get an invite message to send them yourself."
              : "They need an account already. An admin invites anyone new."}
        </p>
      )}
    </form>
  );
}

// The eight team colours as a radio group. Every screen draws the stored colour, so all of them refetch.
function ColourPicker({ team }) {
  const qc = useQueryClient();
  const pick = useMutation({
    mutationFn: (color) => api(`/teams/${team.id}`, { method: "PATCH", body: { color } }),
    onSettled: () => qc.invalidateQueries(),
  });
  return (
    <div className="colour-pick">
      <span className="muted small">Colour</span>
      <div role="radiogroup" aria-label="Team colour">
        {TEAM_COLOUR_NAMES.map((name, slot) => (
          <button
            key={slot}
            type="button"
            role="radio"
            aria-checked={team.color === slot}
            aria-label={name}
            title={name}
            disabled={pick.isPending}
            style={{ background: teamColor(slot) }}
            onClick={() => team.color !== slot && pick.mutate(slot)}
          />
        ))}
      </div>
      {pick.isError && <span className="error small">{pick.error.message}</span>}
    </div>
  );
}

export default function Team() {
  const me = useOutletContext();
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const team = me.visible_teams.find((t) => t.id === id);
  const [find, setFind] = useState("");
  useEffect(() => setFind(""), [id]); // a search on one team does not carry over to the next
  const members = useQuery({
    queryKey: ["members", id, find],
    queryFn: () => api(rosterQuery(`/teams/${id}/members`, find)),
    enabled: Boolean(team),
    placeholderData: keepPreviousData,
  });
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api("/auth/config"), staleTime: Infinity });
  const done = { onSettled: () => qc.invalidateQueries({ queryKey: ["members", id] }) };
  const setRole = useMutation({ mutationFn: ({ email, role }) => api(`/teams/${id}/members`, { method: "PUT", body: { email, role } }), ...done });
  const remove = useMutation({ mutationFn: (userId) => api(`/teams/${id}/members/${userId}`, { method: "DELETE" }), ...done });

  if (!team) {
    return (
      <div className="page">
        <div className="empty-state">
          <strong>Team not found.</strong>
          <span className="muted">You are not on this team, or it no longer exists.</span>
        </div>
      </div>
    );
  }
  const isLead = team.role === "lead";
  // The API's rule (teams.lead_of): a lead manages their team, except the demo's shared teams,
  // and an admin may also pick the colour of any public team.
  const canManage = isLead && (team.private || config.data?.demo === false);
  const canColour = canManage || (me.is_admin && !team.private);
  const error = setRole.error || remove.error;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="team-title">
            <span className="team-swatch" style={{ background: teamColor(team.color) }} />
            {team.name}
            {team.key && <span className="item-key">{team.key}</span>}
          </h1>
          <p className="muted">
            {team.role === "admin" ? "You are not on this team." : `You are its ${team.role}.`} {ROLE_HINT[team.role]}.
          </p>
          {canColour && <ColourPicker team={team} />}
        </div>
        <button onClick={() => navigate(`/items?team=${id}`)}>
          <Icon name="list" /> Open items
        </button>
      </header>

      {canManage && (
        <section className="card team-add">
          <div className="card-head">
            <h2>Add someone</h2>
            <p>Leads approve work, members do it, viewers follow it.</p>
          </div>
          <div className="card-body">
            <AddMemberForm team={team} demo={config.data?.demo} />
          </div>
        </section>
      )}

      <h2 className="section-title">
        Members{" "}
        <span className="count-inline">
          {members.data ? `${members.data.members.length}${members.data.capped ? "+" : ""}` : ""}
        </span>
      </h2>
      <RosterFilter data={members.data} value={find} onChange={setFind} />
      {error && <p className="error">{error.message}</p>}
      {remove.isSuccess && remove.data.released > 0 && (
        <p className="muted small">Released {remove.data.released} open item(s) they owned; each release is in the item's history.</p>
      )}
      <div className="table">
        {members.isPending && <div className="group-note">Loading</div>}
        {members.data?.members.map((m) => (
          <div key={m.id} className="trow roster-row">
            <span className="tperson">
              <Avatar name={m.name} size={28} />
              <span className="tperson-text">
                <span>
                  <strong>{m.name}</strong>
                  {m.id === me.id && <span className="muted"> (you)</span>}
                </span>
                <span className="muted small">{m.email}</span>
              </span>
            </span>
            {canManage && m.id !== me.id ? (
              <select
                aria-label={`Role for ${m.name}`}
                value={m.role}
                disabled={setRole.isPending}
                onChange={(e) => setRole.mutate({ email: m.email, role: e.target.value })}
              >
                {ROLES.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            ) : (
              <span className="muted small">{m.role}</span>
            )}
            {canManage && m.id !== me.id ? (
              <button className="ghost small-btn" disabled={remove.isPending} onClick={() => remove.mutate(m.id)}>
                Remove
              </button>
            ) : (
              <span />
            )}
          </div>
        ))}
      </div>
      <p className="muted small more-note">
        <Link to={`/items?team=${id}`}>See this team's items</Link>
      </p>
    </div>
  );
}
