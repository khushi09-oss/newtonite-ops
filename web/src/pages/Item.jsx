import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { api, newKey } from "../api";
import {
  KIND_LABEL,
  PRIORITY_LABEL,
  Person,
  Priority,
  Status,
  TRANSITION_LABEL,
  Time,
  describeChange,
  describeEvent,
} from "../ui";

const NOTE_REQUIRED = { "awaiting_approval>triaged": true };

export default function Item() {
  const { id } = useParams();
  const me = useOutletContext();
  const qc = useQueryClient();
  const key = ["item", id];
  const item = useQuery({ queryKey: key, queryFn: () => api(`/items/${id}`), refetchInterval: 15000 });

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
    },
  });

  if (item.isPending) return <div className="page-loading">Loading item</div>;
  if (item.isError) {
    return (
      <div className="page">
        <p className="error">
          {item.error.status === 404 ? "There is no item here you can see." : item.error.message}
        </p>
        <Link to="/items">Back to items</Link>
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

  return (
    <div className="page item-page">
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
            <button className="link" onClick={retry} disabled={act.isPending}>
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
          <div className="crumbs">
            <Link to="/items">Items</Link> / {it.team_name} / {KIND_LABEL[it.kind]}
          </div>
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
              busy={act.isPending}
            />
          ) : (
            <>
              <h1>{it.title}</h1>
              {it.description ? <p className="description">{it.description}</p> : <p className="muted">No description.</p>}
            </>
          )}

          <section className="timeline">
            <h2>Activity</h2>
            <form
              className="comment-box"
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
              <textarea
                placeholder={can("comment") ? "Add a comment" : "You cannot comment on this item"}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                disabled={!can("comment")}
                rows={2}
              />
              <button className="primary small-btn" disabled={!comment.trim() || act.isPending}>
                Comment
              </button>
            </form>
            <ol className="events">
              {events.map((e) => (
                <li key={e.id} className={`event event-${e.kind}`}>
                  <span className="event-line">
                    <strong>{e.actor_name}</strong> {describeEvent(e, it)} <Time iso={e.created_at} />
                  </span>
                  {e.note && <p className="event-note">{e.note}</p>}
                </li>
              ))}
            </ol>
            {events.length >= 20 && events.length < it.version && (
              <button
                className="link"
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
          <div className="actions">
            {can("claim") && (
              <button
                className="primary"
                onClick={() => versioned("/claim", {}, () => ({ assignee_id: me.id, assignee_name: me.name, status: "in_progress" }))}
                disabled={act.isPending}
              >
                Claim
              </button>
            )}
            {transitions.map((to) => (
              <button key={to} onClick={() => transition(to)} disabled={act.isPending}>
                {TRANSITION_LABEL[to](it.status)}
              </button>
            ))}
            {can("release") && (
              <button onClick={() => versioned("/release", {}, () => ({ assignee_id: null, assignee_name: null }))} disabled={act.isPending}>
                Release
              </button>
            )}
            {can("assign") && (
              <button onClick={() => setAssigning((v) => !v)} disabled={act.isPending}>
                Reassign
              </button>
            )}
            {can("edit") && !editing && <button onClick={() => setEditing(true)}>Edit</button>}
            {can("watch") && (
              <button
                onClick={() =>
                  act.mutate({
                    path: `/items/${id}/watch`,
                    method: it.watching ? "DELETE" : "POST",
                    optimistic: (old) => ({ watching: !old.watching }),
                  })
                }
                disabled={act.isPending}
              >
                {it.watching ? "Stop watching" : "Watch"}
              </button>
            )}
          </div>
          {noteFor && (
            <form
              className="note-box"
              onSubmit={(e) => {
                e.preventDefault();
                transition(noteFor);
              }}
            >
              <textarea autoFocus placeholder="Why it is going back" value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
              <div className="note-actions">
                <button className="primary small-btn" disabled={!note.trim() || act.isPending}>
                  {TRANSITION_LABEL[noteFor](it.status)} with note
                </button>
                <button type="button" className="link" onClick={() => setNoteFor(null)}>
                  Cancel
                </button>
              </div>
            </form>
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
            <dd>{it.team_name}</dd>
            <dt>Due</dt>
            <dd>{it.due_at ? new Date(it.due_at).toLocaleDateString() : <span className="muted">None</span>}</dd>
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
        </aside>
      </div>
    </div>
  );
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
      <input value={form.title} onChange={set("title")} required maxLength={200} />
      <textarea value={form.description} onChange={set("description")} rows={4} />
      <div className="new-item-row">
        {canPriority && (
          <select value={form.priority} onChange={set("priority")}>
            {PRIORITY_LABEL.map((l, i) => (
              <option key={i} value={i}>
                P{i} {l}
              </option>
            ))}
          </select>
        )}
        <input type="date" value={form.due_at} onChange={set("due_at")} />
        <button className="primary" disabled={busy}>
          Save changes
        </button>
        <button type="button" className="link" onClick={onCancel}>
          Cancel
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
            <button key={m.id} className="chip" onClick={() => onPick(m)}>
              {m.name} <span className="role">{m.role}</span>
            </button>
          ))}
      <button className="link" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
