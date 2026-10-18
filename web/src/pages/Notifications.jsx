import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Avatar, ItemKey, Time, describeEvent } from "../ui";

// "Today", "Yesterday", then the weekday and date, in the reader's own time zone.
function dayLabel(iso) {
  const d = new Date(iso);
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

function byDay(list) {
  const days = [];
  for (const n of list) {
    const label = dayLabel(n.created_at);
    if (days.at(-1)?.label !== label) days.push({ label, items: [] });
    days.at(-1).items.push(n);
  }
  return days;
}

export default function Notifications() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const unreadOnly = params.get("tab") === "unread";
  const q = useQuery({
    queryKey: ["notifications", unreadOnly ? "unread" : "all"],
    queryFn: () => api(`/notifications${unreadOnly ? "?unread=true" : ""}`),
  });
  const unread = useQuery({ queryKey: ["notifications", "unread"], queryFn: () => api("/notifications?unread=true") });
  const done = { onSettled: () => qc.invalidateQueries({ queryKey: ["notifications"] }) };
  const read = useMutation({ mutationFn: (id) => api(`/notifications/${id}/read`, { method: "POST" }), ...done });
  const readAll = useMutation({
    mutationFn: (upTo) => api("/notifications/read", { method: "POST", body: { up_to: upTo } }),
    ...done,
  });

  const unreadList = unread.data?.notifications || [];
  const unreadCount = unread.data?.capped ? "50+" : unreadList.length;

  return (
    <div className="page">
      <header className="page-head tabbed">
        <div>
          <h1>Notifications</h1>
          <p className="muted">Changes to items you reported, own or watch.</p>
        </div>
        {unreadList.length > 0 && (
          <button disabled={readAll.isPending} onClick={() => readAll.mutate(unreadList[0].id)}>
            Mark all as read
          </button>
        )}
      </header>
      <div className="tabs" role="tablist" aria-label="Notifications">
        <button role="tab" className="tab" aria-selected={!unreadOnly} onClick={() => setParams({}, { replace: true })}>
          All
        </button>
        <button
          role="tab"
          className="tab"
          aria-selected={unreadOnly}
          onClick={() => setParams({ tab: "unread" }, { replace: true })}
        >
          Unread
          {unreadList.length > 0 && <span className="tab-count">{unreadCount}</span>}
        </button>
      </div>
      {readAll.isError && <p className="error">{readAll.error.message}</p>}
      {q.isPending && <div className="page-loading">Loading notifications</div>}
      {q.isError && <div className="error">Could not load notifications: {q.error.message}</div>}
      {q.data?.notifications.length === 0 && (
        <div className="empty-state">
          <strong>{unreadOnly ? "You are all caught up." : "Nothing yet."}</strong>
          <span className="muted">
            {unreadOnly ? "Nothing new since you last looked." : "Watch an item to hear when it changes."}
          </span>
        </div>
      )}
      {q.data &&
        byDay(q.data.notifications).map((day) => (
          <section key={day.label} className="day">
            <h2 className="day-head">{day.label}</h2>
            <div className="group open">
              {day.items.map((n) => (
                <Link
                  key={n.id}
                  to={`/items/${n.item_id}`}
                  className={`nrow ${n.read_at ? "" : "unread"}`}
                  onClick={() => n.read_at || read.mutate(n.id)}
                >
                  <i className="dot" aria-hidden="true" />
                  {!n.read_at && <span className="visually-hidden">Unread: </span>}
                  <Avatar name={n.actor_name} size={28} />
                  <span className="nrow-text">
                    <span className="nrow-line">
                      <strong>{n.actor_name}</strong> {describeEvent(n)}
                    </span>
                    <span className="nrow-item">
                      <ItemKey teamKey={n.team_key} number={n.number} />
                      <span className="nrow-title">{n.title}</span>
                    </span>
                    {n.kind === "comment" && n.note && <span className="nrow-note">{n.note}</span>}
                  </span>
                  <Time iso={n.created_at} />
                </Link>
              ))}
            </div>
          </section>
        ))}
      {q.data?.capped && <p className="muted small more-note">Showing the newest 50.</p>}
    </div>
  );
}
