import React, { useCallback } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrowserRouter, Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useMatch, useNavigate } from "react-router-dom";
import { api } from "./api";
import { Avatar, BrandMark, DemoBanner, teamColor } from "./ui";
import { Icon } from "./icons";
import { LiveUpdates, pollEvery, useLiveConnected } from "./live";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import Items from "./pages/Items";
import Item from "./pages/Item";
import Notifications from "./pages/Notifications";
import Admin from "./pages/Admin";
import Team from "./pages/Team";
import Privacy from "./pages/Privacy";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 5000, refetchOnWindowFocus: true } },
});

function Shell() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const itemMatch = useMatch("/items/:id");
  const onEnded = useCallback(
    (reason) => qc.invalidateQueries({ queryKey: reason === "session" ? ["me"] : ["item"] }),
    [qc],
  );
  const me = useQuery({ queryKey: ["me"], queryFn: () => api("/me") });
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api("/auth/config"), staleTime: Infinity });

  if (me.isPending) return <div className="page-loading">Loading</div>;
  if (me.isError) return <Navigate to="/login" replace />;

  return (
    <LiveUpdates itemId={itemMatch?.params.id} onEnded={onEnded}>
      <ShellBody me={me.data} demo={config.data?.demo} qc={qc} navigate={navigate} />
    </LiveUpdates>
  );
}

function ShellBody({ me, demo, qc, navigate }) {
  const live = useLiveConnected();
  const location = useLocation();
  const unread = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => api("/notifications?unread=true"),
    refetchInterval: pollEvery(live),
  });
  // The same query the dashboard reads, so the sidebar's count and the home page always agree.
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api("/dashboard"), refetchInterval: pollEvery(live) });

  const unreadCount = unread.data ? unread.data.notifications.length : 0;
  const inboxCount = dash.data ? dash.data.counts.assigned + dash.data.counts.approvals : 0;
  // Every team list comes from the server's visible_teams: what you can see and where you may create.
  const canCreate = me.visible_teams.some((t) => t.can_create);
  const otherTeams = me.visible_teams.filter((t) => t.role === "admin");
  const signOut = async () => {
    // The cookie is HttpOnly, so only the server can end the session.
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    qc.clear();
    navigate("/login");
  };
  const role = me.is_admin ? "Admin" : me.teams.some((t) => t.role === "lead") ? "Team lead" : "Member";

  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <Link to="/" className="brand">
          <BrandMark />
          Newtonite Ops
        </Link>
        {canCreate && (
          <button className="primary rail-new" onClick={() => navigate(newItemUrl(location))}>
            <Icon name="plus" /> New item
          </button>
        )}
        {/* One group, so on a phone the links share a single row that scrolls sideways. */}
        <div className="rail-nav">
          <NavLink to="/" end className="nav-link">
            <Icon name="dashboard" /> Dashboard
          </NavLink>
          <NavLink to="/inbox" className="nav-link">
            <Icon name="inbox" /> Inbox
            {inboxCount > 0 && <span className="nav-count">{inboxCount}</span>}
          </NavLink>
          <NavLink to="/items" end className="nav-link">
            <Icon name="list" /> All items
          </NavLink>
          <NavLink to="/notifications" className="nav-link">
            <Icon name="bell" /> Notifications
            {unreadCount > 0 && <span className="nav-count hot">{unread.data.capped ? "50+" : unreadCount}</span>}
          </NavLink>
          {me.is_admin && (
            <NavLink to="/admin" className="nav-link">
              <Icon name="shield" /> Admin
              {dash.data?.failed_jobs > 0 && (
                <span className="nav-count hot" title="Background jobs that failed and wait for a retry">
                  {dash.data.failed_jobs}
                </span>
              )}
            </NavLink>
          )}
        </div>
        {/* The team lists scroll on their own, so any number of teams fits and your name stays at the foot. */}
        <div className="rail-teams">
          {(me.teams.length > 0 || me.private) && (
            <div className="rail-section">
              Your teams
              {/* On the demo a visitor's own teams are theirs to make; the dashboard's setup does it. */}
              {me.private && (
                <Link to="/?new-team=1" className="rail-add" title="New team" aria-label="New team">
                  <Icon name="plus" />
                </Link>
              )}
            </div>
          )}
          {me.teams.map((t) => (
            <Link key={t.id} to={`/items?team=${t.id}`} className="team-link">
              <span className="team-swatch" style={{ background: teamColor(t.color) }} />
              {t.name}
              <span className="role">{t.role}</span>
            </Link>
          ))}
          {/* An admin also sees the teams they are not on; they are listed, so the sidebar matches the data. */}
          {otherTeams.length > 0 && <div className="rail-section">Other teams</div>}
          {otherTeams.map((t) => (
            <Link key={t.id} to={`/items?team=${t.id}`} className="team-link">
              <span className="team-swatch" style={{ background: teamColor(t.color) }} />
              {t.name}
              <span className="role">admin</span>
            </Link>
          ))}
        </div>
        <div className="rail-foot">
          <Avatar name={me.name} size={28} />
          <div className="who">
            <div className="who-name">{me.name}</div>
            <div className="who-role">{role}</div>
          </div>
          <button className="ghost" onClick={signOut} title="Sign out" aria-label="Sign out">
            <Icon name="out" />
          </button>
        </div>
      </nav>
      <main className="content">
        {demo && <DemoBanner workspace={me.private} />}
        <Outlet context={me} />
      </main>
    </div>
  );
}

// On the items list the form opens over the current filters; anywhere else it opens over all items.
function newItemUrl(location) {
  const params = new URLSearchParams(location.pathname === "/items" ? location.search : "");
  params.set("new", "1");
  return `/items?${params}`;
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/items" element={<Items />} />
            <Route path="/items/:id" element={<Item />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/teams/:id" element={<Team />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
