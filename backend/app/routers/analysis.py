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


HOLDERS_TTL_DAYS = 30


@router.get("/{symbol}/holders")
def holders(symbol: str, refresh: bool = False, db: Session = Depends(get_db)):
    """Major shareholders: AI grounded lookup (30-day cache) with Yahoo fallback."""
    from ..agents.holders import fetch_big_holders
    from ..db import AiHolders

    symbol = symbol.upper()
    structural = (market_data.get_consensus(symbol) or {}).get("ownership")

    if not refresh:
        row = db.get(AiHolders, symbol)
        if row:
            age = (dt.date.today() - dt.date.fromisoformat(row.date)).days
            if age <= HOLDERS_TTL_DAYS:
                payload = json.loads(row.payload_json)
                payload["structural"] = structural
                payload["cached"] = age > 0
                return payload

    name = _resolve_name(symbol, db)
    try:
        result = fetch_big_holders(symbol, name)
        row = db.get(AiHolders, symbol)
        if row:
            row.date = dt.date.today().isoformat()
            row.payload_json = json.dumps(result)
        else:
            db.add(AiHolders(symbol=symbol, date=dt.date.today().isoformat(),
                             payload_json=json.dumps(result)))
        db.commit()
        result["structural"] = structural
        result["cached"] = False
        return result
    except AgentUnavailable as e:
        # Degrade to Yahoo's (sparse for NSE) named holders + structural split.
        named = market_data.get_named_holders(symbol)
        return {
            "symbol": symbol,
            "source": "yahoo",
            "holders": [
                {
                    "name": h["name"], "category": "other",
                    "pct_of_company": h["pct"], "shares": str(h["shares"] or ""),
                    "note": f"as of {h['as_of']}" if h.get("as_of") else "",
                }
                for h in named
            ],
            "as_of": named[0]["as_of"] if named else "",
            "summary": f"AI lookup unavailable ({e}); showing Yahoo institutional data"
                       + (" (none available for this NSE symbol)" if not named else " — may be dated"),
            "structural": structural,
            "cached": False,
        }
