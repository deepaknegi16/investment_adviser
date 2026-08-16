"""Stock Analyst Agent — news digest + prediction + recommendation for one share.

Runs a three-phase Gemini pipeline (see runner.py): tool research over the
market-data functions, a Google-Search-grounded news pass, then a structured
synthesis constrained to ANALYSIS_SCHEMA.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict

from .runner import AgentUnavailable, structured_synthesis, tool_research, web_research
from .tools import FUNCTION_DECLS, TOOL_IMPLS

# Deep per-stock research: best free-tier Gemini model with search grounding.
ANALYST_MODEL = os.environ.get("ANALYST_MODEL", "gemini-3.5-flash")

ANALYSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "news": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "source": {"type": "string"},
                    "url": {"type": "string"},
                    "date": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["headline", "source", "url", "date", "summary"],
            },
        },
        "prediction": {
            "type": "object",
            "properties": {
                "short_term": {"type": "string"},
                "long_term": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["short_term", "long_term", "confidence"],
        },
        "recommendation": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "reasoning": {"type": "string"},
    },
    "required": ["news", "prediction", "recommendation", "reasoning"],
}

RESEARCH_SYSTEM = """You are an equity research analyst covering the Indian stock
market (NSE). Use the tools to build the quantitative picture for the requested
share: current technicals and analyst consensus, plus price history where trend
context helps. Finish with concise research notes (bullet points) covering trend,
momentum, valuation signals, and what the analyst consensus implies. Notes only —
no recommendation yet."""

SYNTHESIS_SYSTEM = """You are an equity research analyst covering the Indian stock
market (NSE), producing grounded analysis for a retail investor's dashboard.

Rules:
- 3 to 6 news items drawn ONLY from the news findings provided, each mapped to a
  real source and URL from the provided source list. If a field is unknown, use
  an empty string — never invent URLs or dates.
- The prediction must state the key drivers and risks, not just a direction.
- The recommendation weighs technicals, analyst consensus, and news together.
- This is informational research, not personalized financial advice."""


def analyze_stock(symbol: str, name: str) -> Dict[str, Any]:
    today = dt.date.today().isoformat()

    notes = tool_research(
        model=ANALYST_MODEL,
        system=RESEARCH_SYSTEM,
        prompt=(
            f"Build the quantitative picture for {name} (symbol {symbol}) "
            f"as of {today}."
        ),
        function_decls=FUNCTION_DECLS,
        tool_impls=TOOL_IMPLS,
    )

    try:
        news_text, sources = web_research(
            model=ANALYST_MODEL,
            prompt=(
                f"Find recent news (prefer the last 30 days, today is {today}) about "
                f"{name} (NSE: {symbol.replace('.NS', '')}) — quarterly results, order "
                f"wins, regulatory events, management changes, and sector developments. "
                f"Summarize each item with its source and date."
            ),
        )
    except AgentUnavailable:
        # The free search-grounding quota is separate and small — degrade to a
        # technicals-only analysis rather than failing the whole run.
        news_text, sources = (
            "(News search is unavailable right now — daily free search quota "
            "reached. Base the analysis on the quantitative research only and "
            "return an empty news list.)",
            [],
        )
    source_lines = "\n".join(f"- {s['title']}: {s['url']}" for s in sources) or "(none)"

    result = structured_synthesis(
        model=ANALYST_MODEL,
        system=SYNTHESIS_SYSTEM,
        prompt=(
            f"Produce the final analysis of {name} ({symbol}) as of {today}.\n\n"
            f"## Quantitative research notes\n{notes}\n\n"
            f"## News findings (from web search)\n{news_text}\n\n"
            f"## Available sources (title: url)\n{source_lines}\n\n"
            f"Return: news digest, short-term (1-3 months) and long-term (1-3 years) "
            f"outlook with confidence, a BUY/HOLD/SELL call, and reasoning in 3-5 "
            f"sentences."
        ),
        schema=ANALYSIS_SCHEMA,
        groq_fallback=False,  # the news grounding is Gemini-only; keep phases consistent
    )
    result["symbol"] = symbol
    result["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return result
