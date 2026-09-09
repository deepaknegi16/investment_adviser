from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import rag, smallcap
from ..agents.runner import AgentUnavailable
from ..agents.screener import screen_top_picks
from ..db import AiPicks, SmallCapScreen, get_db

router = APIRouter(prefix="/api")


@router.get("/picks")
def picks(refresh: bool = False, db: Session = Depends(get_db)):
    today = dt.date.today().isoformat()
    if not refresh:
        cached = db.query(AiPicks).order_by(AiPicks.date.desc()).first()
        if cached:
            payload = json.loads(cached.payload_json)
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
    result["cached"] = False
    return result


@router.get("/smallcaps")
def smallcaps(refresh: bool = False, db: Session = Depends(get_db)):
    """Small/mid caps that clear a hard fundamental and liquidity base.

    Separate from /picks on purpose: that screens the Nifty 100 on momentum,
    where the smallest name is ~Rs 3 lakh crore. This one starts from a quality
    and tradability floor and only ranks what survives it.
    """
    today = dt.date.today().isoformat()
    if not refresh:
        row = db.query(SmallCapScreen).order_by(SmallCapScreen.date.desc()).first()
        if row:
            payload = json.loads(row.payload_json)
            payload["cached"] = row.date != today
            payload["as_of"] = row.date
            return payload

    result = smallcap.screen()
    row = db.get(SmallCapScreen, today)
    if row:
        row.payload_json = json.dumps(result)
    else:
        db.add(SmallCapScreen(date=today, payload_json=json.dumps(result)))
    db.commit()
    result["cached"] = False
    result["as_of"] = today
    return result
