import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { pollEvery, useLiveConnected } from "../live";
import { Person, Priority, Status, Time } from "../ui";

const BUCKETS = [
  ["assigned", "Assigned to me", "Nothing is assigned to you right now."],
  ["approvals", "Waiting for my approval", "No approvals are waiting on you."],
  ["unassigned_urgent", "Urgent and unclaimed", "Every urgent item on your teams has an owner."],
  ["stale", "Quiet for three days", "Nothing on your teams has gone quiet."],
];

export function ItemRow({ item }) {
  return (
    <Link to={`/items/${item.id}`} className="row">
      <Priority value={item.priority} compact />
      <span className="row-title">{item.title}</span>
      <span className="row-team">{item.team_name}</span>
      <Status value={item.status} />
      <span className="row-person">
        <Person name={item.assignee_name} muted="Unassigned" />
      </span>
      <Time iso={item.updated_at} />
    </Link>
  );
}

export default function Inbox() {
  const live = useLiveConnected();
  const q = useQuery({ queryKey: ["inbox"], queryFn: () => api("/inbox"), refetchInterval: pollEvery(live) });
  if (q.isPending) return <div className="page-loading">Loading your inbox</div>;
  if (q.isError) return <div className="error">Could not load the inbox: {q.error.message}</div>;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Inbox</h1>
        <p className="muted">What needs your attention, in the order it needs it.</p>
      </header>
      {BUCKETS.map(([key, title, empty]) => {
        const bucket = q.data[key];
        return (
          <section key={key} className="bucket">
            <h2>
              {title}
              <span className="count-inline">
                {bucket.capped ? "first 50" : bucket.items.length}
              </span>
            </h2>
            {bucket.items.length === 0 ? (
              <p className="empty">{empty}</p>
            ) : (
              <div className="rows">
                {bucket.items.map((item) => (
                  <ItemRow key={item.id} item={item} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
