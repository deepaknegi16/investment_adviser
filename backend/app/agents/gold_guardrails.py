"""Guardrails for the Gold Watch agent.

The Analyst and Screener are on-demand: a person clicks, waits, and is the error
detector. The Gold Watch runs on cron and emails conclusions to an inbox nobody
is auditing, so its output has to be checked mechanically before anyone sees it.

Two rules shape everything here:

1. **Flag, don't drop.** A violation adds trust metadata and a recorded reason;
   it does not delete the model's work. Silently shrinking a report is how a
   monitor lies to you. The single exception is an unverifiable *link* — you
   cannot "mark a URL untrusted", because a reader still clicks it and lands
   somewhere unknown, so the event keeps its text and loses its href.

2. **Silence must always be explained.** The most dangerous failure for a
   watcher is reporting calm because it went blind. `check_feed_health` exists
   because `gold_news._fetch` swallows every feed error and returns [] — so nine
   dead feeds and a genuinely quiet market look identical downstream.

Everything is a pure function over data (except `check_send_allowed`, which
reads the run history), so the adversarial suite in eval_gold.py can exercise
the whole layer with no network, no model and no mocks.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .. import safety

TRUST_OK = "ok"
TRUST_UNVERIFIED = "unverified"
TRUST_SUSPECT = "suspect"

DIRECTIONS = {"bullish", "bearish", "neutral"}
IMPACTS = {"high", "medium", "low"}
HORIZONS = {"immediate", "weeks", "months"}

MAX_EVENT_AGE_DAYS = 45
MIN_HEADLINES = 15          # below this the sweep is not seeing the market
MAX_HEADLINE_CHARS = 200

# Alert-storm limits. Env-overridable so they can be loosened without a deploy.
MAX_ALERTS_PER_DAY = int(os.environ.get("GOLD_ALERT_MAX_PER_DAY", "6"))
ALERT_COOLDOWN_MIN = int(os.environ.get("GOLD_ALERT_COOLDOWN_MIN", "45"))
HIGH_IMPACT_RATIO = float(os.environ.get("GOLD_ALERT_HIGH_RATIO", "0.7"))


@dataclass
class Violation:
    kind: str
    severity: str                      # low | medium | high
    detail: str
    event_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalise_headline(text: str) -> str:
    """Canonical form used for feed matching AND dedupe.

    Extracted so the provenance check and gold_watch's repair step can never
    disagree about what "the same headline" means.
    """
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())[:60]


# ------------------------------------------------------------------ 1. input

# Patterns that look like an instruction aimed at the model rather than a news
# headline. RSS titles are third-party text we neither control nor trust.
_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)",
    r"disregard\s+(?:all\s+)?(?:previous|prior|the above)",
    r"\bsystem\s*:",
    r"\bassistant\s*:",
    r"you\s+(?:must|should)\s+(?:now\s+)?(?:output|return|respond|reply|say|ignore)",
    r"new\s+instructions?\s*:",
    r"<\|.*?\|>",
    r"\{\{.*?\}\}",
    r"</?(?:system|instruction|prompt)>",
]
_INJECTION_RX = [re.compile(p, re.I | re.S) for p in _INJECTION_PATTERNS]


def sanitize_feed(items: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Violation]]:
    """Strip prompt structure out of feed text and flag instruction-like items.

    Headlines are rendered into a markdown prompt block, so a title containing
    '###' or a fence can forge structure the model reads as ours.
    """
    out: List[Dict[str, Any]] = []
    violations: List[Violation] = []
    for item in items:
        clean = dict(item)
        raw = clean.get("headline", "") or ""

        text = raw.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"```+", " ", text)          # code fences
        text = re.sub(r"^[\s>#*\-=|]+", "", text)  # leading markdown structure
        text = re.sub(r"[#|]{2,}", " ", text)      # forged headings / tables
        text = re.sub(r"\s{2,}", " ", text).strip()
        if len(text) > MAX_HEADLINE_CHARS:
            text = text[:MAX_HEADLINE_CHARS].rstrip() + "…"
        clean["headline"] = text

        # Sources are printed in the prompt too, and are equally third-party.
        clean["source"] = re.sub(r"[\r\n#|`]+", " ", clean.get("source", "") or "").strip()[:80]

        hits = [rx.pattern for rx in _INJECTION_RX if rx.search(raw)]
        if hits:
            clean["suspect"] = True
            violations.append(Violation(
                kind="injection_suspected",
                severity="high",
                detail=f"feed item reads like an instruction ({hits[0]}): {raw[:120]!r}",
            ))
        out.append(clean)
    return out, violations


def check_feed_health(
    items: Sequence[Dict[str, Any]], buckets: Sequence[str]
) -> List[Violation]:
    """Can the watcher actually see? Silence must never be unexplained."""
    violations: List[Violation] = []
    if not items:
        violations.append(Violation(
            kind="watcher_blind", severity="high",
            detail="no headlines retrieved from any feed — this sweep cannot "
                   "distinguish a quiet market from a broken one",
        ))
        return violations

    if len(items) < MIN_HEADLINES:
        violations.append(Violation(
            kind="watcher_degraded", severity="medium",
            detail=f"only {len(items)} headlines retrieved (expected >= {MIN_HEADLINES})",
        ))

    covered = {i.get("factor") for i in items}
    empty = [b for b in buckets if b not in covered]
    if buckets and len(empty) > max(1, len(buckets) // 3):
        violations.append(Violation(
            kind="feed_gaps", severity="medium",
            detail=f"{len(empty)}/{len(buckets)} factor buckets returned nothing: "
                   f"{', '.join(sorted(empty))}",
        ))
    return violations


# ----------------------------------------------------------------- 2. events


def check_events(
    events: Sequence[Dict[str, Any]],
    feed_items: Sequence[Dict[str, Any]],
    valid_factors: Set[str],
    today: Optional[dt.date] = None,
) -> Tuple[List[Dict[str, Any]], List[Violation]]:
    """Provenance, enum and date checks. Returns (events, violations).

    Events are annotated with `trust` and kept. Only exact in-sweep duplicates
    are removed, which loses no information.
    """
    today = today or dt.date.today()
    by_headline = {normalise_headline(f.get("headline", "")): f for f in feed_items}
    violations: List[Violation] = []
    kept: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for e in events:
        ev = dict(e)
        ev.setdefault("trust", TRUST_OK)
        ev["trust_reasons"] = []
        eid = ev.get("id") or normalise_headline(ev.get("headline", ""))

        # -- provenance --------------------------------------------------
        match = by_headline.get(normalise_headline(ev.get("headline", "")))
        if match:
            # The feed is the source of record: overwrite, never merge.
            ev["url"] = match.get("url", "")
            ev["source"] = match.get("source", "")
            ev["date"] = match.get("date", "")
            if match.get("suspect"):
                ev["trust"] = TRUST_SUSPECT
                ev["trust_reasons"].append("derived from a feed item flagged as injection-like")
                violations.append(Violation(
                    kind="from_suspect_source", severity="medium",
                    detail=f"event built from an injection-flagged headline: {ev.get('headline','')[:90]!r}",
                    event_id=eid,
                ))
        else:
            ev["trust"] = TRUST_UNVERIFIED
            ev["trust_reasons"].append("headline not found in any source feed")
            dropped = ev.get("url", "")
            ev["url"] = ""   # never ship a link we cannot trace
            violations.append(Violation(
                kind="unsourced", severity="high",
                detail=f"no feed item matches {ev.get('headline','')[:90]!r}"
                       + (f"; discarded unverifiable url {dropped[:60]!r}" if dropped else ""),
                event_id=eid,
            ))

        # -- enums (the Groq fallback does not enforce them) --------------
        for fieldname, allowed, safe in (
            ("factor", valid_factors, None),
            ("direction", DIRECTIONS, "neutral"),
            ("impact", IMPACTS, "low"),
            ("horizon", HORIZONS, "months"),
        ):
            value = ev.get(fieldname)
            if value not in allowed:
                violations.append(Violation(
                    kind="enum_invalid", severity="medium",
                    detail=f"{fieldname}={value!r} is not a valid value",
                    event_id=eid,
                ))
                ev["trust"] = TRUST_SUSPECT if ev["trust"] == TRUST_OK else ev["trust"]
                ev["trust_reasons"].append(f"invalid {fieldname} coerced")
                if safe is not None:
                    ev[fieldname] = safe
                elif value not in allowed:
                    ev[fieldname] = sorted(allowed)[0] if allowed else ""

        # -- dates -------------------------------------------------------
        raw_date = (ev.get("date") or "").strip()
        if raw_date:
            try:
                d = dt.date.fromisoformat(raw_date[:10])
                if d > today:
                    ev["trust"] = TRUST_SUSPECT
                    ev["trust_reasons"].append("dated in the future")
                    violations.append(Violation(
                        kind="date_implausible", severity="high",
                        detail=f"event dated {raw_date} is in the future", event_id=eid))
                elif (today - d).days > MAX_EVENT_AGE_DAYS:
                    ev["trust"] = TRUST_SUSPECT if ev["trust"] == TRUST_OK else ev["trust"]
                    ev["trust_reasons"].append("older than the news window")
                    violations.append(Violation(
                        kind="date_implausible", severity="low",
                        detail=f"event dated {raw_date} is older than {MAX_EVENT_AGE_DAYS} days",
                        event_id=eid))
            except ValueError:
                violations.append(Violation(
                    kind="date_implausible", severity="low",
                    detail=f"unparseable date {raw_date!r}", event_id=eid))
                ev["date"] = ""

        # -- exact in-sweep duplicates -----------------------------------
        if ev.get("id") and ev["id"] in seen_ids:
            violations.append(Violation(
                kind="duplicate_in_sweep", severity="low",
                detail=f"repeated event {ev['id']}", event_id=eid))
            continue
        if ev.get("id"):
            seen_ids.add(ev["id"])
        kept.append(ev)

    return kept, violations


# ------------------------------------------------------------------ 3. prose

# Anchored so only numbers making a checkable claim are compared. A loose
# "flag any number not in the snapshot" rule would fire on every cited forecast.
_PRICE_CLAIM = re.compile(r"GOLDBEES[^.\n]{0,60}?₹\s*([\d,]+(?:\.\d+)?)", re.I)
_YEAR_CLAIM = re.compile(
    r"(?:1[- ]year|1y\b|twelve[- ]month|past year|over the year)[^.\n]{0,60}?([\d.]+)\s*%", re.I)
_PREMIUM_CLAIM = re.compile(
    r"(?:premium[^.\n]{0,40}?([\d.]+)\s*%|([\d.]+)\s*%[^.\n]{0,25}?premium)", re.I)


def _num(text: str) -> Optional[float]:
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def check_prose(result: Dict[str, Any], snapshot: Dict[str, Any]) -> List[Violation]:
    """Advice-language and numeric-drift checks over the model's free text."""
    violations: List[Violation] = []
    bias = result.get("bias") or {}
    blocks = [
        ("bias.summary", bias.get("summary", "")),
        ("goldbees_note", result.get("goldbees_note", "")),
    ] + [
        (f"events[{i}].why_it_matters", e.get("why_it_matters", ""))
        for i, e in enumerate(result.get("events", []))
    ]

    for label, text in blocks:
        for hit in safety.scan_advice(text or ""):
            violations.append(Violation(
                kind="advice_language", severity="high",
                detail=f"{label} contains {hit['kind']} language: {hit['phrase']!r}",
            ))

    etf = snapshot.get("etf") or {}
    dom = snapshot.get("domestic") or {}
    prose = " ".join(t for _, t in blocks if t)

    def drift(claimed: Optional[float], actual: Optional[float], tol: float, what: str) -> None:
        if claimed is None or actual is None:
            return
        if abs(claimed - actual) > tol:
            violations.append(Violation(
                kind="numeric_drift", severity="medium",
                detail=f"prose says {what} is {claimed}, snapshot says {round(actual, 2)}",
            ))

    m = _PRICE_CLAIM.search(prose)
    if m:
        drift(_num(m.group(1)), etf.get("price"), max(2.0, (etf.get("price") or 0) * 0.02),
              "the GOLDBEES price")
    m = _YEAR_CLAIM.search(prose)
    if m:
        drift(_num(m.group(1)), etf.get("chg_1y"), 1.5, "the 1-year return")
    m = _PREMIUM_CLAIM.search(prose)
    if m:
        drift(_num(m.group(1) or m.group(2)), dom.get("premium_vs_baseline_pct"), 1.0,
              "the domestic premium")

    return violations


