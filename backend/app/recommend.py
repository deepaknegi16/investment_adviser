"""Rule-based status color and buy/hold/sell recommendation.

Deterministic and free — this is what the main table shows. The AI Analyst
Agent produces a richer recommendation on demand in the detail view.
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


def _technical_score(m: dict) -> int:
    """Sum of trend/momentum signals, roughly in [-4, +4]."""
    score = 0
    price, sma50, sma200 = m["price"], m.get("sma50"), m.get("sma200")
    if sma50 and price > sma50:
        score += 1
    if sma50 and price < sma50:
        score -= 1
    if sma200 and price > sma200:
        score += 1
    if sma200 and price < sma200:
        score -= 1
    ret_1m = m.get("ret_1m")
    if ret_1m is not None:
        score += 1 if ret_1m > 2 else (-1 if ret_1m < -2 else 0)
    rsi = m.get("rsi")
    if rsi is not None:
        if rsi > 70:
            score -= 1  # overbought
        elif rsi < 30:
            score += 1  # oversold, potential value entry
    return score


def recommendation(m: dict, consensus_mean: Optional[float]) -> dict:
    """Blend technical score with Yahoo analyst consensus (1=strong buy..5=sell)."""
    tech = _technical_score(m)
    blended = float(tech)
    if consensus_mean is not None:
        # Map consensus 1..5 onto roughly +2..-2 and add it in.
        blended += (3.0 - consensus_mean)
    if blended >= 2:
        advice = "BUY MORE"
    elif blended <= -2:
        advice = "SELL"
    else:
        advice = "HOLD"
    return {"advice": advice, "tech_score": tech, "blended_score": round(blended, 1)}
