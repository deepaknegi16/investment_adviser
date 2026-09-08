"""Gold Watch API: factor board, news sweep, alert history, email test."""
from __future__ import annotations

import json

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import gold_factors, gold_scenarios, notify
from ..agents.gold_alerts import run_and_alert
from ..agents.gold_watch import FACTORS
from ..agents.runner import AgentUnavailable
from ..db import GoldEvent, GoldWatchRun, get_db

router = APIRouter(prefix="/api/gold")


@router.get("/factors")
def factors():
    """The quantitative factor board — deterministic, no AI, no quota spend."""
    return {
        "taxonomy": {k: v["label"] for k, v in FACTORS.items()},
        **gold_factors.snapshot(),
    }


@router.get("/history")
def history(days: int = 1100):
    return {"points": gold_factors.history(days)}


@router.get("/scenarios")
def scenarios(horizon: str = "2027-12"):
    """Driver-path scenario projection + 1sd cone. Deterministic, no AI."""
    return gold_scenarios.project(horizon)


@router.get("/sensitivity")
def sensitivity():
    """End-horizon GOLDBEES across a gold x USDINR grid."""
    return gold_scenarios.sensitivity()


@router.post("/watch")
def watch(force_email: bool = False, db: Session = Depends(get_db)):
    """Run a full news sweep now; emails if the materiality bar is cleared."""
    try:
        return run_and_alert(force_email=force_email)
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))


@router.get("/latest")
def latest(db: Session = Depends(get_db)):
    """Most recent sweep, straight from cache — no AI call."""
    row = db.query(GoldWatchRun).order_by(GoldWatchRun.id.desc()).first()
    if not row:
        raise HTTPException(404, "No gold watch has run yet. POST /api/gold/watch.")
    payload = json.loads(row.payload_json)
    payload["run_ts"] = row.ts.isoformat(timespec="seconds")
    payload["emailed"] = bool(row.emailed)
    return payload


@router.get("/events")
def events(limit: int = 50, factor: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(GoldEvent)
    if factor:
        q = q.filter(GoldEvent.factor == factor)
    rows = q.order_by(GoldEvent.first_seen.desc()).limit(min(limit, 200)).all()
    return {
        "events": [
            {
                "id": r.id, "factor": r.factor,
                "factor_label": FACTORS.get(r.factor, {}).get("label", r.factor),
                "headline": r.headline, "source": r.source, "url": r.url,
                "date": r.event_date, "direction": r.direction, "impact": r.impact,
                "horizon": r.horizon, "why_it_matters": r.why_it_matters,
                "first_seen": r.first_seen.isoformat(timespec="seconds"),
                "alerted": bool(r.alerted),
            }
            for r in rows
        ]
    }


@router.get("/runs")
def runs(limit: int = 30, db: Session = Depends(get_db)):
    rows = db.query(GoldWatchRun).order_by(GoldWatchRun.id.desc()).limit(min(limit, 100)).all()
    return {
        "runs": [
            {
                "id": r.id, "ts": r.ts.isoformat(timespec="seconds"), "bias": r.bias,
                "conviction": r.conviction, "etf_price": r.etf_price,
                "n_events": r.n_events, "n_new": r.n_new, "emailed": bool(r.emailed),
                "email_error": r.email_error,
            }
            for r in rows
        ]
    }


@router.get("/alerts/config")
def alerts_config():
    return notify.config_status()


@router.post("/alerts/test")
def alerts_test():
    """Send a one-line test email so you can verify SMTP before trusting alerts."""
    try:
        return notify.send_email(
            subject="[GoldBeES] Alert channel test",
            text_body="Your GOLDBEES gold-watch alerts are wired up correctly.",
            html_body='<div style="font:15px/1.6 system-ui;padding:20px">'
                      "Your <b>GOLDBEES</b> gold-watch alerts are wired up correctly."
                      "</div>",
        )
    except notify.EmailNotConfigured as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"SMTP send failed: {type(e).__name__}: {e}")
