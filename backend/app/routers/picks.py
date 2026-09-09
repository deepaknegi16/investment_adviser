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
    """Suggested weights across the top-20.

    Scored on the same research-weighted factors as the watchlist rather than on
    the screener's rank, so a name sizes identically in both tables. The screen
    decides *which* twenty; conviction decides how much of each.
    """
    picks = result.get("picks") or []
    if not picks:
        return
    symbols = [p["symbol"] for p in picks if p.get("symbol")]
    closes_map = market_data.get_closes(symbols) if symbols else {}
    cons_map = market_data.get_consensus_bulk(symbols) if symbols else {}
    # On-demand endpoint (not the 60 s poll), and fundamentals are cached 24 h,
    # so paying for them once here is what lets value and quality count for the
    # picks table at all.
    fund_map = market_data.get_fundamentals_bulk(symbols) if symbols else {}

    items = []
    for p in picks:
        sym = p.get("symbol")
        if not sym:
            continue
        closes = closes_map.get(sym)
        m = market_data.compute_metrics(closes) if closes is not None and not closes.empty else {}
        f = fund_map.get(sym) or {}
        items.append({
            "symbol": sym,
            "price": m.get("price"),
            "sma200": m.get("sma200"),
            "ret_1y": m.get("ret_1y"),
            "ret_1m": m.get("ret_1m"),
            "ann_vol": m.get("ann_vol"),
            "consensus_mean": (cons_map.get(sym) or {}).get("mean"),
            "sector": f.get("sector"),
            "fundamentals": f.get("metrics"),
        })

    alloc = allocation.suggest(items)
    for p in picks:
        info = alloc["per_symbol"].get(p.get("symbol"), {})
        p["suggested_pct"] = info.get("suggested_pct", 0.0)
        p["suggested_why"] = info.get("reason")
        p["suggested_signal"] = info.get("signal")
        p["ann_vol"] = info.get("ann_vol")
    result["allocation"] = {
        "cash_pct": alloc["cash_pct"],
        "warnings": alloc["warnings"],
        "basis": alloc["basis"],
    }
