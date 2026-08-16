from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import rag
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
