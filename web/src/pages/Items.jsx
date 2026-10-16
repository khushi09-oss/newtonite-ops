import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { api, newKey } from "../api";
import { Icon } from "../icons";
import { ItemRow, KIND_LABEL, PRIORITY_LABEL, STATUS_LABEL, StatusIcon } from "../ui";

const FILTERS = ["team", "status", "priority", "assignee", "overdue", "q"];
const STATUSES = Object.keys(STATUS_LABEL);
const DONE = ["resolved", "closed"];
// Each group opens on its ten most recent, so every status is in view; more come 25 at a time.
const FIRST = 10;
const PAGE = 25;

export default function Items() {
  const me = useOutletContext();
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((f) => [f, params.get(f) || ""]));
  const [search, setSearch] = useState(filters.q);
  // "New item" is ?new=1, so the sidebar's button opens the form from any page, this one included.
  const creating = params.get("new") === "1";
  // Finished work starts folded away and open work starts open. A search opens every group, since
  // what you typed may well be finished; folding during a search is remembered apart from that.
  const [folded, setFolded] = useState(() => new Set(DONE));
  const foldKey = (s) => (filters.q ? `search:${s}` : s);

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
    next.delete("new");
    setParams(next, { replace: true });
  }

  function setCreating(on) {
    const next = new URLSearchParams(params);
    if (on) next.set("new", "1");
    else next.delete("new");
    setParams(next, { replace: !on });
  }

  function clearFilters() {
    setSearch("");
    setParams(new URLSearchParams(), { replace: true });
  }

  // Every filter but status: status is what the list is grouped by.
  const query = new URLSearchParams(
    Object.entries(filters).filter(([k, v]) => v && k !== "status"),
  ).toString();
  const counts = useQuery({ queryKey: ["items", "counts", query], queryFn: () => api(`/items/counts?${query}`) });
  const groups = filters.status ? [filters.status] : STATUSES;
  const total = counts.data ? groups.reduce((n, s) => n + counts.data.counts[s], 0) : 0;
  const open = counts.data ? groups.filter((s) => !DONE.includes(s)).reduce((n, s) => n + counts.data.counts[s], 0) : 0;
  const filtered = FILTERS.some((f) => filters[f]);
  const canCreate = me.is_admin || me.teams.some((t) => t.role !== "viewer");

  const toggle = (s) =>
    setFolded((f) => {
      const next = new Set(f);
      next.has(foldKey(s)) ? next.delete(foldKey(s)) : next.add(foldKey(s));
      return next;
    });

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Items</h1>
          <p className="muted">
            {counts.data ? summary(open, total - open, filtered) : "Everything on your teams, grouped by where it is in the workflow."}
          </p>
        </div>
        {canCreate && (
          <button className="primary" onClick={() => setCreating(true)}>
            <Icon name="plus" /> New item
          </button>
        )}
      </header>
      {creating && <NewItem me={me} onDone={() => setCreating(false)} />}

      <div className="filter-bar">
        <label className="search">
          <Icon name="search" />
          <input
            type="search"
            aria-label="Search items"
            placeholder="Search, or type a key like RSK-12"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <select aria-label="Team" value={filters.team} onChange={(e) => setFilter("team", e.target.value)}>
          <option value="">All my teams</option>
          {me.teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select aria-label="Priority" value={filters.priority} onChange={(e) => setFilter("priority", e.target.value)}>
          <option value="">Any priority</option>
          {PRIORITY_LABEL.map((l, i) => (
            <option key={i} value={i}>
              {l}
            </option>
          ))}
        </select>
        <button
          className={`chip ${filters.assignee === me.id ? "chip-on" : ""}`}
          aria-pressed={filters.assignee === me.id}
          onClick={() => setFilter("assignee", filters.assignee === me.id ? "" : me.id)}
        >
          Assigned to me
        </button>
        <button
          className={`chip ${filters.overdue ? "chip-on" : ""}`}
          aria-pressed={!!filters.overdue}
          onClick={() => setFilter("overdue", filters.overdue ? "" : "true")}
        >
          Overdue
        </button>
        {filtered && (
          <button className="link clear" onClick={clearFilters}>
            Clear filters
          </button>
        )}
      </div>

      {counts.isPending && <div className="page-loading">Loading items</div>}
      {counts.isError && <div className="error">Could not load items: {counts.error.message}</div>}
      {counts.data && total === 0 && (
        <div className="empty-state">
          <strong>Nothing matches.</strong>
          <span className="muted">Try another search, or clear the filters.</span>
        </div>
      )}
      {counts.data &&
        groups
          .filter((s) => counts.data.counts[s] > 0)
          .map((s) => (
            <Group
              key={s}
              status={s}
              query={query}
              count={counts.data.counts[s]}
              open={!folded.has(foldKey(s)) || !!filters.status}
              onToggle={() => toggle(s)}
            />
          ))}
    </div>
  );
}

