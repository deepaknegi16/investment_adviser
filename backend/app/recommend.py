"""Rule-based status color and buy/hold/sell recommendation.

Deterministic and free — this is what the main table shows. Every signal is
returned as an explainable factor so the UI can show *why* the advice is what
it is. The AI Analyst Agent produces a richer recommendation on demand.
"""
from __future__ import annotations

from typing import Optional


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
