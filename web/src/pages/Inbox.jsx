import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { pollEvery, useLiveConnected } from "../live";
import { ItemRow } from "../ui";

const TABS = [
  ["assigned", "Assigned to me", "Open items you own, most urgent first.", "Nothing is assigned to you right now."],
  ["approvals", "Needs my approval", "Requests from others on teams where you approve: the ones you lead, or every team for an admin.", "No approvals are waiting on you."],
  ["unassigned_urgent", "Urgent, unclaimed", "Urgent and high priority items on your teams that nobody owns yet.", "Every urgent item on your teams has an owner."],
  ["stale", "Gone quiet", "Open items on your teams with no change in three days, quietest first.", "Nothing on your teams has gone quiet."],
];

export default function Inbox() {
  const live = useLiveConnected();
  const [params, setParams] = useSearchParams();
  const q = useQuery({ queryKey: ["inbox"], queryFn: () => api("/inbox"), refetchInterval: pollEvery(live) });
  if (q.isPending) return <div className="page-loading">Loading your inbox</div>;
  if (q.isError) return <div className="error">Could not load the inbox: {q.error.message}</div>;

  // With no tab chosen, open on the first one that has work in it.
  const tab = TABS.find(([key]) => key === params.get("tab")) || TABS.find(([key]) => q.data[key].items.length) || TABS[0];
  const [key, , hint, empty] = tab;
  const bucket = q.data[key];
  const count = (b) => (b.capped ? `${b.items.length}+` : b.items.length);

  return (
    <div className="page">
      <header className="page-head tabbed">
        <div>
          <h1>Inbox</h1>
          <p className="muted">What needs your attention, in the order it needs it.</p>
        </div>
      </header>
      <div className="tabs" role="tablist" aria-label="Inbox">
        {TABS.map(([k, label]) => (
          <button
            key={k}
            role="tab"
            aria-selected={k === key}
            className="tab"
            onClick={() => setParams({ tab: k }, { replace: true })}
          >
            {label}
            <span className="tab-count">{count(q.data[k])}</span>
          </button>
        ))}
      </div>
      <p className="tab-hint muted">{hint}</p>
      {bucket.items.length === 0 ? (
        <div className="empty-state">
          <strong>All clear.</strong>
          <span className="muted">{empty}</span>
        </div>
      ) : (
        <div className="group open">
          <div className="group-body" role="tabpanel">
            {bucket.items.map((item) => (
              <ItemRow key={item.id} item={item} />
            ))}
          </div>
        </div>
      )}
      {bucket.capped && <p className="muted small more-note">Showing the first {bucket.items.length}.</p>}
    </div>
  );
}
