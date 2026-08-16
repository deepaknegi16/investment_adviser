"""Client-side tools the Claude agents can call, plus the web-search server tool."""
from __future__ import annotations

import json

from anthropic import beta_tool

from .. import market_data


@beta_tool
def get_price_history(symbol: str, period: str = "1y") -> str:
    """Get historical closing prices for an NSE stock or ETF.

    Args:
        symbol: Yahoo Finance symbol, e.g. "INFY.NS".
        period: One of "1w", "1m", "1y", "5y".
    """
    points = market_data.get_chart(symbol, period)
    # Thin long series so the tool result stays small.
    if len(points) > 60:
        step = len(points) // 60 + 1
        points = points[::step] + [points[-1]]
    return json.dumps({"symbol": symbol, "period": period, "closes": points})


@beta_tool
def get_technicals(symbol: str) -> str:
    """Get current technical indicators and analyst consensus for an NSE symbol.

    Returns price, day change, 1w/1m/1y/5y returns, SMA50/SMA200, RSI-14,
    52-week high/low, and Yahoo analyst consensus (mean rating 1=strong buy
    to 5=sell, price target, analyst count) when available.

    Args:
        symbol: Yahoo Finance symbol, e.g. "INFY.NS".
    """
    closes = market_data.get_closes([symbol]).get(symbol)
    if closes is None or closes.empty:
        return json.dumps({"error": f"no price data for {symbol}"})
    metrics = market_data.compute_metrics(closes)
    metrics["analyst_consensus"] = market_data.get_consensus(symbol)
    return json.dumps({"symbol": symbol, **metrics})


WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 6,
    "user_location": {"type": "approximate", "country": "IN", "timezone": "Asia/Kolkata"},
}
