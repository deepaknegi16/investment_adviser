"""Function tools the OpenAI agents can call, plus the web-search built-in tool.

Each tool has a plain-Python implementation (returns a JSON string) and a
matching strict JSON-schema definition for the Responses API.
"""
from __future__ import annotations

import json

from .. import market_data


def get_price_history(symbol: str, period: str = "1y") -> str:
    points = market_data.get_chart(symbol, period)
    # Thin long series so the tool result stays small.
    if len(points) > 60:
        step = len(points) // 60 + 1
        points = points[::step] + [points[-1]]
    return json.dumps({"symbol": symbol, "period": period, "closes": points})


def get_technicals(symbol: str) -> str:
    closes = market_data.get_closes([symbol]).get(symbol)
    if closes is None or closes.empty:
        return json.dumps({"error": f"no price data for {symbol}"})
    metrics = market_data.compute_metrics(closes)
    metrics["analyst_consensus"] = market_data.get_consensus(symbol)
    return json.dumps({"symbol": symbol, **metrics})


FUNCTION_TOOLS = [
    {
        "type": "function",
        "name": "get_price_history",
        "description": (
            "Get historical closing prices for an NSE stock or ETF over a period. "
            "Use for trend context beyond the summary technicals."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Yahoo Finance symbol, e.g. INFY.NS",
                },
                "period": {
                    "type": "string",
                    "enum": ["1w", "1m", "1y", "5y"],
                    "description": "History window",
                },
            },
            "required": ["symbol", "period"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_technicals",
        "description": (
            "Get current technical indicators and analyst consensus for an NSE "
            "symbol: price, day change, 1w/1m/1y/5y returns, SMA50/SMA200, RSI-14, "
            "52-week high/low, and Yahoo analyst consensus (mean rating 1=strong "
            "buy to 5=sell, price target, analyst count) when available."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Yahoo Finance symbol, e.g. INFY.NS",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
]

TOOL_IMPLS = {
    "get_price_history": get_price_history,
    "get_technicals": get_technicals,
}

WEB_SEARCH_TOOL = {"type": "web_search"}
