import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { api, newKey } from "../api";
import { Icon } from "../icons";
import { pollEvery, useLiveConnected } from "../live";
import {
  Avatar,
  KIND_LABEL,
  PRIORITY_LABEL,
  Person,
  Priority,
  STATUS_LABEL,
  Status,
  StatusIcon,
  TRANSITION_LABEL,
  Time,
  describeChange,
  describeEvent,
  itemKey,
  teamColor,
} from "../ui";

const NOTE_REQUIRED = { "awaiting_approval>triaged": true };
const ORDER = Object.keys(STATUS_LABEL);
const FINISHED = ["resolved", "closed"];

export default function Item() {
  const { id } = useParams();
  const me = useOutletContext();
  const qc = useQueryClient();
  const key = ["item", id];
  const live = useLiveConnected();
  const item = useQuery({ queryKey: key, queryFn: () => api(`/items/${id}`), refetchInterval: pollEvery(live) });

  const [conflict, setConflict] = useState(null); // {current, events, vars}
  const [problem, setProblem] = useState(null);
  const [older, setOlder] = useState([]); // events paged in below the first 20
  const [noteFor, setNoteFor] = useState(null); // transition target that needs a note
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [comment, setComment] = useState("");

  // Every mutation goes through here: optimistic apply, roll back on any error, 409 becomes the strip.
  const act = useMutation({
    mutationFn: (vars) => api(vars.path, { method: vars.method || "POST", body: vars.body, key: vars.key }),
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData(key);
      if (vars.optimistic && prev) {
        qc.setQueryData(key, { ...prev, ...vars.optimistic(prev), version: prev.version + 1 });
      }
      setProblem(null);
      return { prev };
    },
    onError: (err, vars, ctx) => {
      if (ctx && ctx.prev) qc.setQueryData(key, ctx.prev);
      if (err.status === 409 && err.body) setConflict({ current: err.body.current, events: err.body.events, vars });
      else setProblem(err.message);
    },
    onSuccess: (data, vars) => {
      if (data) qc.setQueryData(key, data);
      setConflict(null);
      if (vars.after) vars.after();
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  if (item.isPending) return <div className="page-loading">Loading item</div>;
  if (item.isError) {
    return (
      <div className="page">
        <div className="empty-state">
          <strong>{item.error.status === 404 ? "There is no item here you can see." : "Could not load this item."}</strong>
          <span className="muted">{item.error.status === 404 ? "It may be on a team you are not part of." : item.error.message}</span>
          <Link to="/items">Back to items</Link>
        </div>
      </div>
    );
  }
  const it = item.data;
  const can = (a) => it.allowed_actions.includes(a);
  const versioned = (path, body, optimistic, after) =>
    act.mutate({ path: `/items/${id}${path}`, body: { version: it.version, ...body }, key: newKey(), optimistic, after });

  function transition(to) {
    const needsNote = NOTE_REQUIRED[`${it.status}>${to}`];
    if (needsNote && noteFor !== to) {
      setNoteFor(to);
      return;
    }
    if (needsNote && !note.trim()) return;
    versioned(
      "/transition",
      { to, note: needsNote ? note.trim() : undefined },
      () => ({ status: to, approval_requested_by: to === "awaiting_approval" ? me.id : null }),
      () => {
        setNoteFor(null);
        setNote("");
      },
    );
  }

  function retry() {
    const { vars, current } = conflict;
    act.mutate({ ...vars, body: vars.body && "version" in vars.body ? { ...vars.body, version: current.version } : vars.body });
  }

  function reload() {
    setConflict(null);
    qc.invalidateQueries({ queryKey: key });
  }

  const transitions = it.allowed_actions.filter((a) => a.startsWith("transition:")).map((a) => a.slice(11));
  const events = [...it.events, ...older];
  const last = conflict && conflict.events && conflict.events[0];
  // The step that moves the work on gets the primary button: a claim that starts the work, else the
  // next status along, else a claim that only takes ownership. The server says which a claim is.
  const forward = transitions.find((to) => ORDER.indexOf(to) > ORDER.indexOf(it.status));
  const primary = can("claim") && it.claim_to ? "claim" : forward || (can("claim") ? "claim" : undefined);
  const others = transitions.filter((to) => to !== primary);
  // A claim that only takes ownership, while a move has the primary button, sits with the other moves.
  const claimAside = can("claim") && primary !== "claim";
  const overdue = it.due_at && new Date(it.due_at) < new Date() && !FINISHED.includes(it.status);
  const busy = act.isPending;
  const claim = () =>
    versioned("/claim", {}, () => ({ assignee_id: me.id, assignee_name: me.name, ...(it.claim_to && { status: it.claim_to }) }));
  const tools = can("assign") || can("release") || (can("edit") && !editing);

  return (
    <div className="page item-page">
      <div className="item-top">
        <nav className="crumbs" aria-label="Breadcrumb">
          <Link to="/items">Items</Link>
          <Icon name="chevron" className="crumb-sep" />
          <Link to={`/items?team=${it.team_id}`} className="crumb-team">
            <span className="team-swatch" style={{ background: teamColor(it.team_color) }} />
            {it.team_name}
          </Link>
          <Icon name="chevron" className="crumb-sep" />
          <span className="crumb-key">{itemKey(it.team_key, it.number)}</span>
        </nav>
        {can("watch") && (
          <button
            className={`chip ${it.watching ? "chip-on" : ""}`}
            aria-pressed={it.watching}
            onClick={() =>
              act.mutate({
                path: `/items/${id}/watch`,
                method: it.watching ? "DELETE" : "POST",
                optimistic: (old) => ({ watching: !old.watching }),
              })
            }
            disabled={busy}
          >
            <Icon name="bell" /> {it.watching ? "Watching" : "Watch"}
          </button>
        )}
      </div>

      {conflict && (
        <div className="conflict" role="alert">
          <span>
            {last ? (
              <>
                Changed by <strong>{last.actor_name}</strong> <Time iso={last.created_at} />: {describeChange(last, conflict.current)}.
              </>
            ) : (
              "Someone changed this item while you were looking at it."
            )}{" "}
            Your change was not saved.
          </span>
          <span className="conflict-actions">
            <button className="link" onClick={reload}>
              Reload
            </button>
            <button className="link" onClick={retry} disabled={busy}>
              Retry on the new version
            </button>
          </span>
        </div>
      )}
      {problem && (
        <div className="problem" role="alert">
          {problem}
          <button className="link" onClick={() => setProblem(null)}>
            Dismiss
          </button>
        </div>
      )}

      <div className="item-grid">
        <article className="item-main">
          {editing ? (
            <EditForm
              it={it}
              canPriority={can("priority")}
              onCancel={() => setEditing(false)}
              onSave={(changes) =>
                act.mutate({
                  path: `/items/${id}`,
                  method: "PATCH",
                  body: { version: it.version, ...changes },
                  optimistic: () => changes,
                  after: () => setEditing(false),
                })
              }
              busy={busy}
            />
          ) : (
            <header className="item-head">
              <div className="item-tags">
                <span className="tag">{KIND_LABEL[it.kind]}</span>
                {it.priority <= 1 && <Priority value={it.priority} />}
                {overdue && <span className="due late">Overdue</span>}
              </div>
              <h1>{it.title}</h1>
              <p className="item-byline muted">
                Opened by {it.reporter_name} <Time iso={it.created_at} />
              </p>
              {it.description ? <p className="description">{it.description}</p> : <p className="muted">No description.</p>}
            </header>
          )}

          <section className="timeline" aria-labelledby="activity-title">
            <div className="timeline-head">
              <h2 id="activity-title">Activity</h2>
              <span className="muted small">{it.version.toLocaleString()} changes</span>
            </div>
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                const body = comment.trim();
                if (!body) return;
                act.mutate({
                  path: `/items/${id}/comments`,
                  body: { body },
                  key: newKey(),
                  optimistic: (old) => ({
                    events: [
                      { id: "pending", seq: old.version + 1, kind: "comment", note: body, actor_name: me.name, created_at: new Date().toISOString() },
                      ...old.events,
                    ],
                  }),
                  after: () => setComment(""),
                });
              }}
            >
              <Avatar name={me.name} size={28} />
              <div className="composer-box">
                <textarea
                  aria-label="Comment"
                  placeholder={can("comment") ? "Leave a comment" : "Viewers cannot comment on this item"}
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  disabled={!can("comment")}
                  rows={2}
                />
                <div className="composer-foot">
                  <button className="primary small-btn" disabled={!comment.trim() || busy}>
                    Comment
                  </button>
                </div>
              </div>
            </form>
            <ol className="events">
              {events.map((e) =>
                e.kind === "comment" ? (
                  <li key={e.id} className="event event-comment">
                    <Avatar name={e.actor_name} size={28} />
                    <div className="comment-card">
                      <div className="event-line">
                        <strong>{e.actor_name}</strong> <Time iso={e.created_at} />
                      </div>
                      <p className="event-note">{e.note}</p>
                    </div>
                  </li>
                ) : (
                  <li key={e.id} className="event">
                    <span className="event-mark">
                      {e.kind === "status" && e.after ? <StatusIcon value={e.after.status} /> : <span className="event-dot" />}
                    </span>
                    <div>
                      <div className="event-line">
                        <strong>{e.actor_name}</strong> {describeEvent(e, it)} <Time iso={e.created_at} />
                      </div>
                      {e.note && <p className="event-note quiet">{e.note}</p>}
                    </div>
                  </li>
                ),
              )}
            </ol>
            {events.length >= 20 && events.length < it.version && (
              <button
                className="link older"
                onClick={async () => {
                  const min = Math.min(...events.map((e) => e.seq));
                  const page = await api(`/items/${id}/events?before_seq=${min}&limit=50`);
                  setOlder((o) => [...o, ...page.events]);
                }}
              >
                Show older activity
              </button>
            )}
          </section>
        </article>

        <aside className="item-side">
          <section className="side-card">
            <h2>Next step</h2>
            <p className="muted small">{nextHint(it, Boolean(primary || others.length || claimAside), primary)}</p>
            {primary && (
              <button className="primary wide" onClick={() => (primary === "claim" ? claim() : transition(primary))} disabled={busy}>
                {primary === "claim" ? "Claim" : TRANSITION_LABEL[primary](it.status)}
              </button>
            )}
            {(others.length > 0 || claimAside) && (
              <div className="actions">
                {others.map((to) => (
                  <button key={to} onClick={() => transition(to)} disabled={busy}>
                    {TRANSITION_LABEL[to](it.status)}
                  </button>
                ))}
                {claimAside && (
                  <button onClick={claim} disabled={busy}>
                    Claim
                  </button>
                )}
              </div>
            )}
            {noteFor && (
              <form
                className="note-box"
                onSubmit={(e) => {
                  e.preventDefault();
                  transition(noteFor);
                }}
              >
                <textarea autoFocus aria-label="Note" placeholder="Why it is going back" value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
                <div className="note-actions">
                  <button className="primary small-btn" disabled={!note.trim() || busy}>
                    {TRANSITION_LABEL[noteFor](it.status)} with note
                  </button>
                  <button type="button" className="link" onClick={() => setNoteFor(null)}>
                    Cancel
                  </button>
                </div>
              </form>
            )}
            {tools && (
              <div className="side-tools">
                {can("assign") && (
                  <button className="ghost small-btn" onClick={() => setAssigning((v) => !v)} disabled={busy}>
                    <Icon name="users" /> Reassign
                  </button>
                )}
                {can("release") && (
                  <button
                    className="ghost small-btn"
                    onClick={() => versioned("/release", {}, () => ({ assignee_id: null, assignee_name: null }))}
                    disabled={busy}
                  >
                    Release
                  </button>
                )}
                {can("edit") && !editing && (
                  <button className="ghost small-btn" onClick={() => setEditing(true)}>
                    Edit
                  </button>
                )}
              </div>
            )}
            {assigning && (
              <AssignPicker
                teamId={it.team_id}
                onPick={(u) => {
                  setAssigning(false);
                  versioned("/assign", { assignee_id: u.id }, () => ({ assignee_id: u.id, assignee_name: u.name }));
                }}
                onCancel={() => setAssigning(false)}
              />
            )}
          </section>

          <section className="side-card">
            <h2>Details</h2>
            <dl className="props">
              <dt>Status</dt>
              <dd>
                <Status value={it.status} />
              </dd>
              <dt>Priority</dt>
              <dd>
                <Priority value={it.priority} />
              </dd>
              <dt>Assignee</dt>
              <dd>
                <Person name={it.assignee_name} muted="Unassigned" />
              </dd>
              <dt>Reporter</dt>
              <dd>
                <Person name={it.reporter_name} />
              </dd>
              <dt>Team</dt>
              <dd className="crumb-team">
                <span className="team-swatch" style={{ background: teamColor(it.team_color) }} />
                {it.team_name}
              </dd>
              <dt>Due</dt>
              <dd className={overdue ? "late-text" : ""}>
                {it.due_at ? (
                  new Date(it.due_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
                ) : (
                  <span className="muted">None</span>
                )}
              </dd>
              <dt>Created</dt>
              <dd>
                <Time iso={it.created_at} />
              </dd>
              <dt>Updated</dt>
              <dd>
                <Time iso={it.updated_at} />
              </dd>
              <dt>Version</dt>
              <dd className="muted">{it.version}</dd>
            </dl>
          </section>
        </aside>
      </div>
    </div>
  );
}

// One sentence on where the item stands, so the buttons under it make sense.
function nextHint(it, canAct, primary) {
  if (!canAct) {
    return FINISHED.includes(it.status) ? "This work is finished." : "You can follow this item but not change it.";
  }
  switch (it.status) {
    case "new":
      return "Nobody has looked at this yet. Claim it to start, or triage it for the team.";
    case "triaged":
      return "Ready for someone to pick up.";
    case "in_progress":
      return it.assignee_name ? `${it.assignee_name} is working on it.` : "Work has started.";
    case "awaiting_approval":
      return primary === "approved"
        ? "Waiting for your approval. Approve it, or reject it with a note."
        : "Waiting for a team lead to approve it or send it back.";
    case "approved":
      return "Approved. Resolve it once the work is done.";
    case "resolved":
      return "Resolved. Close it once the requester confirms.";
    default:
      return "Closed. Reopen it if the problem comes back.";
  }
}

function EditForm({ it, canPriority, onSave, onCancel, busy }) {
  const [form, setForm] = useState({
    title: it.title,
    description: it.description,
    priority: it.priority,
    due_at: it.due_at ? it.due_at.slice(0, 10) : "",
  });
  const set = (k) => (e) => setForm({ ...form, [k]: k === "priority" ? Number(e.target.value) : e.target.value });
  return (
    <form
      className="edit-form"
      onSubmit={(e) => {
        e.preventDefault();
        const changes = {};
        if (form.title !== it.title) changes.title = form.title.trim();
        if (form.description !== it.description) changes.description = form.description;
        if (canPriority && form.priority !== it.priority) changes.priority = form.priority;
        const due = form.due_at ? new Date(form.due_at).toISOString() : null;
        if (due !== it.due_at) changes.due_at = due;
        if (Object.keys(changes).length) onSave(changes);
        else onCancel();
      }}
    >
      <label className="field">
        Title
        <input value={form.title} onChange={set("title")} required maxLength={200} />
      </label>
      <label className="field">
        Description
        <textarea value={form.description} onChange={set("description")} rows={4} />
      </label>
      <div className="new-item-row">
        {canPriority && (
          <label className="field">
            Priority
            <select value={form.priority} onChange={set("priority")}>
              {PRIORITY_LABEL.map((l, i) => (
                <option key={i} value={i}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          Due date
          <input type="date" value={form.due_at} onChange={set("due_at")} />
        </label>
        <span className="grow" />
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button className="primary" disabled={busy}>
          Save changes
        </button>
      </div>
    </form>
  );
}

function AssignPicker({ teamId, onPick, onCancel }) {
  const members = useQuery({ queryKey: ["members", teamId], queryFn: () => api(`/teams/${teamId}/members`) });
  return (
    <div className="assign">
      {members.isPending && <span className="muted">Loading team</span>}
      {members.isError && <span className="error">{members.error.message}</span>}
      {members.data &&
        members.data.members
          .filter((m) => m.role !== "viewer")
          .map((m) => (
            <button key={m.id} className="assign-option" onClick={() => onPick(m)}>
              <Person name={m.name} /> <span className="role">{m.role}</span>
            </button>
          ))}
      <button className="link" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
