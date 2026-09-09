/** Suggested weight for one row, with a bar so relative size reads at a glance. */
export default function AllocCell({ pct, why, max = 15 }) {
  const value = typeof pct === "number" ? pct : null;
  if (value === null) return <td className="alloc-cell muted">—</td>;
  if (value <= 0) {
    return (
      <td className="alloc-cell" title={why || "Not suggested"}>
        <span className="alloc-zero">—</span>
      </td>
    );
  }
  return (
    <td className="alloc-cell" title={why || ""}>
      <span className="alloc-pct">{value}%</span>
      <span className="alloc-bar" aria-hidden="true">
        <i style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
      </span>
    </td>
  );
}
