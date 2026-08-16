export default function LineChart({ points, width = 500, height = 160 }) {
  if (!points || points.length < 2) {
    return <div className="loading">No chart data.</div>;
  }
  const values = points.map((p) => p.c);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 6;
  const x = (i) => pad + (i / (points.length - 1)) * (width - pad * 2);
  const y = (v) => pad + (1 - (v - min) / range) * (height - pad * 2 - 14);
  const path = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  const color = up ? "var(--green)" : "var(--red)";

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto" }}>
      <path d={path} fill="none" stroke={color} strokeWidth="2" />
      <path
        d={`${path} L${x(points.length - 1)},${height - 14} L${x(0)},${height - 14} Z`}
        fill={color}
        opacity="0.08"
      />
      <text x={pad} y={height - 2} fill="var(--muted)" fontSize="10">
        {points[0].t}
      </text>
      <text x={width - pad} y={height - 2} fill="var(--muted)" fontSize="10" textAnchor="end">
        {points[points.length - 1].t}
      </text>
      <text x={width - pad} y={y(max) + 4} fill="var(--muted)" fontSize="10" textAnchor="end">
        ₹{max.toLocaleString("en-IN")}
      </text>
    </svg>
  );
}
