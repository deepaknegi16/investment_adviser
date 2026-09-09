/** Suggested weight for one row, with a bar so relative size reads at a glance. */
export default function AllocCell({ pct, why, signal, max = 15 }) {
  const value = typeof pct === "number" ? pct : null;
  const tip = [why, signal].filter(Boolean).join("\n\n");
  if (value === null) return <td className="alloc-cell muted">—</td>;
  if (value <= 0) {
    return (
      <td className="alloc-cell" title={tip || "Not suggested"}>
        <span className="alloc-zero">—</span>
      </td>
    );
  }
  return (
    <td className="alloc-cell" title={tip}>
      <span className="alloc-pct">{value}%</span>
      <span className="alloc-bar" aria-hidden="true">
        <i style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
      </span>
    </td>
  );
}
