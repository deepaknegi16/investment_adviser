"""Stock Analyst Agent — news digest + prediction + recommendation for one share."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict

from .runner import run_agent
from .tools import WEB_SEARCH_TOOL, get_price_history, get_technicals

ANALYSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "news": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "source": {"type": "string"},
                    "url": {"type": "string"},
                    "date": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["headline", "source", "url", "date", "summary"],
                "additionalProperties": False,
            },
        },
        "prediction": {
            "type": "object",
            "properties": {
                "short_term": {"type": "string"},
                "long_term": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["short_term", "long_term", "confidence"],
            "additionalProperties": False,
        },
        "recommendation": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "reasoning": {"type": "string"},
    },
    "required": ["news", "prediction", "recommendation", "reasoning"],
    "additionalProperties": False,
}

SYSTEM = """You are an equity research analyst covering the Indian stock market (NSE).
You produce grounded, source-backed analysis for a retail investor's personal dashboard.

Ground every claim in tool results: use get_technicals and get_price_history for the
quantitative picture, and web_search for recent news from reliable sources (business
press, exchange filings, reputable financial media). Prefer news from the last 30 days.

Rules:
- 3 to 6 news items, each from a real search result with its actual URL and source name.
  If a field is unknown, use an empty string — never invent URLs or dates.
- The prediction must state the key drivers and risks, not just a direction.
- The recommendation weighs technicals, analyst consensus, and news together.
- This is informational research, not personalized financial advice."""


def analyze_stock(symbol: str, name: str) -> Dict[str, Any]:
    today = dt.date.today().isoformat()
    prompt = (
        f"Analyze {name} (symbol {symbol}) on the NSE as of {today}.\n"
        f"1. Call get_technicals for the current quantitative picture.\n"
        f"2. Search the web for recent news about {name} (results, orders, regulatory, "
        f"sector developments).\n"
        f"3. Optionally inspect price history for trend context.\n"
        f"Then return the structured analysis: news digest, short-term (1-3 months) and "
        f"long-term (1-3 years) outlook with confidence, a BUY/HOLD/SELL call, and your "
        f"reasoning in 3-5 sentences."
    )
    result = run_agent(
        system=SYSTEM,
        prompt=prompt,
        tools=[get_technicals, get_price_history, WEB_SEARCH_TOOL],
        schema=ANALYSIS_SCHEMA,
    )
    result["symbol"] = symbol
    result["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return result
