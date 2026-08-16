import { useEffect, useState } from "react";
import { api } from "../api.js";
import LineChart from "./LineChart.jsx";
import { AdviceBadge, Pct } from "./PortfolioTable.jsx";

const STATUS_LABEL = { green: "UPTREND", red: "DOWNTREND", orange: "MIXED" };

function scoreClass(score) {
  return score > 0 ? "pos" : score < 0 ? "neg" : "muted";
}

export default function StockDrawer({ share, onClose, onRemove, onWatchlistChange }) {
  // `share` may be a full watchlist row, or just {symbol, name, pick} from the
  // top-20 table — in that case fetch the full summary.
  const hasMetrics = share.price !== undefined;
  const [details, setDetails] = useState(hasMetrics ? share : null);
  const [detailsError, setDetailsError] = useState(null);
  const [period, setPeriod] = useState("1y");
  const [chart, setChart] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [aiLoading, setAiLoading] = useState(true);
  const [aiError, setAiError] = useState(null);
  const [showLogic, setShowLogic] = useState(false);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (share.price !== undefined) {
      setDetails(share);
    } else {
      setDetails(null);
      api.summary(share.symbol).then(setDetails).catch((e) => setDetailsError(e.message));
    }
  }, [share]);

  useEffect(() => {
    setChart(null);
    api.history(share.symbol, period).then((d) => setChart(d.points)).catch(() => setChart([]));
  }, [share.symbol, period]);

  const loadAnalysis = async (refresh = false) => {
    setAiLoading(true);
    setAiError(null);
    try {
      setAnalysis(await api.analysis(share.symbol, refresh));
    } catch (e) {
      setAiError(e.message);
    } finally {
      setAiLoading(false);
    }
  };

  useEffect(() => {
    setAnalysis(null);
    loadAnalysis();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [share.symbol]);

  const addToWatchlist = async () => {
    setAdding(true);
    try {
      await api.addShare(share.symbol, details?.name || share.name);
      setDetails((d) => (d ? { ...d, in_watchlist: true } : d));
      onWatchlistChange?.();
    } catch {
      /* keep drawer usable */
    } finally {
      setAdding(false);
    }
  };

  const logic = details?.advice_logic;
  const inWatchlist = details?.in_watchlist !== false; // watchlist rows omit the flag

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="drawer">
        <div className="drawer-head">
          <div>
            <h2>
              {details?.name || share.name} <span className="share-symbol">{share.symbol}</span>
            </h2>
            {detailsError ? (
              <div className="error-banner">{detailsError}</div>
            ) : !details ? (
              <div className="muted">Loading data…</div>
            ) : (
              <>
                <div className="price-line">
                  ₹{details.price?.toLocaleString("en-IN")}{" "}
                  <span style={{ fontSize: 15 }}>
                    <Pct value={details.day_change_pct} /> today
                  </span>
                </div>
                <div>
                  <span className={`dot ${details.status}`} />{" "}
                  <b>{STATUS_LABEL[details.status]}</b>
                  {"  ·  "}Base advice: <AdviceBadge advice={details.advice} />{" "}
                  {logic && (
                    <button className="link-btn" onClick={() => setShowLogic((v) => !v)}>
                      {showLogic ? "hide logic" : "why?"}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
          <button className="close-btn" onClick={onClose}>✕</button>
        </div>

        {share.pick && (
          <div className="mini-panel pick-panel">
            <h3>🏆 AI screener pick #{share.pick.rank}</h3>
            <p style={{ margin: 0 }}>
              <AdviceBadge advice={share.pick.recommendation} /> {share.pick.rationale}
            </p>
          </div>
        )}

        {showLogic && logic && (
          <div className="mini-panel" style={{ marginTop: 10 }}>
            <h3>Why this advice</h3>
            {logic.factors.map((f, i) => (
              <div className="logic-item" key={i}>
                <span className={`logic-score ${scoreClass(f.score)}`}>
                  {f.score > 0 ? `+${f.score}` : f.score}
                </span>
                <span>
                  <b>{f.factor}:</b> {f.detail}
                </span>
              </div>
            ))}
            <p className="muted" style={{ marginBottom: 0 }}>
              Total score {logic.blended_score > 0 ? "+" : ""}{logic.blended_score}
              {" — "}{logic.rule}
            </p>
          </div>
        )}

        <div className="period-tabs">
          {["1w", "1m", "1y", "5y"].map((p) => (
            <button
              key={p}
              className={p === period ? "active" : ""}
              onClick={() => setPeriod(p)}
            >
              {p.toUpperCase()}
            </button>
          ))}
        </div>
        {chart === null ? <div className="loading">Loading chart…</div> : <LineChart points={chart} />}

        <div className="panel-grid">
          <div className="mini-panel">
            <h3>Technicals</h3>
            {details ? (
              <dl>
                <div className="kv"><dt>SMA 50</dt><dd>{details.sma50 ?? "—"}</dd></div>
                <div className="kv"><dt>SMA 200</dt><dd>{details.sma200 ?? "—"}</dd></div>
                <div className="kv"><dt>RSI (14)</dt><dd>{details.rsi ?? "—"}</dd></div>
                <div className="kv"><dt>vs 52-wk high</dt><dd><Pct value={details.pct_from_high52} /></dd></div>
                <div className="kv">
                  <dt>Analyst consensus</dt>
                  <dd>
                    {details.consensus?.mean
                      ? `${details.consensus.mean} (${details.consensus.label || "n/a"})`
                      : "—"}
                  </dd>
                </div>
                <div className="kv">
                  <dt>Target price</dt>
                  <dd>{details.consensus?.target ? `₹${details.consensus.target}` : "—"}</dd>
                </div>
              </dl>
            ) : (
              <div className="muted">Loading…</div>
            )}
          </div>

          <div className="mini-panel">
            <h3>AI Prediction</h3>
            {aiLoading ? (
              <div className="muted">Analyzing with AI… this can take a couple of minutes on first load.</div>
            ) : aiError ? (
              <div className="muted">Unavailable: {aiError}</div>
            ) : analysis ? (
              <>
                <div className="kv" style={{ display: "flex", justifyContent: "space-between" }}>
                  <span className="muted">AI advice</span>
                  <AdviceBadge advice={analysis.recommendation} />
                </div>
                <p><b>Short term:</b> {analysis.prediction.short_term}</p>
                <p><b>Long term:</b> {analysis.prediction.long_term}</p>
                <p className="muted">
                  Confidence: {analysis.prediction.confidence} · {analysis.generated_at}
                  {analysis.cached ? " (cached)" : ""}
                </p>
                <p>{analysis.reasoning}</p>
              </>
            ) : null}
          </div>
        </div>

        <div className="mini-panel" style={{ marginTop: 12 }}>
          <h3>Latest news</h3>
          {aiLoading ? (
            <div className="muted">Fetching news…</div>
          ) : analysis?.news?.length ? (
            analysis.news.map((n, i) => (
              <div className="news-item" key={i}>
                {n.url ? (
                  <a href={n.url} target="_blank" rel="noreferrer">{n.headline}</a>
                ) : (
                  <b>{n.headline}</b>
                )}
                <div className="src">{n.source}{n.date ? ` · ${n.date}` : ""}</div>
                <p>{n.summary}</p>
              </div>
            ))
          ) : (
            <div className="muted">No news available.</div>
          )}
        </div>

        <div className="drawer-actions">
          <button className="ghost" onClick={() => loadAnalysis(true)} disabled={aiLoading}>
            ↻ Refresh AI analysis
          </button>
          {inWatchlist ? (
            <button className="danger" onClick={() => onRemove(share.symbol)}>
              🗑 Remove from watchlist
            </button>
          ) : (
            <button onClick={addToWatchlist} disabled={adding}>
              {adding ? "Adding…" : "＋ Add to watchlist"}
            </button>
          )}
        </div>
        <p className="disclaimer">⚠ AI-generated. Not financial advice.</p>
      </div>
    </>
  );
}
