"""System metrics: RAG corpus health, cache state, chat quality, eval results."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import (
    AiAnalysis,
    AiPicks,
    ChatLog,
    EvalRun,
    RagChunk,
    WatchlistItem,
    get_db,
)

router = APIRouter(prefix="/api")


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    today = dt.date.today().isoformat()
    day_ago = dt.datetime.utcnow() - dt.timedelta(hours=24)

    # --- RAG corpus health ---
    chunks = db.query(RagChunk).all()
    by_source: dict = {}
    embedded = 0
    for c in chunks:
        src = c.doc_key.split(":", 1)[0]  # analysis | picks | file
        by_source[src] = by_source.get(src, 0) + 1
        if c.embedding:
            embedded += 1
    total_chunks = len(chunks)

    # --- caches ---
    analyses_total = db.query(func.count()).select_from(AiAnalysis).scalar() or 0
    analyses_today = (
        db.query(func.count()).select_from(AiAnalysis).filter(AiAnalysis.date == today).scalar() or 0
    )
    picks_days = db.query(func.count()).select_from(AiPicks).scalar() or 0
    latest_picks = db.query(func.max(AiPicks.date)).scalar()

    # --- chat quality (from chat_log) ---
    turns = db.query(ChatLog).all()
    turns_24h = [t for t in turns if t.ts and t.ts >= day_ago]
    providers: dict = {}
    modes: dict = {}
    for t in turns:
        providers[t.provider or "?"] = providers.get(t.provider or "?", 0) + 1
        modes[t.retrieval_mode or "?"] = modes.get(t.retrieval_mode or "?", 0) + 1
    scored = [t.top_score for t in turns if t.top_score is not None]
    latencies = [t.latency_ms for t in turns if t.latency_ms is not None]

    # --- latest eval run ---
    latest_eval = db.query(EvalRun).order_by(EvalRun.id.desc()).first()

    return {
        "rag": {
            "total_chunks": total_chunks,
            "by_source": by_source,
            "embedded_chunks": embedded,
            "embedding_coverage_pct": round(100 * embedded / total_chunks, 1) if total_chunks else None,
        },
        "caches": {
            "watchlist_size": db.query(func.count()).select_from(WatchlistItem).scalar() or 0,
            "analyses_cached_total": analyses_total,
            "analyses_cached_today": analyses_today,
            "picks_days_cached": picks_days,
            "latest_picks_date": latest_picks,
        },
        "chat": {
            "turns_total": len(turns),
            "turns_last_24h": len(turns_24h),
            "provider_breakdown": providers,
            "retrieval_mode_breakdown": modes,
            "avg_top_similarity": round(sum(scored) / len(scored), 3) if scored else None,
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
            "groq_fallback_rate_pct": round(
                100 * providers.get("groq", 0) / len(turns), 1
            ) if turns else None,
        },
        "eval": {
            "latest_run_at": latest_eval.ts.isoformat(timespec="seconds") if latest_eval else None,
            "results": json.loads(latest_eval.payload_json) if latest_eval else None,
            "how_to_run": "backend: .venv/bin/python eval_rag.py [--no-judge] [--judge-sample N]",
        },
    }
