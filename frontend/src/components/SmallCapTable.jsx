import { useEffect, useState } from "react";
import { api } from "../api.js";

const cr = (n) => (n == null ? "—" : `₹${Number(n).toLocaleString("en-IN")} cr`);
const pct = (n) => (n == null ? "—" : `${n > 0 ? "+" : ""}${n}%`);

/** Small/mid caps that clear a hard base before promise is scored at all. */
export default function SmallCapTable({ onSelect }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showGates, setShowGates] = useState(false);

  const load = async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.smallcaps(refresh));
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
        <h2>🌱 Small &amp; mid caps with a solid base</h2>
        <button className="ghost" onClick={() => load(true)} disabled={loading}>
          {loading ? "Screening…" : "↻ Rescreen"}
        </button>
      </div>
      <div className="card">
      {error ? (
        <div className="loading">Screen unavailable: {error}</div>
      ) : loading && !data ? (
        <div className="loading">Screening 140 small &amp; mid caps…</div>
      ) : data ? (
        <>
          <div className="sc-summary">
            <b>{data.n_passed}</b> of {data.n_assessed} names clear every base gate
            {data.cached && <span className="muted"> · as of {data.as_of}</span>}
            <button className="ghost sc-gates-toggle" onClick={() => setShowGates((v) => !v)}>
              {showGates ? "hide the gates" : "what are the gates?"}
            </button>
          </div>

          {showGates && data.gates && (
            <div className="sc-gates">
              <div>
                Market cap {cr(data.gates.market_cap_cr[0])} – {cr(data.gates.market_cap_cr[1])}
                <span className="muted"> · below this, liquidity and governance risk dominate; above it, the Top-20 already covers it</span>
              </div>
              <div>
                Median daily turnover ≥ ₹{data.gates.min_daily_turnover_cr} cr
                <span className="muted"> · the gate that lets you actually exit</span>
              </div>
              <div>Positive trailing earnings <span className="muted">· a small cap without profit is a story</span></div>
              <div>ROE ≥ {data.gates.min_roe_pct}% <span className="muted">· earns on its own capital</span></div>
              <div>Debt/equity ≤ {data.gates.max_debt_equity_pct}% <span className="muted">· small caps fail through the balance sheet</span></div>
              <div>Revenue not shrinking <span className="muted">· cheap and shrinking is a trap, not a base</span></div>
              <div>
                Above the 200-day average, but under {data.gates.max_extension_above_sma200_pct}% above it, RSI ≤ {data.gates.max_rsi}
                <span className="muted"> · a base is something price built on, not something it is falling through — and not a vertical move you are late to</span>
              </div>
            </div>
          )}

            <table>
              <thead>
                <tr>
                  <th>Share</th>
                  <th>Price ₹</th>
                  <th>Market cap</th>
                  <th title="Median daily traded value — how easily you could exit">Liquidity</th>
                  <th>ROE</th>
                  <th>P/E</th>
                  <th>1Y</th>
                  <th style={{ textAlign: "left" }}>Why it ranks here</th>
                </tr>
              </thead>
              <tbody>
                {data.shares.map((s) => (
                  <tr key={s.symbol} onClick={() => onSelect?.({ symbol: s.symbol, name: s.name })}>
                    <td>
                      <b>{s.name}</b>{" "}
                      <span className="muted">{s.symbol.replace(".NS", "")}</span>
                      {s.sector && <div className="muted sc-sector">{s.sector}</div>}
                    </td>
                    <td>{s.price}</td>
                    <td>{cr(s.market_cap_cr)}</td>
                    <td>₹{s.turnover_cr} cr<span className="muted">/day</span></td>
                    <td>{s.roe}%</td>
                    <td>{s.trailing_pe ?? "—"}</td>
                    <td className={s.ret_1y >= 0 ? "up" : "down"}>{pct(s.ret_1y)}</td>
                    <td className="why">{s.signal}</td>
                  </tr>
                ))}
              </tbody>
            </table>

          {data.near_misses?.length > 0 && (
            <div className="sc-near">
              <b>Just missed</b> <span className="muted">— failed exactly one gate, often the more interesting list</span>
              <ul>
                {data.near_misses.map((n) => (
                  <li key={n.symbol}>
                    {n.name} <span className="muted">— {n.missed_on[0]}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.coverage_note && <div className="alloc-fine">{data.coverage_note}</div>}
          <div className="sc-risk">⚠ {data.risk_note}</div>
        </>
      ) : null}
      </div>
    </div>
  );
}
