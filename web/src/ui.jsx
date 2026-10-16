// Small shared pieces: status and priority glyphs, people, relative time, and the words for events.

export const STATUS_LABEL = {
  new: "New",
  triaged: "Triaged",
  in_progress: "In progress",
  awaiting_approval: "Awaiting approval",
  approved: "Approved",
  resolved: "Resolved",
  closed: "Closed",
};

export const KIND_LABEL = {
  incident: "Incident",
  customer_issue: "Customer issue",
  payment: "Payment",
  compliance: "Compliance",
  task: "Task",
};

export const PRIORITY_LABEL = ["Urgent", "High", "Normal", "Low"];

// What the button says for each move. "to" is the target status.
export const TRANSITION_LABEL = {
  triaged: (from) => (from === "closed" ? "Reopen" : from === "awaiting_approval" ? "Reject" : "Triage"),
  in_progress: () => "Start",
  awaiting_approval: () => "Request approval",
  approved: () => "Approve",
  resolved: () => "Resolve",
  closed: () => "Close",
};

export function Status({ value }) {
  return (
    <span className={`status status-${value}`}>
      <i aria-hidden="true" />
      {STATUS_LABEL[value] || value}
    </span>
  );
}

export function Priority({ value, compact }) {
  return (
    <span className={`priority priority-${value}`} title={PRIORITY_LABEL[value]}>
      P{value}
      {compact ? null : <span className="priority-word">{PRIORITY_LABEL[value]}</span>}
    </span>
  );
}

export function Person({ name, muted }) {
  if (!name) return <span className="muted">{muted || "Nobody"}</span>;
  const initials = name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2);
  return (
    <span className="person">
      <span className="avatar" aria-hidden="true">
        {initials}
      </span>
      {name}
    </span>
  );
}

export function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  const m = s / 60;
  if (m < 60) return `${Math.round(m)} min ago`;
  const h = m / 60;
  if (h < 24) return `${Math.round(h)} h ago`;
  const d = h / 24;
  if (d < 30) return `${Math.round(d)} d ago`;
  return new Date(iso).toLocaleDateString();
}

export function Time({ iso }) {
  return (
    <time dateTime={iso} title={iso ? new Date(iso).toLocaleString() : ""}>
      {timeAgo(iso)}
    </time>
  );
}

// Names for ids the events carry. The claimer is the actor; any other assignee is read off the item.
function whoIs(id, e, item) {
  if (!id) return "nobody";
  if (e && id === e.actor_id) return e.actor_name;
  if (item && id === item.assignee_id && item.assignee_name) return item.assignee_name;
  if (item && id === item.reporter_id && item.reporter_name) return item.reporter_name;
  return "someone else";
}

// One sentence per history event, written from the actor's side.
export function describeEvent(e, item) {
  const before = e.before || {};
  const after = e.after || {};
  switch (e.kind) {
    case "created":
      return "created this item";
    case "claimed":
      return "claimed it and started work";
    case "released":
      return "released it";
    case "assigned":
      return `reassigned it to ${whoIs(after.assignee_id, e, item)}`;
    case "comment":
      return "commented";
    case "status":
      return `moved it from ${STATUS_LABEL[before.status] || before.status} to ${STATUS_LABEL[after.status] || after.status}`;
    case "edited": {
      const fields = Object.keys(after).map((f) => f.replace("_at", " date"));
      return `changed the ${fields.join(", ")}`;
    }
    default:
      return e.kind;
  }
}

// The one line the conflict strip shows: what changed, from what, to what.
export function describeChange(e, item) {
  const before = e.before || {};
  const after = e.after || {};
  if (e.kind === "comment") return `added a comment: "${(e.note || "").slice(0, 80)}"`;
  if (e.kind === "created") return "created the item";
  const pretty = (f, v) => {
    if (f === "status") return STATUS_LABEL[v] || v;
    if (f === "priority") return `P${v}`;
    if (f === "assignee_id" || f === "approval_requested_by") return whoIs(v, e, item);
    if (v === null || v === undefined) return "nothing";
    return String(v).length > 40 ? `${String(v).slice(0, 40)}...` : String(v);
  };
  return Object.keys(after)
    .filter((f) => f !== "approval_requested_by")
    .map((f) => `${f.replace("_id", "").replace("_at", " date")} ${pretty(f, before[f])} → ${pretty(f, after[f])}`)
    .join(", ");
}
