from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import market_data
from ..agents.analyst import analyze_stock
from ..agents.runner import AgentUnavailable
from ..db import AiAnalysis, WatchlistItem, get_db

router = APIRouter(prefix="/api/stocks")


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

    item = db.get(WatchlistItem, symbol)
    name = item.name if item else symbol.replace(".NS", "")
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
    result["cached"] = False
    return result
