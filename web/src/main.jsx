import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrowserRouter, Link, Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "./api";
import Login from "./pages/Login";
import Inbox from "./pages/Inbox";
import Items from "./pages/Items";
import Item from "./pages/Item";
import Notifications from "./pages/Notifications";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 5000, refetchOnWindowFocus: true } },
});

function Shell() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api("/me") });
  const unread = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => api("/notifications?unread=true"),
    refetchInterval: 15000,
    enabled: me.isSuccess,
  });

  if (me.isPending) return <div className="page-loading">Loading</div>;
  if (me.isError) return <Navigate to="/login" replace />;

  const count = unread.data ? unread.data.notifications.length : 0;
  const signOut = () => {
    document.cookie = "session=; Max-Age=0; path=/";
    qc.clear();
    navigate("/login");
  };

  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <Link to="/inbox" className="brand">
          Newtonite Ops
        </Link>
        <NavLink to="/inbox">Inbox</NavLink>
        <NavLink to="/items">Items</NavLink>
        <NavLink to="/notifications">
          Notifications
          {count > 0 && <span className="count">{unread.data.capped ? "50+" : count}</span>}
        </NavLink>
        <div className="rail-foot">
          <div className="who">{me.data.name}</div>
          <div className="muted small">
            {me.data.is_admin ? "Admin" : null}
            {me.data.teams.map((t) => (
              <div key={t.id}>
                {t.name} <span className="role">{t.role}</span>
              </div>
            ))}
          </div>
          <button className="link" onClick={signOut}>
            Sign out
          </button>
        </div>
      </nav>
      <main className="content">
        <Outlet context={me.data} />
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Shell />}>
            <Route index element={<Navigate to="/inbox" replace />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/items" element={<Items />} />
            <Route path="/items/:id" element={<Item />} />
            <Route path="/notifications" element={<Notifications />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
