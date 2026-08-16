from __future__ import annotations

import datetime as dt
import json

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import market_data, rag, recommend
from ..agents.analyst import analyze_stock
from ..agents.runner import AgentUnavailable
from ..db import AiAnalysis, WatchlistItem, get_db

router = APIRouter(prefix="/api/stocks")

_UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "nifty100.json"


def _resolve_name(symbol: str, db: Session) -> str:
    item = db.get(WatchlistItem, symbol)
    if item:
        return item.name
    try:
        for entry in json.loads(_UNIVERSE_PATH.read_text()):
            if entry["symbol"] == symbol:
                return entry["name"]
    except Exception:
        pass
    return symbol.replace(".NS", "")


@router.get("/{symbol}/summary")
def summary(symbol: str, db: Session = Depends(get_db)):
    """Full metrics + explainable advice for any NSE symbol (watchlist or not)."""
    symbol = symbol.upper()
    closes = market_data.get_closes([symbol]).get(symbol)
    if closes is None or closes.empty:
        raise HTTPException(404, f"No price data for {symbol}.")
    m = market_data.compute_metrics(closes)
    consensus = market_data.get_consensus(symbol)
    rec = recommend.recommendation(m, consensus.get("mean"))
    return {
        "symbol": symbol,
        "name": _resolve_name(symbol, db),
        **m,
        "status": recommend.status_color(m),
        "advice": rec["advice"],
        "advice_logic": rec["logic"],
        "consensus": consensus,
        "in_watchlist": db.get(WatchlistItem, symbol) is not None,
    }


@router.get("/{symbol}/history")
def history(symbol: str, period: str = "1y"):
    if period not in ("1w", "1m", "1y", "5y"):
        raise HTTPException(400, "period must be one of 1w, 1m, 1y, 5y")
    points = market_data.get_chart(symbol.upper(), period)
    if not points:
        raise HTTPException(404, f"No history for {symbol}")
    return {"symbol": symbol.upper(), "period": period, "points": points}


@router.get("/{symbol}/analysis")
def analysis(symbol: str, refresh: bool = False, db: Session = Depends(get_db)):
    symbol = symbol.upper()
    today = dt.date.today().isoformat()
    if not refresh:
        cached = (
            db.query(AiAnalysis)
            .filter(AiAnalysis.symbol == symbol)
            .order_by(AiAnalysis.date.desc())
            .first()
        )
        if cached:
            payload = json.loads(cached.payload_json)
            payload["cached"] = cached.date != today
            return payload

    name = _resolve_name(symbol, db)
    try:
        result = analyze_stock(symbol, name)
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))

    row = db.get(AiAnalysis, (symbol, today))
    if row:
        row.payload_json = json.dumps(result)
    else:
        db.add(AiAnalysis(symbol=symbol, date=today, payload_json=json.dumps(result)))
    db.commit()
    try:
        rag.index_analysis(symbol, name, result)  # feed the chat's RAG index
    except Exception:
        pass  # indexing must never break the analysis response
    result["cached"] = False
    return result
