"""Screener Agent — top-20 picks from the Nifty 100 universe.

Two stages keep the free-tier quota spend minimal:
1. Deterministic pre-screen (free, no AI): momentum/trend score over the full
   universe, keep the top 30 candidates with all their metrics attached.
2. One structured-synthesis call: Gemini Flash-Lite ranks the candidates into
   a top 20 with rationale (Groq fallback if Gemini is rate-limited).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .. import market_data
from .runner import structured_synthesis

# Bulk ranking over pre-scored candidates: cheapest capable free-tier model.
SCREENER_MODEL = os.environ.get("SCREENER_MODEL", "gemini-3.5-flash-lite")

UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "nifty100.json"

PICKS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "name": {"type": "string"},
                    "symbol": {"type": "string"},
                    "recommendation": {"type": "string", "enum": ["BUY", "HOLD"]},
                    "rationale": {"type": "string"},
                },
                "required": ["rank", "name", "symbol", "recommendation", "rationale"],
            },
        },
        "market_note": {"type": "string"},
    },
    "required": ["picks", "market_note"],
}

SYSTEM = """You are an equity screener for the Indian market (NSE), selecting the 20 most
promising large-cap shares for a retail investor's watch table.

You receive pre-screened candidates with momentum and trend metrics already computed.
Rank them on the combination of: established trend quality (price vs SMAs), momentum
sustainability (1m vs 1y returns, RSI not overheated), and distance from the 52-week
high (room to run vs chasing a top).

Rules:
- Return exactly 20 picks, ranked 1 (best) to 20, drawn only from the given candidates.
- Each rationale is one concise sentence naming the concrete driver in the metrics.
- Add a one-sentence market_note summarizing what the candidate set says about the market.
- This is informational research, not personalized financial advice."""


def _load_universe() -> List[dict]:
    return json.loads(UNIVERSE_PATH.read_text())


def _prescreen(top_n: int = 30) -> List[dict]:
    universe = _load_universe()
    by_symbol = {u["symbol"]: u["name"] for u in universe}
    closes_map = market_data.get_closes(list(by_symbol))
    scored = []
    for symbol, closes in closes_map.items():
        m = market_data.compute_metrics(closes)
        if m.get("ret_1m") is None or m.get("ret_1y") is None:
            continue
        trend_bonus = 0.0
        if m.get("sma50") and m.get("sma200") and m["price"] > m["sma50"] > m["sma200"]:
            trend_bonus = 10.0
        score = m["ret_1m"] + m["ret_1y"] / 6 + trend_bonus
        scored.append({"symbol": symbol, "name": by_symbol[symbol], "score": round(score, 1), **m})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def screen_top_picks() -> Dict[str, Any]:
    candidates = _prescreen()
    if len(candidates) < 20:
        raise RuntimeError("Pre-screen produced too few candidates (market data issue).")
    lines = [
        f"- {c['name']} ({c['symbol']}): price {c['price']}, 1m {c['ret_1m']}%, "
        f"1y {c['ret_1y']}%, 5y {c.get('ret_5y')}%, RSI {c.get('rsi')}, "
        f"price vs SMA50/SMA200: {c['price']}/{c.get('sma50')}/{c.get('sma200')}, "
        f"vs 52w-high {c.get('pct_from_high52')}%, screen-score {c['score']}"
        for c in candidates
    ]
    prompt = (
        f"Today is {dt.date.today().isoformat()}. Here are the top {len(candidates)} "
        f"momentum/trend candidates from the NSE large-cap universe:\n\n"
        + "\n".join(lines)
        + "\n\nSelect and rank the 20 best picks."
    )
    result = structured_synthesis(
        model=SCREENER_MODEL,
        system=SYSTEM,
        prompt=prompt,
        schema=PICKS_SCHEMA,
        groq_fallback=True,
    )
    # Attach live table metrics to each pick for display.
    metrics_by_symbol = {c["symbol"]: c for c in candidates}
    for pick in result.get("picks", []):
        m = metrics_by_symbol.get(pick.get("symbol"), {})
        pick["price"] = m.get("price")
        pick["ret_1m"] = m.get("ret_1m")
        pick["ret_1y"] = m.get("ret_1y")
    result["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return result
