"""Cross-sectional conviction scoring for position sizing.

This replaces the first sizing model, which measurement showed was not usable:

  * **17% one-way turnover per DAY.** Conviction was built from step functions —
    "+1 if price > SMA50", "+1 if 1-month return > 2%", "-1 if RSI > 70". A stock
    hovering at its 50-day average flips a whole point back and forth on noise,
    and the weights swing with it. At Indian brokerage plus STT, and with gains
    under 12 months taxed at slab rate, a column that implies rebalancing 17% of
    a portfolio daily is worse than no column.
  * **Analyst consensus was the single loudest input** (mean |contribution| 1.11,
    above every technical factor) despite being one Yahoo field, and despite the
    evidence for rating *levels* being weak — sell-side ratings are heavily
    skewed to buy, and it is revisions rather than levels that carry signal.
  * **Fundamentals contributed nothing** to the score — they were a ±20% tilt
    applied after the fact — even though value and profitability are the two
    best-documented cross-sectional equity premia.

The fix is to score the way factor models actually do: **continuous
cross-sectional percentile ranks within the basket**, combined with weights that
reflect the strength of the published evidence rather than what was easy to
compute.

Weights and their basis (all long-documented, all cross-sectional equity
premia):

| Component | Weight | Why |
|---|---|---|
| Momentum, 12-month excluding the last month | 25% | Jegadeesh & Titman (1993); Asness, Moskowitz & Pedersen (2013). One of the most replicated anomalies. The recent month is skipped deliberately — at 1-month horizons the effect *reverses* |
| Quality / profitability (ROE, ROA, margins) | 25% | Novy-Marx (2013); Fama-French RMW (2015) |
| Value (earnings, book, EV/EBITDA yields) | 25% | Fama-French HML (1992) |
| Long-term trend (price vs 200-day) | 15% | Slow-moving and stable; the trend signal that does not thrash |
| Analyst consensus | 10% | Womack (1996) finds signal in revisions, far less in levels. Deliberately the smallest weight, not the largest |

Two things are deliberately NOT in conviction:

* **1-month momentum**, which the old model scored positively. Short-horizon
  reversal (Jegadeesh 1990) means the sign of that evidence is arguably the
  opposite; rather than bet either way it is dropped.
* **Low volatility**, which is real (Frazzini & Pedersen 2014) but is already the
  divisor in the risk-parity step. Scoring it here too would double-count it.

Nothing here gives a name a higher score for already being held. Conviction is a
function of the metrics only, so the same company scores identically whether it
sits in the watchlist or the screener.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

WEIGHTS: Dict[str, float] = {
    "momentum": 0.25,
    "quality": 0.25,
    "value": 0.25,
    "trend": 0.15,
    "consensus": 0.10,
}

COMPONENT_LABEL = {
    "momentum": "Momentum (12m ex-1m)",
    "quality": "Quality / profitability",
    "value": "Value",
    "trend": "Long-term trend",
    "consensus": "Analyst consensus",
}


def _pct_rank(values: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Percentile rank within the basket, mapped to -1..+1.

    Cross-sectional ranking is what makes this stable: a small price move
    changes a rank slightly instead of flipping a threshold, and it makes
    unlike quantities (a P/E and a 12-month return) directly comparable.
    """
    known = {k: v for k, v in values.items() if v is not None}
    if len(known) < 2:
        return {k: (0.0 if k in known else None) for k in values}
    order = sorted(known, key=lambda k: known[k])
    n = len(order) - 1
    out: Dict[str, Optional[float]] = {k: None for k in values}
    for i, k in enumerate(order):
        out[k] = round((i / n) * 2 - 1, 4)
    return out


def _composite(parts: List[Dict[str, Optional[float]]], symbols: List[str]) -> Dict[str, Optional[float]]:
    """Average whichever sub-signals exist for each symbol."""
    out: Dict[str, Optional[float]] = {}
    for s in symbols:
        vals = [p.get(s) for p in parts if p.get(s) is not None]
        out[s] = round(sum(vals) / len(vals), 4) if vals else None
    return out


