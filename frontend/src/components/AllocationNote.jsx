/** Explains the Suggested column: what is in cash, and any cap it could not honour. */
export default function AllocationNote({ allocation, scope }) {
  if (!allocation) return null;
  const { cash_pct: cash, warnings = [], basis } = allocation;
  return (
    <div className="alloc-note">
      <div className="alloc-note-head">
        <b>Suggested sizing</b>
        <span className="muted">
          one allocation across both tables · {basis?.deployed_pct}% deployed ·{" "}
          <b>{cash}% cash</b>
        </span>
      </div>
      {basis?.scope && <div className="alloc-fine">{basis.scope}</div>}
      {scope !== "picks" && basis?.component_weights && (
        <div className="alloc-weights">
          {Object.entries(basis.component_weights).map(([k, v]) => (
            <span key={k}>
              {k} <b>{Math.round(v * 100)}%</b>
            </span>
          ))}
        </div>
      )}
      {scope !== "picks" &&
        warnings.map((w, i) => (
          <div className="alloc-warn" key={i}>⚠ {w}</div>
        ))}
      {scope !== "picks" && basis?.turnover_note && (
        <div className="alloc-fine">{basis.turnover_note}</div>
      )}
      {scope !== "picks" && <div className="alloc-fine">{basis?.note}</div>}
    </div>
  );
}
