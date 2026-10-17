// Two small charts for the dashboard, in plain SVG. Both palettes pass the dataviz validator: a plum
// ordinal ramp for workflow order (light = earliest), and plum and teal for the trend, apart under CVD.
import { useEffect, useRef, useState } from "react";
import { STATUS_LABEL } from "./ui";

export const OPEN_ORDER = ["new", "triaged", "in_progress", "awaiting_approval", "approved"];
export const STATUS_RAMP = {
  new: "#cfa0cf",
  triaged: "#b076b2",
  in_progress: "#8f4f95",
  awaiting_approval: "#6a2d6d",
  approved: "#46134a",
};
const SERIES = { created: "#8a3d8f", resolved: "#159a8a" };

function TableToggle({ table, onToggle }) {
  return (
    <button className="link table-toggle" onClick={onToggle}>
      {table ? "Show as chart" : "Show as table"}
    </button>
  );
}

export function TeamBars({ teams }) {
  const [tip, setTip] = useState(null);
  const [table, setTable] = useState(false);
  const totals = teams.map((t) => OPEN_ORDER.reduce((a, s) => a + (t.by_status[s] || 0), 0));
  const max = Math.max(1, ...totals);
  return (
    <div>
      <div className="legend" aria-hidden={table}>
        {OPEN_ORDER.map((s) => (
          <span key={s}>
            <i className="swatch" style={{ background: STATUS_RAMP[s] }} />
            {STATUS_LABEL[s]}
          </span>
        ))}
        <span style={{ marginLeft: "auto" }}>
          <TableToggle table={table} onToggle={() => setTable((v) => !v)} />
        </span>
      </div>
      {table ? (
        <table className="data-table">
          <thead>
            <tr>
              <th>Team</th>
              {OPEN_ORDER.map((s) => (
                <th key={s}>{STATUS_LABEL[s]}</th>
              ))}
              <th>Open</th>
            </tr>
          </thead>
          <tbody>
            {teams.map((t, i) => (
              <tr key={t.team_id}>
                <td>{t.team}</td>
                {OPEN_ORDER.map((s) => (
                  <td key={s}>{(t.by_status[s] || 0).toLocaleString()}</td>
                ))}
                <td>{totals[i].toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="team-bars chart-wrap" onMouseLeave={() => setTip(null)}>
          {teams.map((t, i) => (
            <div key={t.team_id} className="team-bar">
              <span className="team-bar-name">{t.team}</span>
              <div className="stack" style={{ width: `${(totals[i] / max) * 100}%` }}>
                {OPEN_ORDER.filter((s) => t.by_status[s]).map((s) => (
                  <span
                    key={s}
                    className="stack-seg"
                    style={{ flex: t.by_status[s], background: STATUS_RAMP[s] }}
                    onMouseEnter={(e) => {
                      const box = e.currentTarget.closest(".chart-wrap").getBoundingClientRect();
                      const r = e.currentTarget.getBoundingClientRect();
                      setTip({ x: r.left - box.left + r.width / 2, y: r.top - box.top, team: t.team, status: s, n: t.by_status[s] });
                    }}
                  />
                ))}
              </div>
              <span className="team-bar-total">{totals[i].toLocaleString()}</span>
            </div>
          ))}
          {tip && (
            <div className="chart-tip" style={{ left: tip.x, top: tip.y }}>
              <b>{tip.team}</b>
              <br />
              {STATUS_LABEL[tip.status]}: {tip.n.toLocaleString()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function niceMax(v) {
  if (v <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / step) * step;
}

const day = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function TrendChart({ trend }) {
  const [hover, setHover] = useState(null);
  const [table, setTable] = useState(false);
  // Drawn at the width it is shown at, so its text stays 11px on a phone instead of shrinking.
  const wrap = useRef(null);
  const [W, setW] = useState(640);
  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, [table]);
  const H = 210;
  const m = { l: 34, r: 74, t: 10, b: 24 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const top = niceMax(Math.max(...trend.map((d) => Math.max(d.created, d.resolved))));
  const x = (i) => m.l + (trend.length === 1 ? iw / 2 : (i / (trend.length - 1)) * iw);
  const y = (v) => m.t + ih - (v / top) * ih;
  const path = (key) => trend.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join("");
  const last = trend.length - 1;
  const ticks = [0, top / 2, top];
  // A date label needs about 48px; a narrow chart labels fewer days, always including today.
  const step = Math.max(3, Math.ceil(trend.length / Math.max(1, Math.floor(iw / 48))));
  // The end labels sit at each line's last value. When the values are close (both 0, say) they
  // would print over each other, so keep them a line apart, higher value on top, inside the plot.
  const GAP = 14;
  const ends = { created: y(trend[last].created), resolved: y(trend[last].resolved) };
  if (Math.abs(ends.created - ends.resolved) < GAP) {
    const [upper, lower] = trend[last].created >= trend[last].resolved ? ["created", "resolved"] : ["resolved", "created"];
    const mid = (ends.created + ends.resolved) / 2;
    const shift = Math.min(0, m.t + ih - (mid + GAP / 2)) + Math.max(0, m.t - (mid - GAP / 2));
    ends[upper] = mid - GAP / 2 + shift;
    ends[lower] = mid + GAP / 2 + shift;
  }

  if (table) {
    return (
      <div>
        <Legend table onToggle={() => setTable(false)} />
        <table className="data-table">
          <thead>
            <tr>
              <th>Day</th>
              <th>Created</th>
              <th>Resolved</th>
            </tr>
          </thead>
          <tbody>
            {trend.map((d) => (
              <tr key={d.day}>
                <td>{day(d.day)}</td>
                <td>{d.created}</td>
                <td>{d.resolved}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div>
      <Legend onToggle={() => setTable(true)} />
      <div className="chart-wrap" ref={wrap}>
        <svg
          className="chart-svg"
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Items created and resolved per day over the last ${trend.length} days`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const px = ((e.clientX - r.left) / r.width) * W;
            const i = Math.round(((px - m.l) / iw) * (trend.length - 1));
            setHover(Math.max(0, Math.min(last, i)));
          }}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} stroke={t ? "#eef0f3" : "#d7dbe1"} strokeWidth="1" />
              <text x={m.l - 8} y={y(t) + 4} textAnchor="end">
                {Math.round(t)}
              </text>
            </g>
          ))}
          {trend.map((d, i) =>
            i % step === last % step ? (
              <text key={d.day} x={x(i)} y={H - 6} textAnchor="middle">
                {day(d.day)}
              </text>
            ) : null,
          )}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={m.t} y2={m.t + ih} stroke="#c3c8d0" strokeWidth="1" />}
          {["created", "resolved"].map((k) => (
            <g key={k}>
              <path d={path(k)} fill="none" stroke={SERIES[k]} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={x(hover ?? last)} cy={y(trend[hover ?? last][k])} r="4" fill={SERIES[k]} stroke="#fff" strokeWidth="2" />
              <text x={W - m.r + 10} y={ends[k] + 4} style={{ fill: "#454b56" }}>
                {k === "created" ? "Created" : "Resolved"} {trend[last][k]}
              </text>
            </g>
          ))}
        </svg>
        {hover !== null && (
          <div className="chart-tip" style={{ left: `${(x(hover) / W) * 100}%`, top: `${(m.t / H) * 100}%` }}>
            <b>{day(trend[hover].day)}</b>
            <br />
            Created {trend[hover].created}, resolved {trend[hover].resolved}
          </div>
        )}
      </div>
    </div>
  );
}

function Legend({ table, onToggle }) {
  return (
    <div className="legend">
      <span>
        <i className="line-key" style={{ background: SERIES.created }} /> Created
      </span>
      <span>
        <i className="line-key" style={{ background: SERIES.resolved }} /> Resolved
      </span>
      <span style={{ marginLeft: "auto" }}>
        <TableToggle table={table} onToggle={onToggle} />
      </span>
    </div>
  );
}
