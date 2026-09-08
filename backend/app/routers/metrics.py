"""System metrics: RAG corpus health, cache state, chat quality, eval results."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import notify
from ..db import (
    AiAnalysis,
    AiPicks,
    ChatLog,
    EvalRun,
    GoldEvent,
    GoldWatchRun,
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

    # --- gold watch ---
    gold_runs = db.query(GoldWatchRun).order_by(GoldWatchRun.id.desc()).all()
    gold_runs_24h = [r for r in gold_runs if r.ts and r.ts >= day_ago]
    gold_emailed = [r for r in gold_runs if r.emailed]
    gold_events = db.query(GoldEvent).all()
    by_factor: dict = {}
    by_impact: dict = {}
    for e in gold_events:
        by_factor[e.factor or "?"] = by_factor.get(e.factor or "?", 0) + 1
        by_impact[e.impact or "?"] = by_impact.get(e.impact or "?", 0) + 1
    last_gold = gold_runs[0] if gold_runs else None
    email_cfg = notify.config_status()

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
        "gold_watch": {
            "runs_total": len(gold_runs),
            "runs_last_24h": len(gold_runs_24h),
            "last_run_at": last_gold.ts.isoformat(timespec="seconds") if last_gold else None,
            "last_bias": last_gold.bias if last_gold else None,
            "last_conviction": last_gold.conviction if last_gold else None,
            "last_etf_price": last_gold.etf_price if last_gold else None,
            "events_tracked": len(gold_events),
            "events_by_factor": by_factor,
            "events_by_impact": by_impact,
            "alerts_sent": len(gold_emailed),
            "alert_rate_pct": round(100 * len(gold_emailed) / len(gold_runs), 1) if gold_runs else None,
            "last_email_error": last_gold.email_error if last_gold else None,
            "email_configured": email_cfg["configured"],
            "email_missing_config": email_cfg["missing"],
            "email_to": email_cfg["to"],
        },
        "eval": {
            "latest_run_at": latest_eval.ts.isoformat(timespec="seconds") if latest_eval else None,
            "results": json.loads(latest_eval.payload_json) if latest_eval else None,
            "how_to_run": "backend: .venv/bin/python eval_rag.py [--no-judge] [--judge-sample N]",
        },
    }
