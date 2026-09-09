from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import allocation, market_data, rag
from ..agents.runner import AgentUnavailable
from ..agents.screener import screen_top_picks
from ..db import AiPicks, get_db

router = APIRouter(prefix="/api")


@router.get("/picks")
def picks(refresh: bool = False, db: Session = Depends(get_db)):
    today = dt.date.today().isoformat()
    if not refresh:
        cached = db.query(AiPicks).order_by(AiPicks.date.desc()).first()
        if cached:
            payload = json.loads(cached.payload_json)
            if not (payload.get("picks") or [{}])[0].get("suggested_pct"):
                _attach_allocation(payload)  # cached before sizing existed
            payload["cached"] = cached.date != today
            return payload

    try:
        result = screen_top_picks()
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    row = db.get(AiPicks, today)
    if row:
        row.payload_json = json.dumps(result)
    else:
        db.add(AiPicks(date=today, payload_json=json.dumps(result)))
    db.commit()
    try:
        rag.index_picks(result)  # feed the chat's RAG index
    except Exception:
        pass  # indexing must never break the picks response
    _attach_allocation(result)
    result["cached"] = False
    return result


def _attach_allocation(result: dict) -> None:
    """Suggested weights across the top-20, sized by risk and screen rank.

    The screener returns a rank and a BUY/HOLD call but no score, so conviction
    comes from the rank itself: rank 1 is the strongest name the pre-screen
    found, rank 20 the weakest that still made the list.
    """
    picks = result.get("picks") or []
    if not picks:
        return
    symbols = [p["symbol"] for p in picks if p.get("symbol")]
    closes_map = market_data.get_closes(symbols) if symbols else {}

    items = []
    n = len(picks)
    for p in picks:
        sym = p.get("symbol")
        if not sym:
            continue
        closes = closes_map.get(sym)
        vol = market_data._ann_vol(closes) if closes is not None and not closes.empty else None
        rank = p.get("rank") or n
        # Rank 1 -> ~5.0, rank 20 -> ~1.0 on the same scale the watchlist's
        # blended score uses, so both tables size positions the same way.
        score = 5.0 - 4.0 * ((rank - 1) / max(1, n - 1))
        if p.get("recommendation") == "HOLD":
            score -= 1.5
        items.append({
            "symbol": sym,
            "blended_score": round(score, 2),
            "ann_vol": vol,
            "sector": market_data.cached_sector(sym),
        })

    alloc = allocation.suggest(items)
    for p in picks:
        info = alloc["per_symbol"].get(p.get("symbol"), {})
        p["suggested_pct"] = info.get("suggested_pct", 0.0)
        p["suggested_why"] = info.get("reason")
        p["ann_vol"] = info.get("ann_vol")
    result["allocation"] = {
        "cash_pct": alloc["cash_pct"],
        "warnings": alloc["warnings"],
        "basis": alloc["basis"],
    }
