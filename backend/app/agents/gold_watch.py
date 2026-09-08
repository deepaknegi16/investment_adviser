"""Gold Watch Agent — follows everything that moves the gold price, then the
rupee price of gold, then GOLDBEES, and emails when something material lands.

Why a dedicated agent instead of reusing the stock analyst: GOLDBEES has no
earnings, no management and no order book. Its price is a chain of macro
factors, and news matters only in so far as it moves one *link* of that chain:

    US real rates / Fed  ->  gold in USD  ->  x USDINR  ->  x India import duty
    central bank buying                        ->  GOLDBEES
    geopolitics / oil / inflation
    ETF flows, India physical demand

So the agent watches a fixed taxonomy of factors, tags every headline with the
factor it hits, a direction and an impact, and only wakes the inbox for things
that actually clear a materiality bar.

Pipeline:
  1. gold_factors.snapshot()  — deterministic maths, no AI
  2. gold_news.collect()      — RSS discovery per factor bucket, no AI, no quota
  3. web_research()           — optional Gemini search-grounding enrichment; the
                               free daily quota is small, so this is best-effort
  4. structured_synthesis()   — tag, score and summarise into WATCH_SCHEMA

Discovery is deliberately quota-free. When the AI search quota is exhausted the
watcher must still see the news, because a watcher that goes blind silently is
the one failure mode that matters.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from typing import Any, Dict, List, Tuple

from .. import gold_factors, gold_news, safety
from . import gold_guardrails as gr
from .runner import AgentUnavailable, structured_synthesis, web_research

GOLD_MODEL = os.environ.get("GOLD_WATCH_MODEL", os.environ.get("ANALYST_MODEL", "gemini-3.5-flash"))

# The factor taxonomy. Each bucket is a distinct transmission channel — the
# `sign` records which way a *positive* development in that bucket pushes the
# rupee gold price, which is what lets the agent reason about INR-specific
# divergence (e.g. gold falls in USD but GOLDBEES holds because INR slides).
FACTORS: Dict[str, Dict[str, str]] = {
    "fed_rates": {
        "label": "Fed policy & US real yields",
        "channel": "gold in USD",
        "query": "Federal Reserve interest rate decision, FOMC minutes, dot plot, "
                 "US CPI/PCE inflation print, US real yields and TIPS, Treasury "
                 "10-year yield moves",
    },
    "dollar": {
        "label": "US dollar / DXY",
        "channel": "gold in USD",
        "query": "US dollar index DXY strength or weakness, de-dollarisation, "
                 "reserve currency shifts, dollar funding stress",
    },
    "central_banks": {
        "label": "Central bank gold buying",
        "channel": "gold in USD",
        "query": "central bank gold purchases and reserves, World Gold Council "
                 "demand trends, PBoC RBI gold buying, IMF reserve data",
    },
    "geopolitics": {
        "label": "Geopolitics & safe-haven demand",
        "channel": "gold in USD",
        "query": "Middle East / Iran conflict escalation, war risk, sanctions, "
                 "tariffs and trade war, sovereign debt or banking stress",
    },
    "etf_flows": {
        "label": "ETF & investment flows",
        "channel": "gold in USD",
        "query": "gold ETF inflows outflows, SPDR GLD holdings, COMEX futures "
                 "positioning, CFTC net longs, speculative positioning in gold",
    },
    "inr": {
        "label": "Rupee / USDINR",
        "channel": "USD -> INR leg",
        "query": "USDINR rupee exchange rate, RBI intervention and forex "
                 "reserves, India current account deficit, FII flows into India",
    },
    "india_policy": {
        "label": "India import duty, tax & regulation",
        "channel": "domestic premium",
        "query": "India gold import duty customs duty change, GST on gold, "
                 "SEBI gold ETF rules, sovereign gold bond, gold monetisation, "
                 "India gold import curbs",
    },
    "india_demand": {
        "label": "India physical demand & domestic premium",
        "channel": "domestic premium",
        "query": "India gold demand jewellery festive wedding season, domestic "
                 "gold discount or premium to landed cost, MCX gold, India gold "
                 "imports monthly tonnage, smuggling",
    },
    "supply": {
        "label": "Mine supply, recycling & fund mechanics",
        "channel": "gold in USD",
        "query": "gold mine production and output, recycling supply, refinery "
                 "flows, Nippon India ETF Gold BeES AUM expense ratio tracking "
                 "error news",
    },
}

# NSE gold ETFs that should be routed to this agent instead of the equity
# analyst. An explicit list, not a substring match on "GOLD" — GOLDIAM is a
# jewellery *equity* and would be misrouted by a naive match. Extend as needed.
GOLD_ETF_SYMBOLS = {
    "GOLDBEES.NS",   # Nippon India ETF Gold BeES
    "SETFGOLD.NS",   # SBI Gold ETF
    "GOLDSHARE.NS",  # UTI Gold ETF
    "AXISGOLD.NS",   # Axis Gold ETF
    "QGOLDHALF.NS",  # Quantum Gold Fund
    "IVZINGOLD.NS",  # Invesco India Gold ETF
    "LICMFGOLD.NS",  # LIC MF Gold ETF
    "HDFCGOLD.NS",   # HDFC Gold ETF
    "TATAGOLD.NS",   # Tata Gold ETF
}


def is_gold_etf(symbol: str) -> bool:
    return symbol.upper() in GOLD_ETF_SYMBOLS


WATCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "factor": {"type": "string", "enum": list(FACTORS.keys())},
                    "headline": {"type": "string"},
                    "source": {"type": "string"},
                    "url": {"type": "string"},
                    "date": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": ["bullish", "bearish", "neutral"],
                    },
                    "impact": {"type": "string", "enum": ["high", "medium", "low"]},
                    "horizon": {
                        "type": "string",
                        "enum": ["immediate", "weeks", "months"],
                    },
                    "why_it_matters": {"type": "string"},
                },
                "required": [
                    "factor", "headline", "source", "url", "date",
                    "direction", "impact", "horizon", "why_it_matters",
                ],
            },
        },
        "bias": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["bullish", "neutral", "bearish"],
                },
                "conviction": {"type": "string", "enum": ["low", "medium", "high"]},
                "summary": {"type": "string"},
            },
            "required": ["direction", "conviction", "summary"],
        },
        "goldbees_note": {"type": "string"},
        "watch_next": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["events", "bias", "goldbees_note", "watch_next"],
}

SYNTHESIS_SYSTEM = """You are a commodities strategist covering gold for an
Indian retail investor who holds GOLDBEES (Nippon India ETF Gold BeES, NSE), a
rupee-denominated physical-gold ETF.

