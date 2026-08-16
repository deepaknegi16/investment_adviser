import { useEffect, useState } from "react";
import { api } from "../api.js";
import LineChart from "./LineChart.jsx";
import { AdviceBadge, Pct } from "./PortfolioTable.jsx";

const STATUS_LABEL = { green: "UPTREND", red: "DOWNTREND", orange: "MIXED" };

export default function StockDrawer({ share, onClose, onRemove }) {
  const [period, setPeriod] = useState("1y");
  const [chart, setChart] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [aiLoading, setAiLoading] = useState(true);
  const [aiError, setAiError] = useState(null);

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

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="drawer">
        <div className="drawer-head">
          <div>
            <h2>
              {share.name} <span className="share-symbol">{share.symbol}</span>
            </h2>
            <div className="price-line">
              ₹{share.price?.toLocaleString("en-IN")}{" "}
              <span style={{ fontSize: 15 }}>
                <Pct value={share.day_change_pct} /> today
              </span>
            </div>
            <div>
              <span className={`dot ${share.status}`} />{" "}
              <b>{STATUS_LABEL[share.status]}</b>
              {"  ·  "}Base advice: <AdviceBadge advice={share.advice} />
            </div>
          </div>
          <button className="close-btn" onClick={onClose}>✕</button>
        </div>

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
            <dl>
              <div className="kv"><dt>SMA 50</dt><dd>{share.sma50 ?? "—"}</dd></div>
              <div className="kv"><dt>SMA 200</dt><dd>{share.sma200 ?? "—"}</dd></div>
              <div className="kv"><dt>RSI (14)</dt><dd>{share.rsi ?? "—"}</dd></div>
              <div className="kv"><dt>vs 52-wk high</dt><dd><Pct value={share.pct_from_high52} /></dd></div>
              <div className="kv">
                <dt>Analyst consensus</dt>
                <dd>
                  {share.consensus?.mean
                    ? `${share.consensus.mean} (${share.consensus.label || "n/a"})`
                    : "—"}
                </dd>
              </div>
              <div className="kv">
                <dt>Target price</dt>
                <dd>{share.consensus?.target ? `₹${share.consensus.target}` : "—"}</dd>
              </div>
            </dl>
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
          <button className="danger" onClick={() => onRemove(share.symbol)}>
            🗑 Remove from watchlist
          </button>
        </div>
        <p className="disclaimer">⚠ AI-generated. Not financial advice.</p>
      </div>
    </>
  );
}
