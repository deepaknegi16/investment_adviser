from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import market_data, recommend
from ..db import WatchlistItem, get_db

router = APIRouter(prefix="/api")


class AddShare(BaseModel):
    symbol: str
    name: str


@router.get("/watchlist")
def get_watchlist(db: Session = Depends(get_db)):
    items = db.query(WatchlistItem).order_by(WatchlistItem.added_at).all()
    symbols = [i.symbol for i in items]
    if not symbols:
        return {"shares": []}
    closes_map = market_data.get_closes(symbols)
    consensus_map = market_data.get_consensus_bulk(symbols)
    shares = []
    for item in items:
        closes = closes_map.get(item.symbol)
        if closes is None or closes.empty:
            shares.append({"symbol": item.symbol, "name": item.name, "error": "no data"})
            continue
        m = market_data.compute_metrics(closes)
        consensus = consensus_map.get(item.symbol) or {}
        rec = recommend.recommendation(m, consensus.get("mean"))
        shares.append({
            "symbol": item.symbol,
            "name": item.name,
            **m,
            "status": recommend.status_color(m),
            "advice": rec["advice"],
            "consensus": consensus,
        })
    return {"shares": shares}


@router.post("/watchlist", status_code=201)
def add_share(body: AddShare, db: Session = Depends(get_db)):
    symbol = body.symbol.strip().upper()
    if not symbol.endswith(".NS"):
        raise HTTPException(400, "Only NSE symbols (ending in .NS) are supported.")
    if db.get(WatchlistItem, symbol):
        raise HTTPException(409, f"{symbol} is already in the watchlist.")
    closes = market_data.get_closes([symbol]).get(symbol)
    if closes is None or closes.empty:
        raise HTTPException(400, f"No price data found for {symbol}.")
    db.add(WatchlistItem(symbol=symbol, name=body.name.strip() or symbol))
    db.commit()
    return {"symbol": symbol, "name": body.name}


@router.delete("/watchlist/{symbol}")
def remove_share(symbol: str, db: Session = Depends(get_db)):
    item = db.get(WatchlistItem, symbol.upper())
    if not item:
        raise HTTPException(404, "Not in watchlist.")
    db.delete(item)
    db.commit()
    return {"removed": symbol.upper()}


@router.get("/search")
def search(q: str):
    if len(q.strip()) < 2:
        return {"results": []}
    return {"results": market_data.search_nse(q.strip())}
