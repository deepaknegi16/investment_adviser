"""One allocation across everything you could hold.

The first version sized the watchlist and the screener picks as two independent
baskets. Each summed to 100%, which meant the two tables together implied 190%
of a portfolio, and a stock appearing in both — HAL — was shown at 15% in one
and 5% in the other. Two answers for one holding is not a rounding problem; it
is two models.

There is only one pot of money, so there is now one allocation. Held names and
screener candidates compete in the same cross-section, which is also what makes
the "don't favour something just because I already own it" property real: a
holding that scores poorly loses weight to a candidate that scores well, and the
same symbol gets one number wherever it is displayed.
"""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import allocation, market_data
from ..db import AiPicks, WatchlistItem, get_db

router = APIRouter(prefix="/api")


@router.get("/allocation")
def portfolio_allocation(db: Session = Depends(get_db)):
    """Suggested weights over the union of the watchlist and today's picks."""
    held = {i.symbol: i.name for i in db.query(WatchlistItem).all()}

    picks: dict = {}
    row = db.query(AiPicks).order_by(AiPicks.date.desc()).first()
    if row:
        for p in (json.loads(row.payload_json).get("picks") or []):
            if p.get("symbol"):
                picks[p["symbol"]] = p.get("name") or p["symbol"]

    symbols = sorted(set(held) | set(picks))
    if not symbols:
        return {"weights": {}, "cash_pct": 100.0, "per_symbol": {}, "warnings": [],
                "basis": {}, "universe": {"held": [], "candidates": []}}

    closes_map = market_data.get_closes(symbols)
    cons_map = market_data.get_consensus_bulk(symbols)
    market_data.warm_fundamentals(symbols)

    items = []
    for sym in symbols:
        closes = closes_map.get(sym)
        if closes is None or closes.empty:
            continue
        m = market_data.compute_metrics(closes)
        f = market_data.cached_fundamentals(sym) or {}
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

    result = allocation.suggest(items)
    result["universe"] = {
        "held": sorted(held),
        "candidates": sorted(set(picks) - set(held)),
        "n_scored": len(items),
    }
    # Selection-effect disclosure. The candidates are not a neutral comparison
    # set: the screener picked them from ~110 names on a momentum/trend
    # pre-screen, and momentum + trend are 40% of the conviction weighting. They
    # are therefore expected to out-score an unfiltered watchlist on the very
    # axes they were selected for. Saying so matters, because without it the
    # output reads as "sell most of what you own".
    candidates = set(picks) - set(held)
    held_w = sum(v for k, v in result["weights"].items() if k in held)
    cand_w = sum(v for k, v in result["weights"].items() if k in candidates)
    if candidates and cand_w > held_w:
        result["warnings"].insert(0, (
            f"Screener candidates take {cand_w:.0f}% versus {held_w:.0f}% for your "
            f"holdings — but that comparison is not neutral. The candidates were "
            f"selected from ~110 names by a momentum and trend pre-screen, and "
            f"momentum plus trend are 40% of the conviction weighting, so they are "
            f"expected to score well on exactly the axes that chose them. Read this "
            f"as a shortlist worth researching, not as an instruction to rotate out "
            f"of what you own."
        ))

    result["basis"]["scope"] = (
        f"One allocation across {len(items)} names — {len(held)} you hold plus "
        f"{len(set(picks) - set(held))} screener candidates. Both tables read "
        f"these same weights, so the two columns together sum to 100%, and a "
        f"stock in both lists shows one number."
    )
    return result
