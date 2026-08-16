import { useEffect, useState } from "react";
import { api } from "../api.js";
import { AdviceBadge, Pct } from "./PortfolioTable.jsx";

export default function PicksTable({ onSelect }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.picks(refresh));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="section">
      <div className="section-head">
        <h2>🏆 Top 20 Picks (AI screener)</h2>
        <button className="ghost" onClick={() => load(true)} disabled={loading}>
          {loading ? "Screening… (can take a few minutes)" : "↻ Refresh picks"}
        </button>
      </div>
      <div className="card">
        {error ? (
          <div className="loading">
            AI screener unavailable: {error}
          </div>
        ) : loading && !data ? (
          <div className="loading">Running AI screener…</div>
        ) : data ? (
          <>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Share</th>
                  <th>Price ₹</th>
                  <th>1M</th>
                  <th>1Y</th>
                  <th>Advice</th>
                  <th style={{ textAlign: "left" }}>Why</th>
                </tr>
              </thead>
              <tbody>
                {data.picks.map((p) => (
                  <tr key={p.symbol} onClick={() => onSelect?.(p)} title="Click for full data + reasoning">
                    <td>{p.rank}</td>
                    <td style={{ textAlign: "left" }}>
                      <span className="share-name">{p.name}</span>
                      <span className="share-symbol">{p.symbol.replace(".NS", "")}</span>
                    </td>
                    <td>{p.price ? p.price.toLocaleString("en-IN") : "—"}</td>
                    <td><Pct value={p.ret_1m} /></td>
                    <td><Pct value={p.ret_1y} /></td>
                    <td><AdviceBadge advice={p.recommendation} /></td>
                    <td style={{ textAlign: "left", whiteSpace: "normal", minWidth: 220 }}>
                      {p.rationale}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ padding: "10px 14px" }} className="muted">
              {data.market_note} · Generated {data.generated_at}
              {data.cached ? " (cached from an earlier day — refresh for today's picks)" : ""}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
