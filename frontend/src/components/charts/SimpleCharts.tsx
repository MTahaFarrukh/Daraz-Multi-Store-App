/** Lightweight SVG charts — no chart library dependency. */

type Point = { label: string; value: number };

export function SimpleLineChart({
  points,
  emptyLabel = "No data",
  valuePrefix = "",
}: {
  points: Point[];
  emptyLabel?: string;
  valuePrefix?: string;
}) {
  if (!points.length || points.every((p) => p.value === 0)) {
    return <div className="chart-empty muted-line">{emptyLabel}</div>;
  }
  const w = 320;
  const h = 120;
  const pad = 12;
  const max = Math.max(...points.map((p) => p.value), 1);
  const step = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
  const coords = points.map((p, i) => {
    const x = pad + i * step;
    const y = h - pad - (p.value / max) * (h - pad * 2);
    return { x, y, ...p };
  });
  const path = coords
    .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
    .join(" ");
  const last = coords[coords.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="simple-chart" role="img" aria-label="Trend chart">
      <path d={path} fill="none" stroke="var(--teal-deep)" strokeWidth="2" />
      {coords.map((c, i) => (
        <circle key={i} cx={c.x} cy={c.y} r="2.5" fill="var(--teal-deep)">
          <title>
            {c.label}: {valuePrefix}
            {c.value}
          </title>
        </circle>
      ))}
      {last ? (
        <text x={last.x - 4} y={Math.max(14, last.y - 8)} fontSize="10" fill="var(--muted)">
          {valuePrefix}
          {last.value}
        </text>
      ) : null}
    </svg>
  );
}

export function SimpleBarChart({
  points,
  emptyLabel = "No data",
  valuePrefix = "",
}: {
  points: Point[];
  emptyLabel?: string;
  valuePrefix?: string;
}) {
  if (!points.length || points.every((p) => p.value === 0)) {
    return <div className="chart-empty muted-line">{emptyLabel}</div>;
  }
  const max = Math.max(...points.map((p) => p.value), 1);
  return (
    <div className="simple-bars" role="img" aria-label="Bar chart">
      {points.map((p) => (
        <div key={p.label} className="simple-bar-row" title={`${p.label}: ${valuePrefix}${p.value}`}>
          <span className="simple-bar-label">{p.label}</span>
          <div className="simple-bar-track">
            <div
              className="simple-bar-fill"
              style={{ width: `${Math.max(2, (p.value / max) * 100)}%` }}
            />
          </div>
          <span className="simple-bar-value">
            {valuePrefix}
            {p.value}
          </span>
        </div>
      ))}
    </div>
  );
}
