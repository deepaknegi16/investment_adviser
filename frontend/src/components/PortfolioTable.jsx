export function Pct({ value }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  const cls = value > 0 ? "pos" : value < 0 ? "neg" : "muted";
  return <span className={cls}>{value > 0 ? "+" : ""}{value.toFixed(1)}%</span>;
}

export function AdviceBadge({ advice }) {
  if (!advice) return <span className="muted">—</span>;
  const cls = advice.includes("BUY") ? "buy" : advice === "SELL" ? "sell" : "hold";
  return <span className={`badge ${cls}`}>{advice}</span>;
}

export default function PortfolioTable({ shares, onSelect }) {
  if (shares.length === 0) {
    return <div className="loading">Watchlist is empty — add a share to get started.</div>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Share</th>
          <th>Price ₹</th>
          <th>1W</th>
          <th>1M</th>
          <th>1Y</th>
          <th>5Y</th>
          <th style={{ textAlign: "center" }}>Status</th>
          <th>Advice</th>
        </tr>
      </thead>
      <tbody>
        {shares.map((s) => (
          <tr key={s.symbol} onClick={() => !s.error && onSelect(s)}>
            <td>
              <span className="share-name">{s.name}</span>
              <span className="share-symbol">{s.symbol.replace(".NS", "")}</span>
            </td>
            {s.error ? (
              <td colSpan={7} className="muted">no data available</td>
            ) : (
              <>
                <td>{s.price?.toLocaleString("en-IN")}</td>
                <td><Pct value={s.ret_1w} /></td>
                <td><Pct value={s.ret_1m} /></td>
                <td><Pct value={s.ret_1y} /></td>
                <td><Pct value={s.ret_5y} /></td>
                <td style={{ textAlign: "center" }}>
                  <span className={`dot ${s.status}`} title={s.status} />
                </td>
                <td><AdviceBadge advice={s.advice} /></td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
