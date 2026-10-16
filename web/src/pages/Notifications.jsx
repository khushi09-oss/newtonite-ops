import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Time, describeEvent } from "../ui";

export default function Notifications() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["notifications", "all"], queryFn: () => api("/notifications") });
  const read = useMutation({
    mutationFn: (id) => api(`/notifications/${id}/read`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  if (q.isPending) return <div className="page-loading">Loading notifications</div>;
  if (q.isError) return <div className="error">Could not load notifications: {q.error.message}</div>;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Notifications</h1>
        <p className="muted">Changes to items you reported, own or watch.</p>
      </header>
      {q.data.notifications.length === 0 && <p className="empty">Nothing yet. Watch an item to hear about it.</p>}
      <div className="rows">
        {q.data.notifications.map((n) => (
          <button
            key={n.id}
            className={`row notif ${n.read_at ? "" : "unread"}`}
            onClick={() => {
              if (!n.read_at) read.mutate(n.id);
              navigate(`/items/${n.item_id}`);
            }}
          >
            <i className="dot" aria-hidden="true" />
            <span className="row-title">
              <strong>{n.actor_name}</strong> {describeEvent(n)}
              {n.kind === "comment" && n.note ? <span className="muted">: {n.note}</span> : null}
            </span>
            <span className="row-team">{n.title}</span>
            <Time iso={n.created_at} />
          </button>
        ))}
      </div>
      {q.data.capped && <p className="muted small">Showing the first 50.</p>}
    </div>
  );
}
