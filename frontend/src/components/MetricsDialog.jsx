import { useEffect, useState } from "react";
import { api } from "../api.js";

function Tile({ label, value, sub }) {
  return (
    <div className="stat-tile">
      <div className="stat-value">{value ?? "—"}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function Breakdown({ title, data }) {
  const entries = Object.entries(data || {});
  return (
    <div>
      <h4>{title}</h4>
      {entries.length === 0 ? (
        <span className="muted">no data yet</span>
      ) : (
        entries.map(([k, v]) => (
          <div className="kv" key={k}>
            <dt className="muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))
      )}
    </div>
  );
}

export default function MetricsDialog({ onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.metrics());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const evalResults = data?.eval?.results;
  const gold = data?.gold_watch;
  const guards = data?.guardrails;
  const goldEval = data?.gold_eval;

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog metrics-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="metrics-head">
          <h3>📊 System metrics</h3>
          <span>
            <button className="ghost" onClick={load} disabled={loading}>↻ Refresh</button>{" "}
            <button className="close-btn" onClick={onClose}>✕</button>
          </span>
        </div>

        {error && <div className="error-banner">{error}</div>}
        {loading && !data ? (
          <div className="loading">Loading metrics…</div>
        ) : data ? (
          <>
            <div className="stat-grid">
              <Tile
                label="RAG chunks"
                value={data.rag.total_chunks}
                sub={data.rag.embedding_coverage_pct != null ? `${data.rag.embedding_coverage_pct}% embedded` : null}
              />
              <Tile
                label="Analyses cached"
                value={data.caches.analyses_cached_total}
                sub={`${data.caches.analyses_cached_today} today`}
              />
              <Tile
                label="Chat turns"
                value={data.chat.turns_total}
                sub={`${data.chat.turns_last_24h} in 24h`}
              />
              <Tile
                label="Groq fallback"
                value={data.chat.groq_fallback_rate_pct != null ? `${data.chat.groq_fallback_rate_pct}%` : "—"}
                sub="of chat turns"
              />
              <Tile
                label="Avg similarity"
                value={data.chat.avg_top_similarity ?? "—"}
                sub="top retrieved chunk"
              />
              <Tile
                label="Avg latency"
                value={data.chat.avg_latency_ms != null ? `${(data.chat.avg_latency_ms / 1000).toFixed(1)}s` : "—"}
                sub="per chat answer"
              />
              <Tile
                label="Gold events"
                value={gold?.events_tracked ?? "—"}
                sub={gold ? `${gold.runs_total} sweeps · ${gold.alerts_sent} alerts sent` : "no sweeps yet"}
              />
              <Tile
                label="Trusted events"
                value={guards?.trusted_rate_pct != null ? `${guards.trusted_rate_pct}%` : "—"}
                sub={guards ? `${guards.violations_last_24h} guardrail hits in 24h` : "no data"}
              />
            </div>

            <div className="metrics-grid">
              <div className="mini-panel">
                <h3>RAG corpus</h3>
                <Breakdown title="Chunks by source" data={data.rag.by_source} />
                <div className="kv" style={{ marginTop: 6 }}>
                  <dt className="muted">watchlist size</dt>
                  <dd>{data.caches.watchlist_size}</dd>
                </div>
                <div className="kv">
                  <dt className="muted">picks cached</dt>
                  <dd>{data.caches.latest_picks_date || "never"}</dd>
                </div>
              </div>

              <div className="mini-panel">
                <h3>Chat quality</h3>
                <Breakdown title="Answered by" data={data.chat.provider_breakdown} />
                <Breakdown title="Retrieval mode" data={data.chat.retrieval_mode_breakdown} />
              </div>

              <div className="mini-panel">
                <h3>Gold watch (GOLDBEES)</h3>
                {gold?.runs_total ? (
                  <>
                    <div className="kv">
                      <dt className="muted">last sweep</dt>
                      <dd>{gold.last_run_at?.replace("T", " ") || "—"}</dd>
                    </div>
                    <div className="kv">
                      <dt className="muted">factor bias</dt>
                      <dd>
                        {gold.last_bias || "—"}
                        {gold.last_conviction ? ` (${gold.last_conviction})` : ""}
                      </dd>
                    </div>
                    <div className="kv">
                      <dt className="muted">GOLDBEES at sweep</dt>
                      <dd>{gold.last_etf_price != null ? `₹${gold.last_etf_price}` : "—"}</dd>
                    </div>
                    <div className="kv">
                      <dt className="muted">email alerts</dt>
                      <dd>
                        {gold.email_configured
                          ? `on → ${(gold.email_to || []).join(", ")}`
                          : `off — set ${(gold.email_missing_config || []).join(", ")}`}
                      </dd>
                    </div>
                    <Breakdown title="Events by impact" data={gold.events_by_impact} />
                    <Breakdown title="Events by factor" data={gold.events_by_factor} />
                  </>
                ) : (
                  <span className="muted">
                    no sweeps yet — run backend/gold_watch_run.py
                  </span>
                )}
              </div>

              <div className="mini-panel">
                <h3>Guardrails</h3>
                {guards?.events_scored ? (
                  <>
                    <div className="kv">
                      <dt className="muted">events checked</dt>
                      <dd>{guards.events_scored}</dd>
                    </div>
                    <div className="kv">
                      <dt className="muted">trusted</dt>
                      <dd>
                        {guards.events_trusted}
                        {guards.trusted_rate_pct != null ? ` (${guards.trusted_rate_pct}%)` : ""}
                      </dd>
                    </div>
                    <div className="kv">
                      <dt className="muted">alerts suppressed</dt>
                      <dd>{guards.alerts_suppressed}</dd>
                    </div>
                    {guards.last_suppressed_reason && (
                      <div className="kv">
                        <dt className="muted">last suppression</dt>
                        <dd>{guards.last_suppressed_reason}</dd>
                      </div>
                    )}
                    <Breakdown title="Violations (24h) by kind" data={guards.by_kind_24h} />
                    {goldEval?.latest_run_at && (
                      <div className="kv" style={{ marginTop: 6 }}>
                        <dt className="muted">last gold eval</dt>
                        <dd>{goldEval.passed ? "PASS" : "FAIL"} · {goldEval.latest_run_at?.replace("T", " ")}</dd>
                      </div>
                    )}
                  </>
                ) : (
                  <span className="muted">
                    no checked sweeps yet — run backend/gold_watch_run.py
                  </span>
                )}
              </div>

              <div className="mini-panel" style={{ gridColumn: "1 / -1" }}>
                <h3>Latest evaluation run</h3>
                {evalResults ? (
                  <>
                    <div className="stat-grid eval-grid">
                      <Tile label="recall@5" value={evalResults.retrieval?.recall_at_5} sub="right chunk in top 5" />
                      <Tile label="MRR" value={evalResults.retrieval?.mrr} sub="mean reciprocal rank" />
                      <Tile
                        label="Faithfulness"
                        value={evalResults.generation?.avg_faithfulness != null ? `${evalResults.generation.avg_faithfulness}/5` : "—"}
                        sub="claims supported by context"
                      />
                      <Tile
                        label="Relevance"
                        value={evalResults.generation?.avg_relevance != null ? `${evalResults.generation.avg_relevance}/5` : "—"}
                        sub="answers the question"
                      />
                    </div>
                    <p className="muted" style={{ margin: "8px 0 0" }}>
                      {evalResults.retrieval?.n_questions} golden questions ·{" "}
                      {evalResults.generation ? `${evalResults.generation.n_judged} answers judged · ` : ""}
                      ran {data.eval.latest_run_at}
                    </p>
                  </>
                ) : (
                  <p className="muted" style={{ margin: 0 }}>
                    No eval run yet — from <code>backend/</code> run{" "}
                    <code>.venv/bin/python eval_rag.py</code>
                  </p>
                )}
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
