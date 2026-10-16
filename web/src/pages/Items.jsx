import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { api, newKey } from "../api";
import { KIND_LABEL, PRIORITY_LABEL, STATUS_LABEL } from "../ui";
import { ItemRow } from "./Inbox";

const FILTERS = ["team", "status", "priority", "assignee", "overdue", "q"];

export default function Items() {
  const me = useOutletContext();
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((f) => [f, params.get(f) || ""]));
  const [search, setSearch] = useState(filters.q);
  // The sidebar's "New item" lands here with ?new=1.
  const [creating, setCreating] = useState(params.get("new") === "1");

  // The search box waits for a pause before it touches the URL, so every keystroke is not a request.
  useEffect(() => {
    const t = setTimeout(() => {
      if (search !== filters.q) setFilter("q", search);
    }, 300);
    return () => clearTimeout(t);
  });

  function setFilter(name, value) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  }

  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  const list = useInfiniteQuery({
    queryKey: ["items", query],
    queryFn: ({ pageParam }) => api(`/items?${query}${pageParam ? `&cursor=${pageParam}` : ""}`),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const sentinel = useRef(null);
  useEffect(() => {
    if (!sentinel.current) return;
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && list.hasNextPage && !list.isFetchingNextPage) list.fetchNextPage();
    });
    io.observe(sentinel.current);
    return () => io.disconnect();
  }, [list.hasNextPage, list.isFetchingNextPage, list.fetchNextPage]);

  const items = list.data ? list.data.pages.flatMap((p) => p.items) : [];
  const canCreate = me.is_admin || me.teams.some((t) => t.role !== "viewer");

  return (
    <div className="page">
      <header className="page-head">
        <h1>Items</h1>
        {canCreate && (
          <button className="primary" onClick={() => setCreating((v) => !v)}>
            {creating ? "Cancel" : "New item"}
          </button>
        )}
      </header>
      {creating && <NewItem me={me} onDone={() => setCreating(false)} />}
      <div className="toolbar">
        <input
          type="search"
          placeholder="Search, or type a key like RSK-12"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={filters.team} onChange={(e) => setFilter("team", e.target.value)}>
          <option value="">All my teams</option>
          {me.teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
          <option value="">Any status</option>
          {Object.entries(STATUS_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select value={filters.priority} onChange={(e) => setFilter("priority", e.target.value)}>
          <option value="">Any priority</option>
          {PRIORITY_LABEL.map((l, i) => (
            <option key={i} value={i}>
              P{i} {l}
            </option>
          ))}
        </select>
        <label className="check">
          <input
            type="checkbox"
            checked={filters.assignee === me.id}
            onChange={(e) => setFilter("assignee", e.target.checked ? me.id : "")}
          />
          Mine
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={filters.overdue === "true"}
            onChange={(e) => setFilter("overdue", e.target.checked ? "true" : "")}
          />
          Overdue
        </label>
      </div>
      {list.isPending && <div className="page-loading">Loading items</div>}
      {list.isError && <div className="error">Could not load items: {list.error.message}</div>}
      {list.isSuccess && items.length === 0 && <p className="empty">No items match these filters.</p>}
      <div className="rows">
        {items.map((item) => (
          <ItemRow key={item.id} item={item} />
        ))}
      </div>
      <div ref={sentinel} className="sentinel">
        {list.isFetchingNextPage ? "Loading more" : list.hasNextPage ? "" : items.length ? "That is everything." : ""}
      </div>
    </div>
  );
}

function NewItem({ me, onDone }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const teams = me.teams.filter((t) => t.role !== "viewer");
  const [form, setForm] = useState({
    team_id: teams[0] ? teams[0].id : "",
    kind: "task",
    title: "",
    description: "",
    priority: 2,
  });
  const keyRef = useRef(newKey()); // one key per form; a retry after a timeout reuses it
  const create = useMutation({
    mutationFn: () => api("/items", { method: "POST", body: form, key: keyRef.current }),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["inbox"] });
      onDone();
      navigate(`/items/${item.id}`);
    },
  });
  const set = (k) => (e) => setForm({ ...form, [k]: k === "priority" ? Number(e.target.value) : e.target.value });

  return (
    <form
      className="new-item"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <input autoFocus placeholder="What needs doing?" value={form.title} onChange={set("title")} required maxLength={200} />
      <textarea placeholder="Why it exists, what you already know" value={form.description} onChange={set("description")} rows={3} />
      <div className="new-item-row">
        <select value={form.team_id} onChange={set("team_id")}>
          {teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={form.kind} onChange={set("kind")}>
          {Object.entries(KIND_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select value={form.priority} onChange={set("priority")}>
          {PRIORITY_LABEL.map((l, i) => (
            <option key={i} value={i}>
              P{i} {l}
            </option>
          ))}
        </select>
        <button className="primary" disabled={create.isPending || !form.title.trim()}>
          Create
        </button>
      </div>
      {create.isError && <p className="error">{create.error.message}</p>}
    </form>
  );
}