def score_basket(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Conviction 0..1 per symbol, with the component breakdown behind it.

    Each item: {symbol, ret_1y?, ret_1m?, price?, sma200?, consensus_mean?,
                fundamentals?: {roe, roa, profit_margin, trailing_pe,
                                price_to_book, ev_to_ebitda}}
    """
    syms = [it["symbol"] for it in items]
    by = {it["symbol"]: it for it in items}

    # --- momentum: 12-month return excluding the most recent month ---
    mom_raw: Dict[str, Optional[float]] = {}
    for s in syms:
        r1y, r1m = by[s].get("ret_1y"), by[s].get("ret_1m")
        if r1y is None:
            mom_raw[s] = None
        elif r1m is None:
            mom_raw[s] = r1y
        else:
            # Approximate 12m-ex-1m by removing the last month's contribution.
            mom_raw[s] = ((1 + r1y / 100) / (1 + r1m / 100) - 1) * 100
    momentum = _pct_rank(mom_raw)

    # --- trend: distance above/below the 200-day average ---
    trend_raw = {}
    for s in syms:
        price, sma = by[s].get("price"), by[s].get("sma200")
        trend_raw[s] = ((price / sma - 1) * 100) if (price and sma) else None
    trend = _pct_rank(trend_raw)

    # --- quality: ROE, ROA and net margin, ranked and averaged ---
    def fund(s: str, key: str) -> Optional[float]:
        f = by[s].get("fundamentals") or {}
        return f.get(key)

    quality = _composite(
        [_pct_rank({s: fund(s, k) for s in syms}) for k in ("roe", "roa", "profit_margin")],
        syms,
    )

    # --- value: yields (inverted ratios) so higher is always cheaper ---
    def inv(s: str, key: str) -> Optional[float]:
        v = fund(s, key)
        return (1.0 / v) if (v is not None and v > 0) else None

    value = _composite(
        [_pct_rank({s: inv(s, k) for s in syms})
         for k in ("trailing_pe", "price_to_book", "ev_to_ebitda")],
        syms,
    )

    # --- consensus: 1 (strong buy) .. 5 (sell) -> +1 .. -1 ---
    consensus = {}
    for s in syms:
        mean = by[s].get("consensus_mean")
        consensus[s] = max(-1.0, min(1.0, (3.0 - mean) / 2.0)) if mean is not None else None

    components = {"momentum": momentum, "quality": quality, "value": value,
                  "trend": trend, "consensus": consensus}

    out: Dict[str, Dict[str, Any]] = {}
    for s in syms:
        present = {k: v[s] for k, v in components.items() if v.get(s) is not None}
        if not present:
            out[s] = {"conviction": 0.0, "components": {}, "coverage": 0.0}
            continue
        wsum = sum(WEIGHTS[k] for k in present)
        raw = sum(WEIGHTS[k] * present[k] for k in present) / wsum  # -1..+1
        # Shrink toward neutral when signals are missing. GOLDBEES has no
        # fundamentals at all, so half its weighting scheme is unavailable —
        # scoring it as confidently as a name with full coverage would let two
        # signals stand in for five. Shrinkage toward the mean under uncertainty
        # is the standard treatment (James-Stein); here it is proportional to
        # how much of the weighting scheme actually resolved.
        raw *= wsum ** 0.5
        out[s] = {
            # Map -1..+1 onto 0..1, so a bottom-half name still gets a small
            # weight rather than the old all-or-nothing cliff at zero.
            "conviction": round(max(0.0, min(1.0, (raw + 1) / 2)), 3),
            "raw": round(raw, 3),
            "components": {k: round(v, 3) for k, v in present.items()},
            "coverage": round(wsum, 2),
        }
    return out


def explain(entry: Dict[str, Any]) -> str:
    """One line naming the components that actually drove this score."""
    comps = entry.get("components") or {}
    if not comps:
        return "No usable signals."
    ranked = sorted(comps.items(), key=lambda kv: -abs(kv[1] * WEIGHTS[kv[0]]))[:3]
    bits = [
        f"{COMPONENT_LABEL[k]} {'strong' if v > 0.33 else ('weak' if v < -0.33 else 'middling')}"
        for k, v in ranked
    ]
    return "; ".join(bits) + f" (basket-relative, {int(entry.get('coverage', 0) * 100)}% of signals available)."
