/** Explains the Suggested column: what is in cash, and any cap it could not honour. */
export default function AllocationNote({ allocation }) {
  if (!allocation) return null;
  const { cash_pct: cash, warnings = [], basis } = allocation;
  return (
    <div className="alloc-note">
      <div className="alloc-note-head">
        <b>Suggested sizing</b>
        <span className="muted">
          {basis?.method} · {basis?.deployed_pct}% deployed · <b>{cash}% cash</b>
        </span>
      </div>
      {basis?.component_weights && (
        <div className="alloc-weights">
          {Object.entries(basis.component_weights).map(([k, v]) => (
            <span key={k}>
              {k} <b>{Math.round(v * 100)}%</b>
            </span>
          ))}
        </div>
      )}
      {warnings.map((w, i) => (
        <div className="alloc-warn" key={i}>⚠ {w}</div>
      ))}
      {basis?.turnover_note && <div className="alloc-fine">{basis.turnover_note}</div>}
      <div className="alloc-fine">{basis?.note}</div>
    </div>
  );
}
