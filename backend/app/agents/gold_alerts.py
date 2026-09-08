"""Materiality rules, dedupe and email delivery for the Gold Watch agent.

The agent finds news; this module decides whether the news is worth an inbox
interruption. Getting that bar right is the whole point — a watcher that emails
every headline gets muted within a week, and a muted watcher is worse than none.
"""
from __future__ import annotations

import datetime as dt
import html
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .. import notify
from ..db import GoldEvent, GoldWatchRun, SessionLocal
from .gold_watch import FACTORS, run_watch

# Materiality bar — an alert fires when ANY of these is true.
DAY_MOVE_PCT = 2.0        # a >=2% single-session move in a gold ETF is a real event
MEDIUM_EVENT_COUNT = 3    # three fresh second-order pushes add up to one story
PREMIUM_MOVE_PCT = 2.0    # a 1m jump in the domestic premium = India policy moved
RSI_HOT, RSI_COLD = 70.0, 30.0

DIRECTION_MARK = {"bullish": "▲", "bearish": "▼", "neutral": "•"}
IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}


# ------------------------------------------------------------------ dedupe


def _split_new(db: Session, events: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """Partition into (never-seen-before, already-stored)."""
    fresh, known = [], []
    for e in events:
        if not e.get("id"):
            continue
        (known if db.get(GoldEvent, e["id"]) else fresh).append(e)
    return fresh, known


def _store(db: Session, events: List[Dict[str, Any]], alerted: bool) -> None:
    for e in events:
        db.merge(
            GoldEvent(
                id=e["id"],
                event_date=e.get("date", ""),
                factor=e.get("factor", ""),
                headline=e.get("headline", ""),
                source=e.get("source", ""),
                url=e.get("url", ""),
                direction=e.get("direction", "neutral"),
                impact=e.get("impact", "low"),
                horizon=e.get("horizon", ""),
                why_it_matters=e.get("why_it_matters", ""),
                alerted=1 if alerted else 0,
            )
        )
    db.commit()


def _last_bias(db: Session) -> Optional[str]:
    row = db.query(GoldWatchRun).order_by(GoldWatchRun.id.desc()).first()
    return row.bias if row else None


# ------------------------------------------------------------- materiality


def decide(result: Dict[str, Any], new_events: List[Dict], prev_bias: Optional[str]) -> Dict[str, Any]:
    """Should this wake the inbox? Returns the decision and its reasons."""
    snap = result.get("snapshot", {})
    etf = snap.get("etf", {})
    reasons: List[str] = []

    highs = [e for e in new_events if e.get("impact") == "high"]
    mediums = [e for e in new_events if e.get("impact") == "medium"]
    if highs:
        reasons.append(
            f"{len(highs)} new high-impact event(s): "
            + "; ".join(e["headline"][:80] for e in highs[:3])
        )
    if len(mediums) >= MEDIUM_EVENT_COUNT:
        reasons.append(f"{len(mediums)} new medium-impact events clustered together")

    day = etf.get("chg_1d")
    if day is not None and abs(day) >= DAY_MOVE_PCT:
        reasons.append(f"GOLDBEES moved {day:+.2f}% in one session")

    bias = (result.get("bias") or {}).get("direction")
    if prev_bias and bias and bias != prev_bias:
        reasons.append(f"factor bias flipped {prev_bias} → {bias}")

    prem_1m = ((snap.get("attribution") or {}).get("1m") or {}).get("premium_pct")
    if prem_1m is not None and abs(prem_1m) >= PREMIUM_MOVE_PCT:
        reasons.append(
            f"India domestic premium moved {prem_1m:+.2f}% in a month — "
            f"check for an import-duty or tax change"
        )

    rsi = etf.get("rsi14")
    if rsi is not None:
        if rsi >= RSI_HOT:
            reasons.append(f"RSI-14 at {rsi} — overbought")
        elif rsi <= RSI_COLD:
            reasons.append(f"RSI-14 at {rsi} — oversold")

    return {"alert": bool(reasons), "reasons": reasons}


# ----------------------------------------------------------------- render


def _subject(result: Dict[str, Any], reasons: List[str]) -> str:
    snap = result.get("snapshot", {})
    etf = snap.get("etf", {})
    bias = (result.get("bias") or {}).get("direction", "neutral")
    day = etf.get("chg_1d")
    move = f"{day:+.2f}%" if day is not None else "—"
    lead = reasons[0].split(":")[0] if reasons else "update"
    return f"[GoldBeES] ₹{etf.get('price','?')} ({move}) · {bias} · {lead}"


def _text_body(result: Dict[str, Any], new_events: List[Dict], reasons: List[str]) -> str:
    snap = result.get("snapshot", {})
    etf, d = snap.get("etf", {}), snap.get("drivers", {})
    bias = result.get("bias", {})
    att = (snap.get("attribution") or {}).get("1y") or {}
    lines = [
        f"GOLDBEES gold watch — {result.get('generated_at','')}",
        "",
        f"GOLDBEES ₹{etf.get('price')}  1d {etf.get('chg_1d')}%  1m {etf.get('chg_1m')}%  "
        f"1y {etf.get('chg_1y')}%  RSI {etf.get('rsi14')}  ({etf.get('pct_from_high52')}% from 52w high)",
        f"Gold ${d.get('gold_usd_oz',{}).get('last')}/oz · USDINR {d.get('usdinr',{}).get('last')} · "
        f"DXY {d.get('dxy',{}).get('last')} · US10y {d.get('us10y_pct',{}).get('last')}% · "
        f"Brent ${d.get('brent_usd',{}).get('last')}",
        f"1y attribution: ETF {att.get('etf_pct')}% = gold {att.get('gold_usd_pct')}% "
        f"+ INR {att.get('usdinr_pct')}% + India premium {att.get('premium_pct')}%",
        "",
        f"BIAS: {bias.get('direction','?').upper()} ({bias.get('conviction','?')} conviction)",
        bias.get("summary", ""),
        "",
        "WHY YOU ARE GETTING THIS:",
    ]
    lines += [f"  - {r}" for r in reasons]
    lines += ["", f"NEW EVENTS ({len(new_events)}):"]
    for e in sorted(new_events, key=lambda x: IMPACT_ORDER.get(x.get("impact"), 3)):
        lines += [
            f"  [{e.get('impact','?').upper()}] {DIRECTION_MARK.get(e.get('direction'),'•')} "
            f"{e.get('factor_label', e.get('factor',''))} — {e.get('headline','')}",
            f"      {e.get('why_it_matters','')}",
            f"      {e.get('source','')} {e.get('date','')} {e.get('url','')}",
        ]
    lines += ["", "FOR A GOLDBEES HOLDER:", result.get("goldbees_note", ""), "", "WATCH NEXT:"]
    lines += [f"  - {w}" for w in result.get("watch_next", [])]
    lines += ["", "Informational research, not personalised financial advice."]
    return "\n".join(lines)


def _html_body(result: Dict[str, Any], new_events: List[Dict], reasons: List[str]) -> str:
    snap = result.get("snapshot", {})
    etf, d = snap.get("etf", {}), snap.get("drivers", {})
    bias = result.get("bias", {})
    att = (snap.get("attribution") or {}).get("1y") or {}
    esc = html.escape

    def chip(label: str, value: Any, sub: str = "") -> str:
        return (
            f'<td style="padding:8px 14px 8px 0;vertical-align:top">'
            f'<div style="font:11px/1.4 system-ui;color:#6b7280;text-transform:uppercase;'
            f'letter-spacing:.04em">{esc(label)}</div>'
            f'<div style="font:600 17px/1.3 system-ui;color:#111827">{esc(str(value))}</div>'
            f'<div style="font:12px/1.4 system-ui;color:#6b7280">{esc(sub)}</div></td>'
        )

    colour = {"bullish": "#047857", "bearish": "#b91c1c", "neutral": "#92400e"}
    tone = colour.get(bias.get("direction", "neutral"), "#374151")

    rows = []
    for e in sorted(new_events, key=lambda x: IMPACT_ORDER.get(x.get("impact"), 3)):
        dirn = e.get("direction", "neutral")
        badge = {"high": "#b91c1c", "medium": "#b45309", "low": "#6b7280"}.get(e.get("impact"), "#6b7280")
        url = e.get("url", "")
        title = esc(e.get("headline", ""))
        link = f'<a href="{esc(url)}" style="color:#111827;text-decoration:none">{title}</a>' if url else title
        rows.append(
            f'<tr><td style="padding:12px 0;border-top:1px solid #e5e7eb">'
            f'<div><span style="display:inline-block;font:600 10px/1.6 system-ui;color:#fff;'
            f'background:{badge};border-radius:3px;padding:0 6px;letter-spacing:.05em">'
            f'{esc(e.get("impact","").upper())}</span> '
            f'<span style="font:12px/1.6 system-ui;color:#6b7280">'
            f'{esc(e.get("factor_label", e.get("factor","")))} · '
            f'{DIRECTION_MARK.get(dirn,"•")} {esc(dirn)} · {esc(e.get("horizon",""))}</span></div>'
            f'<div style="font:600 15px/1.45 system-ui;margin:4px 0 3px">{link}</div>'
            f'<div style="font:13px/1.55 system-ui;color:#374151">{esc(e.get("why_it_matters",""))}</div>'
            f'<div style="font:12px/1.5 system-ui;color:#9ca3af;margin-top:3px">'
            f'{esc(e.get("source",""))} · {esc(e.get("date",""))}</div></td></tr>'
        )

    reason_items = "".join(f"<li>{esc(r)}</li>" for r in reasons)
    watch_items = "".join(f"<li>{esc(w)}</li>" for w in result.get("watch_next", []))

    return f"""<div style="max-width:640px;margin:0 auto;padding:24px;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#111827;background:#fff">
<div style="font:12px/1.5 system-ui;color:#6b7280;letter-spacing:.06em;text-transform:uppercase">Gold watch · GOLDBEES</div>
<h1 style="font:700 24px/1.25 system-ui;margin:4px 0 2px">₹{esc(str(etf.get('price')))}
<span style="font:500 15px/1.3 system-ui;color:{'#047857' if (etf.get('chg_1d') or 0) >= 0 else '#b91c1c'}">
{esc(f"{etf.get('chg_1d'):+.2f}%" if etf.get('chg_1d') is not None else '')}</span></h1>
<div style="font:13px/1.5 system-ui;color:#6b7280;margin-bottom:16px">{esc(result.get('generated_at',''))} · Nippon India ETF Gold BeES</div>

<table style="border-collapse:collapse;margin-bottom:8px"><tr>
{chip('Gold', f"${d.get('gold_usd_oz',{}).get('last')}", f"1y {d.get('gold_usd_oz',{}).get('chg_1y')}%")}
{chip('USDINR', d.get('usdinr',{}).get('last'), f"1y {d.get('usdinr',{}).get('chg_1y')}%")}
{chip('DXY', d.get('dxy',{}).get('last'), '')}
{chip('US 10y', f"{d.get('us10y_pct',{}).get('last')}%", '')}
{chip('Brent', f"${d.get('brent_usd',{}).get('last')}", f"1y {d.get('brent_usd',{}).get('chg_1y')}%")}
</tr></table>
<div style="font:12px/1.6 system-ui;color:#6b7280;margin-bottom:18px">
1-year attribution: ETF <b>{esc(str(att.get('etf_pct')))}%</b> = gold {esc(str(att.get('gold_usd_pct')))}%
+ rupee {esc(str(att.get('usdinr_pct')))}% + India premium {esc(str(att.get('premium_pct')))}%
&nbsp;·&nbsp; RSI {esc(str(etf.get('rsi14')))} &nbsp;·&nbsp; {esc(str(etf.get('pct_from_high52')))}% from 52w high</div>

<div style="border-left:3px solid {tone};padding:2px 0 2px 12px;margin-bottom:18px">
<div style="font:700 13px/1.5 system-ui;color:{tone};text-transform:uppercase;letter-spacing:.05em">
{esc(bias.get('direction','')) } · {esc(bias.get('conviction',''))} conviction</div>
<div style="font:14px/1.6 system-ui">{esc(bias.get('summary',''))}</div></div>

<div style="font:12px/1.5 system-ui;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">Why this alert fired</div>
<ul style="font:13px/1.6 system-ui;color:#374151;margin:6px 0 20px;padding-left:20px">{reason_items}</ul>

<div style="font:12px/1.5 system-ui;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">New events ({len(new_events)})</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:20px">{''.join(rows)}</table>

<div style="background:#f9fafb;border-radius:6px;padding:14px;margin-bottom:18px">
<div style="font:12px/1.5 system-ui;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">What it means for your GOLDBEES</div>
<div style="font:14px/1.6 system-ui;margin-top:4px">{esc(result.get('goldbees_note',''))}</div></div>

<div style="font:12px/1.5 system-ui;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">Watch next</div>
<ul style="font:13px/1.6 system-ui;color:#374151;margin:6px 0 20px;padding-left:20px">{watch_items}</ul>

<div style="border-top:1px solid #e5e7eb;padding-top:12px;font:11px/1.6 system-ui;color:#9ca3af">
Generated by the Gold Watch agent in your Investment Adviser dashboard.
Informational research from public sources — not personalised financial advice.</div></div>"""


# ------------------------------------------------------------------ driver


def run_and_alert(
    force_email: bool = False,
    buckets: List[str] | None = None,
    enrich: bool = True,
) -> Dict[str, Any]:
    """One sweep end to end: research → dedupe → decide → email → persist."""
    result = run_watch(buckets, enrich=enrich)
    events = result.get("events", [])

    with SessionLocal() as db:
        new_events, _ = _split_new(db, events)
        prev_bias = _last_bias(db)
        decision = decide(result, new_events, prev_bias)

        should_send = force_email or (decision["alert"] and bool(new_events))
        if force_email and not decision["reasons"]:
            decision["reasons"] = ["manual/scheduled digest — no materiality trigger"]

        emailed, email_error = False, None
        if should_send:
            payload = new_events or events  # a forced digest still needs content
            try:
                notify.send_email(
                    subject=_subject(result, decision["reasons"]),
                    text_body=_text_body(result, payload, decision["reasons"]),
                    html_body=_html_body(result, payload, decision["reasons"]),
                )
                emailed = True
            except Exception as e:  # a dead SMTP host must not lose the research
                email_error = f"{type(e).__name__}: {e}"

        _store(db, new_events, alerted=emailed)
        snap = result.get("snapshot", {})
        db.add(
            GoldWatchRun(
                bias=(result.get("bias") or {}).get("direction"),
                conviction=(result.get("bias") or {}).get("conviction"),
                etf_price=(snap.get("etf") or {}).get("price"),
                n_events=len(events),
                n_new=len(new_events),
                emailed=1 if emailed else 0,
                email_error=email_error,
                payload_json=json.dumps(result),
            )
        )
        db.commit()

    result["new_event_ids"] = [e["id"] for e in new_events]
    result["decision"] = decision
    result["emailed"] = emailed
    result["email_error"] = email_error
    result["email_config"] = notify.config_status()
    return result


# ------------------------------------------------- stock-analyst compatibility

# The dashboard's StockDrawer renders one shape: ANALYSIS_SCHEMA from
# analyst.py. Rather than teach the frontend about gold, the sweep is projected
# into that shape — so opening GOLDBEES in the drawer shows factor news and a
# driver-based outlook instead of the equity analyst hunting for order wins on
# an ETF that has neither earnings nor management.
BIAS_TO_CALL = {"bullish": "BUY", "bearish": "SELL", "neutral": "HOLD"}


def as_stock_analysis(symbol: str, name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    from .. import gold_scenarios

    snap = result.get("snapshot", {})
    etf = snap.get("etf", {})
    bias = result.get("bias", {})
    att = (snap.get("attribution") or {}).get("1y") or {}
    dom = snap.get("domestic", {})

    news = [
        {
            "headline": e.get("headline", ""),
            "source": e.get("source", ""),
            "url": e.get("url", ""),
            "date": e.get("date", ""),
            "summary": f"[{e.get('factor_label', e.get('factor',''))} · "
                       f"{e.get('impact','')} impact · {e.get('direction','')}] "
                       f"{e.get('why_it_matters','')}",
        }
        for e in sorted(
            result.get("events", []), key=lambda x: IMPACT_ORDER.get(x.get("impact"), 3)
        )[:6]
    ]

    try:
        proj = gold_scenarios.project()
        e27 = proj["expected"]["2027-12"]
        long_term = (
            f"Scenario arithmetic to Dec-2027 over explicit driver paths: bear "
            f"₹{e27['bear']:.0f}, base ₹{e27['base']:.0f}, bull ₹{e27['bull']:.0f}; "
            f"probability-weighted ₹{e27['probability_weighted']:.0f} "
            f"({e27['vs_spot_pct']:+.1f}% vs spot). The ±1σ band at "
            f"{proj['realised_vol_pct']}% realised volatility is wider than the "
            f"spread between those scenarios — treat the direction as the signal, "
            f"not the number."
        )
    except Exception:
        long_term = (
            "Long-run direction is set by central-bank demand and the rupee's "
            "drift, not by anything company-specific. Scenario model unavailable "
            "for this run."
        )

    watch = result.get("watch_next", [])
    reasoning = " ".join(filter(None, [
        result.get("goldbees_note", ""),
        f"Return attribution over 1 year: the ETF's {att.get('etf_pct')}% breaks "
        f"into {att.get('gold_usd_pct')}% from gold in dollars, "
        f"{att.get('usdinr_pct')}% from rupee depreciation and "
        f"{att.get('premium_pct')}% from a widening domestic premium "
        f"(now {dom.get('premium_vs_baseline_pct')}% over its early-2025 baseline, "
        f"largely the May-2026 import-duty hike — a policy-reversible gain).",
        f"Next: {watch[0]}" if watch else "",
        "Produced by the Gold Watch factor agent, not the equity analyst: a gold "
        "ETF has no earnings, management or order book, so this is macro "
        "research rather than company analysis.",
    ]))

    return {
        "symbol": symbol,
        "news": news,
        "prediction": {
            "short_term": bias.get("summary", ""),
            "long_term": long_term,
            "confidence": bias.get("conviction", "low"),
        },
        "recommendation": BIAS_TO_CALL.get(bias.get("direction", "neutral"), "HOLD"),
        "reasoning": reasoning,
        "generated_at": result.get("generated_at", ""),
        # Extras the drawer ignores but the gold endpoints and RAG can use.
        "agent": "gold_watch",
        "bias": bias,
        "snapshot": snap,
        "watch_next": watch,
        "emailed": result.get("emailed", False),
        "headlines_scanned": result.get("headlines_scanned", 0),
    }


def analyze_gold_etf(symbol: str, name: str) -> Dict[str, Any]:
    """Full sweep (including alerting) rendered in the stock-analyst shape."""
    return as_stock_analysis(symbol, name, run_and_alert())