Ground rules:
- Every event MUST come from the news findings provided, with a real source and
  a URL taken from the provided source list. Never invent a URL, a date or a
  headline. Unknown field -> empty string.
- Tag each event with the ONE factor bucket it hits hardest.
- `direction` is the effect on the RUPEE price of gold, not the dollar price.
  This matters: a stronger dollar is bearish for gold in USD but is usually
  paired with a weaker rupee, which partly offsets it for an Indian holder. Say
  so when it applies.
- `impact` is materiality, not novelty. high = plausibly moves gold >2% or
  changes the policy regime (a Fed decision, an import-duty change, a war).
  medium = a real but second-order push. low = colour and context.
- Prefer the last 14 days. Drop anything older than 45 days unless it is still
  the live driver.
- No price targets you cannot defend from the quantitative snapshot or a cited
  forecast.
- """ + safety.PROMPT_RULE


def _news_prompt(today: str, buckets: List[str]) -> str:
    lines = [
        f"- {FACTORS[b]['label']}: {FACTORS[b]['query']}" for b in buckets
    ]
    return (
        f"Today is {today}. Search for the latest news (prefer the last 14 days) "
        f"on the factors below, all of which drive the price of gold and, through "
        f"the rupee, the price of the Indian ETF GOLDBEES (Nippon India ETF Gold "
        f"BeES).\n\n" + "\n".join(lines) + "\n\n"
        "For each item found, give the headline, the publisher, the date, a one-"
        "line summary, and say which way it pushes gold. Skip anything older than "
        "45 days unless it is still the dominant live driver."
    )


def event_key(event: Dict[str, Any]) -> str:
    """Stable id for dedupe: same story from the same source on the same day."""
    norm = gr.normalise_headline(event.get("headline") or "")
    raw = f"{event.get('factor','')}|{norm}|{(event.get('source') or '').lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def run_watch(buckets: List[str] | None = None, enrich: bool = True) -> Dict[str, Any]:
    """One full sweep. Returns the snapshot + tagged events + overall bias."""
    today = dt.date.today().isoformat()
    buckets = buckets or list(FACTORS.keys())

    snap = gold_factors.snapshot()
    raw_headlines = gold_news.collect(buckets=buckets, days=14, per_factor=8)

    # RSS titles are third-party text headed straight for a prompt. Strip any
    # prompt structure out of them and flag instruction-like items before the
    # model ever sees them.
    headlines, violations = gr.sanitize_feed(raw_headlines)
    violations += gr.check_feed_health(headlines, buckets)
    feed_block = gold_news.as_prompt_block(headlines)

    # Best-effort AI enrichment on top of the feed. One grounded call, not one
    # per bucket — the free search quota is measured in a handful per day.
    grounded, search_ok = "", False
    if enrich:
        try:
            grounded, _srcs = web_research(GOLD_MODEL, _news_prompt(today, buckets))
            search_ok = True
        except AgentUnavailable as e:
            grounded = f"(AI search enrichment unavailable: {e})"

    d = snap["drivers"]
    quant = (
        f"GOLDBEES Rs{snap['etf']['price']} "
        f"(1d {snap['etf']['chg_1d']}%, 1m {snap['etf']['chg_1m']}%, "
        f"1y {snap['etf']['chg_1y']}%), RSI14 {snap['etf']['rsi14']}, "
        f"{snap['etf']['pct_from_high52']}% from the 52w high of "
        f"Rs{snap['etf']['high52']}, annualised vol {snap['etf']['ann_vol_1y_pct']}%.\n"
        f"Gold ${d['gold_usd_oz']['last']}/oz (1m {d['gold_usd_oz']['chg_1m']}%, "
        f"1y {d['gold_usd_oz']['chg_1y']}%). USDINR {d['usdinr']['last']} "
        f"(1y {d['usdinr']['chg_1y']}%). DXY {d['dxy']['last']}. "
        f"US 10y {d['us10y_pct']['last']}%. Brent ${d['brent_usd']['last']} "
        f"(1y {d['brent_usd']['chg_1y']}%). Nifty50 1y {d['nifty50']['chg_1y']}%.\n"
        f"Domestic premium vs the {snap['domestic']['baseline_window']} baseline: "
        f"{snap['domestic']['premium_vs_baseline_pct']:+}% "
        f"(import duty + local demand + fund drag; a jump here means India policy "
        f"moved, not gold).\n"
        f"Return attribution — 1y: ETF {snap['attribution']['1y']['etf_pct']}% = "
        f"gold {snap['attribution']['1y']['gold_usd_pct']}% + INR "
        f"{snap['attribution']['1y']['usdinr_pct']}% + premium "
        f"{snap['attribution']['1y']['premium_pct']}%."
    )

    result = structured_synthesis(
        model=GOLD_MODEL,
        system=SYNTHESIS_SYSTEM,
        prompt=(
            f"Date: {today}.\n\n## Quantitative factor snapshot\n{quant}\n\n"
            f"## Headlines from the factor news feeds\n"
            f"(these are the ONLY items you may turn into events; copy the "
            f"headline, source, date and URL verbatim from here)\n{feed_block}\n\n"
            f"## Additional AI web-search findings (context only — do not cite "
            f"URLs from here)\n{grounded}\n\n"
            f"Select the up-to-12 most material items across the factor buckets, "
            f"discarding duplicates, price-quote filler and retail jewellery "
            f"colour. Then give the overall bias for the rupee gold price over "
            f"the next 1-3 months, a note on what this specifically means for a "
            f"GOLDBEES holder, and 3-5 concrete things to watch next (name the "
            f"event and its date where known)."
        ),
        schema=WATCH_SCHEMA,
        groq_fallback=True,  # discovery is RSS, so Groq can do the scoring alone
    )

    # Provenance, enum and date guards. The feed is the source of record: an
    # event that matches nothing in it keeps its text but loses its URL, because
    # a link cannot be "marked untrusted" — a reader still clicks it.
    for e in result.get("events", []):
        e["id"] = event_key(e)
        e["factor_label"] = FACTORS.get(e.get("factor", ""), {}).get("label", e.get("factor", ""))
    checked, event_violations = gr.check_events(
        result.get("events", []), headlines, set(FACTORS)
    )
    violations += event_violations
    # factor may have been coerced, so the label is refreshed after the check
    for e in checked:
        e["factor_label"] = FACTORS.get(e.get("factor", ""), {}).get("label", e.get("factor", ""))
    result["events"] = checked

    violations += gr.check_prose(result, snap)

    result["snapshot"] = snap
    result["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    result["headlines_scanned"] = len(headlines)
    result["ai_search_used"] = search_ok
    result["violations"] = [v.as_dict() for v in violations]
    result["guardrails"] = gr.summarise(violations)
    return result