function summary(open, done, filtered) {
  const parts = [open && `${open.toLocaleString()} open`, done && `${done.toLocaleString()} finished`].filter(Boolean);
  if (!parts.length) return filtered ? "Nothing matches these filters." : "Nothing on your teams yet.";
  const verb = open + done === 1 ? "matches" : "match";
  return `${parts.join(" and ")} ${filtered ? `${verb} these filters` : "on your teams"}.`;
}

function Group({ status, query, count, open, onToggle }) {
  const list = useInfiniteQuery({
    queryKey: ["items", query, status],
    queryFn: ({ pageParam }) =>
      api(`/items?${query ? `${query}&` : ""}status=${status}&limit=${pageParam ? PAGE : FIRST}${pageParam ? `&cursor=${pageParam}` : ""}`),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: open,
  });
  const items = list.data ? list.data.pages.flatMap((p) => p.items) : [];

  return (
    <section className={`group ${open ? "open" : ""}`}>
      <button className="group-head" aria-expanded={open} onClick={onToggle}>
        <Icon name="chevron" className="group-chevron" />
        <StatusIcon value={status} />
        <span className="group-name">{STATUS_LABEL[status]}</span>
        <span className="group-count">{count.toLocaleString()}</span>
      </button>
      {open && (
        <div className="group-body">
          {list.isPending && <div className="group-note">Loading</div>}
          {list.isError && <div className="group-note error">Could not load: {list.error.message}</div>}
          {items.map((item) => (
            <ItemRow key={item.id} item={item} showStatus={false} />
          ))}
          {list.hasNextPage && (
            <button className="group-more" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
              {list.isFetchingNextPage ? "Loading" : `Show more (${(count - items.length).toLocaleString()} left)`}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function NewItem({ me, onDone }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const dialog = useRef(null);
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
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onDone();
      navigate(`/items/${item.id}`);
    },
  });
  const set = (k) => (e) => setForm({ ...form, [k]: k === "priority" ? Number(e.target.value) : e.target.value });

  // A native modal: focus is trapped, Escape closes it, and the page behind is inert.
  useEffect(() => {
    dialog.current.showModal();
    dialog.current.querySelector(".modal-title").focus();
  }, []);

  return (
    <dialog ref={dialog} className="modal" onClose={onDone} aria-labelledby="new-item-title">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="modal-head">
          <h2 id="new-item-title">New item</h2>
          <button type="button" className="ghost" aria-label="Close" onClick={() => dialog.current.close()}>
            Esc
          </button>
        </div>
        <input
          className="modal-title"
          aria-label="Title"
          placeholder="What needs doing?"
          value={form.title}
          onChange={set("title")}
          required
          maxLength={200}
        />
        <textarea
          aria-label="Description"
          placeholder="Why it exists, and what you already know"
          value={form.description}
          onChange={set("description")}
          rows={4}
        />
        <div className="modal-fields">
          <label>
            Team
            <select value={form.team_id} onChange={set("team_id")}>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Kind
            <select value={form.kind} onChange={set("kind")}>
              {Object.entries(KIND_LABEL).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            Priority
            <select value={form.priority} onChange={set("priority")}>
              {PRIORITY_LABEL.map((l, i) => (
                <option key={i} value={i}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        </div>
        {create.isError && <p className="error">{create.error.message}</p>}
        <div className="modal-foot">
          <button type="button" onClick={() => dialog.current.close()}>
            Cancel
          </button>
          <button className="primary" disabled={create.isPending || !form.title.trim()}>
            Create item
          </button>
        </div>
      </form>
    </dialog>
  );
}