# ------------------------------------------------------------- 4. send limits


def check_send_allowed(db, result: Dict[str, Any], now: Optional[dt.datetime] = None
                       ) -> Tuple[bool, List[Violation]]:
    """Rate-limit outbound alerts. Returns (allowed, violations).

    A suppressed send is always recorded, so a quiet inbox is never ambiguous
    between "nothing happened" and "the limiter ate it".
    """
    from ..db import GoldWatchRun

    now = now or dt.datetime.utcnow()
    violations: List[Violation] = []

    recent = (
        db.query(GoldWatchRun)
        .filter(GoldWatchRun.emailed == 1)
        .order_by(GoldWatchRun.id.desc())
        .limit(MAX_ALERTS_PER_DAY + 5)
        .all()
    )
    sent_24h = [r for r in recent if r.ts and (now - r.ts) <= dt.timedelta(hours=24)]

    allowed = True
    if len(sent_24h) >= MAX_ALERTS_PER_DAY:
        allowed = False
        violations.append(Violation(
            kind="alert_rate_limited", severity="medium",
            detail=f"{len(sent_24h)} alerts already sent in 24h (cap {MAX_ALERTS_PER_DAY})",
        ))
    elif sent_24h:
        since = (now - sent_24h[0].ts).total_seconds() / 60
        if since < ALERT_COOLDOWN_MIN:
            allowed = False
            violations.append(Violation(
                kind="alert_cooldown", severity="low",
                detail=f"last alert {int(since)} min ago (cooldown {ALERT_COOLDOWN_MIN} min)",
            ))

    # Not a block — a scoring regression should still reach you, but labelled.
    events = result.get("events", [])
    if events:
        highs = sum(1 for e in events if e.get("impact") == "high")
        ratio = highs / len(events)
        if ratio > HIGH_IMPACT_RATIO:
            violations.append(Violation(
                kind="impact_inflation", severity="medium",
                detail=f"{highs}/{len(events)} events marked high impact "
                       f"({ratio:.0%} > {HIGH_IMPACT_RATIO:.0%}) — suspect scoring regression",
            ))
    return allowed, violations


def summarise(violations: Sequence[Violation]) -> Dict[str, Any]:
    """Compact rollup for logs, the run row and /api/metrics."""
    by_kind: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for v in violations:
        by_kind[v.kind] = by_kind.get(v.kind, 0) + 1
        by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
    return {
        "total": len(violations),
        "by_kind": by_kind,
        "by_severity": by_severity,
        "blocking": any(v.severity == "high" for v in violations),
    }


def summarise_dicts(violations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """summarise() for violations already serialised to dicts."""
    by_kind: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for v in violations:
        by_kind[v.get("kind", "?")] = by_kind.get(v.get("kind", "?"), 0) + 1
        by_severity[v.get("severity", "?")] = by_severity.get(v.get("severity", "?"), 0) + 1
    return {
        "total": len(violations),
        "by_kind": by_kind,
        "by_severity": by_severity,
        "blocking": any(v.get("severity") == "high" for v in violations),
    }
