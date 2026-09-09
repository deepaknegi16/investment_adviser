"""Rule-based status color and buy/hold/sell recommendation.

Deterministic and free — this is what the main table shows. Every signal is
returned as an explainable factor so the UI can show *why* the advice is what
it is. The AI Analyst Agent produces a richer recommendation on demand.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import fundamentals as fnd


def status_color(m: dict) -> str:
    """GREEN = clear uptrend, RED = clear downtrend, ORANGE = mixed."""
    price, sma50, sma200 = m["price"], m.get("sma50"), m.get("sma200")
    ret_1m = m.get("ret_1m")
    if sma50 is None or sma200 is None or ret_1m is None:
        return "orange"
    if price > sma50 > sma200 and ret_1m > 0:
        return "green"
    if price < sma50 and price < sma200 and ret_1m < 0:
        return "red"
    return "orange"


def _technical_factors(m: dict) -> list:
    """Each trend/momentum signal with its score contribution and explanation."""
    factors = []
    price, sma50, sma200 = m["price"], m.get("sma50"), m.get("sma200")

    if sma50 is not None:
        above = price > sma50
        factors.append({
            "factor": "Price vs 50-day average",
            "score": 1 if above else -1,
            "detail": f"₹{price} is {'above' if above else 'below'} the 50-day average (₹{sma50}) — {'medium-term uptrend' if above else 'medium-term weakness'}.",
        })
    if sma200 is not None:
        above = price > sma200
        factors.append({
            "factor": "Price vs 200-day average",
            "score": 1 if above else -1,
            "detail": f"₹{price} is {'above' if above else 'below'} the 200-day average (₹{sma200}) — {'long-term uptrend intact' if above else 'long-term downtrend'}.",
        })
    ret_1m = m.get("ret_1m")
    if ret_1m is not None:
        score = 1 if ret_1m > 2 else (-1 if ret_1m < -2 else 0)
        label = "strong" if score == 1 else ("weak" if score == -1 else "flat")
        factors.append({
            "factor": "1-month momentum",
            "score": score,
            "detail": f"{ret_1m:+}% over the last month — {label} recent momentum (±2% is the neutral band).",
        })
    rsi = m.get("rsi")
    if rsi is not None:
        if rsi > 70:
            score, label = -1, f"overbought (RSI {rsi} > 70) — elevated pullback risk"
        elif rsi < 30:
            score, label = 1, f"oversold (RSI {rsi} < 30) — potential value entry"
        else:
            score, label = 0, f"neutral (RSI {rsi}, between 30 and 70)"
        factors.append({"factor": "RSI (14-day)", "score": score, "detail": label})
    return factors


def recommendation(m: dict, consensus_mean: Optional[float]) -> dict:
    """Blend technical factors with Yahoo analyst consensus (1=strong buy..5=sell).

    Returns the advice plus the full explainable breakdown (`logic`).
    """
    factors = _technical_factors(m)
    tech = sum(f["score"] for f in factors)
    consensus_score = 0.0
    if consensus_mean is not None:
        # Map consensus 1..5 onto roughly +2..-2 and add it in.
        consensus_score = round(3.0 - consensus_mean, 1)
        factors.append({
            "factor": "Analyst consensus",
            "score": consensus_score,
            "detail": f"Wall-Street-style mean rating {consensus_mean} on a 1 (strong buy) to 5 (sell) scale, contributing {consensus_score:+}.",
        })
    blended = round(tech + consensus_score, 1)
    if blended >= 2:
        advice = "BUY MORE"
    elif blended <= -2:
        advice = "SELL"
    else:
        advice = "HOLD"
    return {
        "advice": advice,
        "tech_score": tech,
        "blended_score": blended,
        "logic": {
            "factors": factors,
            "tech_score": tech,
            "consensus_score": consensus_score,
            "blended_score": blended,
            "rule": "Total ≥ +2 → BUY MORE · total ≤ −2 → SELL · otherwise HOLD.",
        },
    }


# --------------------------------------------------------------- fundamentals

# How each catalogue group rolls up into a headline pillar.
PILLARS = {
    "Valuation": "value",
    "Profitability": "quality",
    "Growth": "growth",
    "Financial health": "safety",
    "Income": "income",
}


def fundamental_report(fundamentals: dict) -> dict:
    """Turn raw fundamental metrics into an explained, grouped scorecard.

    Mirrors the technical `logic` shape: every number carries what it means, how
    to read it, and whether it currently reads well — so the UI never shows a
    bare ratio the reader has to look up.
    """
    if not fundamentals.get("available"):
        return {"available": False, "reason": fundamentals.get("reason", "")}

    values = fundamentals.get("metrics", {})
    family = fnd.sector_family(fundamentals.get("sector"))
    suppressed = fnd.SUPPRESSED.get(family, set())
    repaired = set(fundamentals.get("repaired") or [])

    groups: Dict[str, List[Dict[str, Any]]] = {}
    pillar_scores: Dict[str, Dict[str, int]] = {}

    for key, meta in fnd.CATALOGUE.items():
        if key in suppressed:
            continue
        value = values.get(key)
        if value is None:
            continue
        v = fnd.verdict(key, value, family)
        band = fnd.band_for(key, family)
        entry = {
            "key": key,
            "label": meta["label"],
            "value": value,
            "unit": meta["unit"],
            "verdict": v,                      # good | ok | watch | None
            "what": meta["what"],
            "read": meta["read"],
            "caveat": meta.get("caveat"),
            "band": band or None,
            "repaired": key in repaired,
        }
        groups.setdefault(meta["group"], []).append(entry)

        pillar = PILLARS.get(meta["group"])
        if pillar and v:
            bucket = pillar_scores.setdefault(pillar, {"good": 0, "ok": 0, "watch": 0})
            bucket[v] += 1

    pillars = {}
    for name, counts in pillar_scores.items():
        total = sum(counts.values())
        net = counts["good"] - counts["watch"]
        pillars[name] = {
            **counts,
            "n": total,
            "net": net,
            "rating": "strong" if net >= 2 else ("weak" if net <= -2 else "mixed"),
        }

    matched = fnd.match_playbooks(values)
    return {
        "available": True,
        "sector": fundamentals.get("sector"),
        "industry": fundamentals.get("industry"),
        "sector_family": family,
        "groups": [
            {"group": g, "metrics": sorted(items, key=lambda x: x["label"])}
            for g, items in groups.items()
        ],
        "pillars": pillars,
        "playbooks": matched,
        "suppressed": sorted(suppressed),
        "suppressed_note": (
            "Debt, liquidity and operating-margin ratios are hidden for lenders: "
            "borrowing is their raw material, not a risk signal, so the usual "
            "thresholds would be misleading."
            if suppressed else None
        ),
        "data_note": (
            f"Yahoo reports this company's financials in "
            f"{fundamentals.get('financial_currency')} while quoting the share in "
            f"{fundamentals.get('quote_currency')}. Ratios mixing the two "
            f"({', '.join(sorted(repaired))}) have been rescaled at USDINR "
            f"{fundamentals.get('usdinr_used')} — Yahoo's own site shows these "
            f"uncorrected."
            if repaired else None
        ),
    }


def fundamental_factors(report: dict) -> List[Dict[str, Any]]:
    """Scoreable factors from the pillars, for blending into the advice."""
    if not report.get("available"):
        return []
    factors = []
    labels = {
        "value": ("Valuation", "cheap relative to earnings and assets", "expensive on most measures"),
        "quality": ("Profitability", "earns well on its capital", "thin returns on capital"),
        "growth": ("Growth", "revenue and profit expanding", "revenue or profit shrinking"),
        "safety": ("Balance sheet", "comfortable debt and liquidity", "stretched balance sheet"),
        "income": ("Dividend", "well-covered yield", "little or poorly-covered income"),
    }
    for pillar, data in report.get("pillars", {}).items():
        label, good_text, bad_text = labels.get(pillar, (pillar, "favourable", "unfavourable"))
        net = data["net"]
        score = 1 if net >= 2 else (-1 if net <= -2 else 0)
        text = good_text if score > 0 else (bad_text if score < 0 else "mixed signals")
        factors.append({
            "factor": f"{label} (fundamentals)",
            "score": score,
            "detail": f"{data['good']} of {data['n']} metrics read well, "
                      f"{data['watch']} flagged — {text}.",
        })
    return factors
