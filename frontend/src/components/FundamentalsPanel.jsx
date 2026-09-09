import { useEffect, useState } from "react";
import { api } from "../api.js";

const VERDICT_LABEL = { good: "good", ok: "ok", watch: "watch" };

/** One metric row. Click to expand what it means and how to read it. */
function Metric({ m }) {
  const [open, setOpen] = useState(false);
  const value =
    m.unit === "₹ cr" ? `₹${Number(m.value).toLocaleString("en-IN")} cr`
    : m.unit === "₹" ? `₹${m.value}`
    : `${m.value}${m.unit}`;

  return (
    <div className={`fund-metric ${open ? "open" : ""}`}>
      <button className="fund-row" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="fund-name">
          {m.label}
          {m.repaired && <span className="fund-fix" title="Rescaled — Yahoo mixes INR price with USD financials for this company">fx</span>}
        </span>
        <span className="fund-value">
          {value}
          {m.verdict && <em className={`fund-verdict ${m.verdict}`}>{VERDICT_LABEL[m.verdict]}</em>}
        </span>
      </button>
      {open && (
        <div className="fund-detail">
          <p><b>What it is.</b> {m.what}</p>
          <p><b>How to read it.</b> {m.read}</p>
          {m.caveat && <p className="fund-caveat"><b>Caveat.</b> {m.caveat}</p>}
        </div>
      )}
    </div>
  );
}

export default function FundamentalsPanel({ symbol }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setData(null);
    api
      .fundamentals(symbol)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [symbol]);

  if (loading) return <div className="muted">Loading fundamentals…</div>;
  if (error) return <div className="muted">Fundamentals unavailable: {error}</div>;
  if (!data?.available) return <div className="muted">{data?.reason}</div>;

  return (
    <div className="fundamentals">
      <div className="fund-pillars">
        {Object.entries(data.pillars).map(([name, p]) => (
          <div key={name} className={`fund-pillar ${p.rating}`}>
            <span className="fund-pillar-name">{name}</span>
            <span className="fund-pillar-rating">{p.rating}</span>
            <span className="fund-pillar-sub">
              {p.good}/{p.n} read well
            </span>
          </div>
        ))}
      </div>

      {data.playbooks.length > 0 && (
        <div className="fund-playbooks">
          {data.playbooks.map((pb) => (
            <div key={pb.name} className={`fund-playbook ${pb.negative ? "negative" : "positive"}`}>
              <h4>
                {pb.negative ? "⚠" : "✓"} {pb.name}
              </h4>
              <div className="fund-pattern">{pb.pattern}</div>
              <p>{pb.means}</p>
              <p className="fund-caveat">{pb.watch}</p>
            </div>
          ))}
        </div>
      )}

      {data.groups.map((g) => (
        <div className="fund-group" key={g.group}>
          <h4>{g.group}</h4>
          {g.metrics.map((m) => (
            <Metric key={m.key} m={m} />
          ))}
        </div>
      ))}

      {data.data_note && <p className="fund-note">ℹ {data.data_note}</p>}
      {data.suppressed_note && <p className="fund-note">ℹ {data.suppressed_note}</p>}
      <p className="disclaimer">
        ⚠ Conventions, not rules — bands shift by sector and single ratios rarely decide
        anything. Not financial advice.
      </p>
    </div>
  );
}
