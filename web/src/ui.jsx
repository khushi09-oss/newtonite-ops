// Small shared pieces: status and priority glyphs, people, relative time, and the words for events.
import { Link } from "react-router-dom";

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

// One glyph per status, so state reads from shape as well as colour.
const STATUS_GLYPH = {
  new: <circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" />,
  triaged: <circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2.2 2" />,
  in_progress: (
    <>
      <circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M7 3.5a3.5 3.5 0 0 1 0 7z" fill="currentColor" />
    </>
  ),
  awaiting_approval: (
    <>
      <circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="7" cy="7" r="2" fill="currentColor" />
    </>
  ),
  approved: (
    <>
      <circle cx="7" cy="7" r="6" fill="currentColor" />
      <path d="M4.6 7.2 6.3 8.8 9.4 5.6" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  resolved: (
    <>
      <circle cx="7" cy="7" r="6" fill="currentColor" />
      <path d="M4.6 7.2 6.3 8.8 9.4 5.6" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  closed: (
    <>
      <circle cx="7" cy="7" r="6" fill="currentColor" />
      <path d="M4.8 4.8l4.4 4.4M9.2 4.8 4.8 9.2" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),
};

export function StatusIcon({ value }) {
  return (
    <svg className={`status-icon status-${value}`} width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      {STATUS_GLYPH[value]}
    </svg>
  );
}

export function Status({ value }) {
  return (
    <span className={`status status-${value}`}>
      <StatusIcon value={value} />
      {STATUS_LABEL[value] || value}
    </span>
  );
}

// Signal bars for high to low, a filled square for urgent: the shape carries it, the word confirms it.
export function PriorityIcon({ value }) {
  if (value === 0) {
    return (
      <svg className="priority-icon priority-0" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <rect x="1" y="1" width="12" height="12" rx="3" fill="currentColor" />
        <path d="M7 4v3.6" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
        <circle cx="7" cy="10" r="0.9" fill="#fff" />
      </svg>
    );
  }
  const lit = 4 - value; // high 3, normal 2, low 1
  return (
    <svg className={`priority-icon priority-${value}`} width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      {[0, 1, 2].map((b) => (
        <rect key={b} x={1.5 + b * 4} y={9 - b * 3} width="3" height={4 + b * 3} rx="1" fill="currentColor" opacity={b < lit ? 1 : 0.22} />
      ))}
    </svg>
  );
}

export function Priority({ value, compact }) {
  return (
    <span className={`priority priority-${value}`} title={PRIORITY_LABEL[value]}>
      <PriorityIcon value={value} />
      {compact ? null : <span className="priority-word">{PRIORITY_LABEL[value]}</span>}
    </span>
  );
}

export const itemKey = (teamKey, number) => (teamKey && number ? `${teamKey}-${number}` : "");

export function ItemKey({ teamKey, number }) {
  return <span className="item-key">{itemKey(teamKey, number)}</span>;
}

// A stable, soft tint per person, so the same face is recognisable across screens.
const TINTS = ["#E8EDFF", "#FDEBDD", "#DFF5EC", "#FCF1D5", "#F6E3F0", "#E4F1FB", "#ECE8FB", "#F1F2F4"];
const INKS = ["#3046B8", "#A44A12", "#13774F", "#8A5C00", "#9A3A75", "#1F6596", "#5A44B8", "#4A515C"];

export function Avatar({ name, size = 20 }) {
  const initials = (name || "?")
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2);
  const h = [...(name || "")].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7) % TINTS.length;
  return (
    <span
      className="avatar"
      aria-hidden="true"
      style={{ width: size, height: size, fontSize: size * 0.42, background: TINTS[h], color: INKS[h] }}
    >
      {initials}
    </span>
  );
}

export function Person({ name, muted }) {
  if (!name) return <span className="muted">{muted || "Nobody"}</span>;
  return (
    <span className="person">
      <Avatar name={name} />
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
    if (f === "priority") return PRIORITY_LABEL[v] || `P${v}`;
    if (f === "assignee_id" || f === "approval_requested_by") return whoIs(v, e, item);
    if (v === null || v === undefined) return "nothing";
    return String(v).length > 40 ? `${String(v).slice(0, 40)}...` : String(v);
  };
  return Object.keys(after)
    .filter((f) => f !== "approval_requested_by")
    .map((f) => `${f.replace("_id", "").replace("_at", " date")} ${pretty(f, before[f])} → ${pretty(f, after[f])}`)
    .join(", ");
}

export function DemoBanner() {
  return (
    <div className="demo-banner" role="note">
      This is a public demo. The people and items are made up, everyone shares them, and they reset every
      night at 04:00 Singapore time.
    </div>
  );
}

// A fixed swatch per team, from the chart palette's categorical order, so a team keeps its colour.
const TEAM_SWATCHES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
// ponytail: the eight standing teams get one colour each; any other team hashes and may share one.
// A colour column picked when a team is created is the upgrade.
const SEEDED = { Payments: 0, Platform: 1, Risk: 2, Onboarding: 3, Support: 4, Billing: 5, Compliance: 6, Incidents: 7 };
export function teamSwatch(name) {
  const h = SEEDED[name] ?? [...(name || "")].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 3);
  return TEAM_SWATCHES[h % TEAM_SWATCHES.length];
}

const OPEN_STATUSES = ["new", "triaged", "in_progress", "awaiting_approval", "approved"];

// Only open work has a deadline worth showing. Past due reads in red, in days.
export function DueTag({ item }) {
  if (!item.due_at || !OPEN_STATUSES.includes(item.status)) return <span className="due" />;
  const due = new Date(item.due_at);
  const days = Math.round((due.getTime() - Date.now()) / 86400000);
  const late = due.getTime() < Date.now();
  const text = late
    ? `${Math.max(1, -days)} d overdue`
    : days === 0
      ? "Due today"
      : `Due ${due.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  return (
    <span className={`due ${late ? "late" : ""}`} title={due.toLocaleString()}>
      {text}
    </span>
  );
}

// One line per item, the same on every list. Inside a status group the status is the group, so it hides.
export function ItemRow({ item, showStatus = true }) {
  return (
    <Link to={`/items/${item.id}`} className={`irow ${showStatus ? "" : "no-status"}`}>
      <Priority value={item.priority} compact />
      <ItemKey teamKey={item.team_key} number={item.number} />
      {showStatus && (
        <span title={STATUS_LABEL[item.status]}>
          <StatusIcon value={item.status} />
        </span>
      )}
      <span className="irow-title">{item.title}</span>
      <DueTag item={item} />
      <span className="irow-team">
        <span className="team-swatch" style={{ background: teamSwatch(item.team_name) }} />
        {item.team_name}
      </span>
      <span title={item.assignee_name || "Unassigned"} className="irow-who">
        {item.assignee_name ? <Avatar name={item.assignee_name} /> : <span className="avatar-empty" />}
      </span>
      <Time iso={item.updated_at} />
    </Link>
  );
}
