"""Big-shareholders lookup: who holds this stock, how much, and what share.

Indian shareholding patterns are quarterly public filings, and famous-investor
("big shot") holdings are widely reported — so a Google-Search-grounded pass
plus structured synthesis retrieves them well. Cached 30 days by the router.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from typing import Any, Dict

from .runner import AgentUnavailable, structured_synthesis, web_research

# Circuit breaker: after a quota failure, skip AI attempts for a while so the
# UI's Yahoo fallback appears instantly instead of waiting out retry ladders.
_down_until = 0.0
_COOLDOWN_SECONDS = 900

HOLDERS_MODEL = os.environ.get("HOLDERS_MODEL", os.environ.get("ANALYST_MODEL", "gemini-3.5-flash"))

HOLDERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "holders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "promoter", "government", "FII", "DII",
                            "mutual_fund", "insurance", "individual", "other",
                        ],
                    },
                    "pct_of_company": {"type": "number"},
                    "shares": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["name", "category", "pct_of_company", "shares", "note"],
            },
        },
        "as_of": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["holders", "as_of", "summary"],
}

SYSTEM = """You compile the major-shareholder picture of an NSE-listed company for a
retail investor's dashboard, from the search findings provided.

Rules:
- 4 to 10 holders, largest and most notable first: promoter entities, LIC and
  other insurers, big mutual funds, FIIs, and any famous individual investors
  ("big shots") reported to hold the stock.
- pct_of_company is the holder's stake as a percent of the company (e.g. 4.5),
  taken from the findings — never invented. shares is the approximate share
  count as reported (e.g. "3.4 crore" or "34,000,000"); use "" if not reported.
- note: one short phrase of context (e.g. "promoter group", "ace investor").
- as_of: the shareholding-pattern quarter or date the findings refer to.
- summary: one sentence on the ownership structure.
- Only include holders actually present in the findings."""


def fetch_big_holders(symbol: str, name: str) -> Dict[str, Any]:
    global _down_until
    if time.time() < _down_until:
        raise AgentUnavailable(
            "search quota exhausted — retrying automatically in a few minutes"
        )
    today = dt.date.today().isoformat()
    nse_code = symbol.replace(".NS", "")
    try:
        findings, sources = web_research(
            model=HOLDERS_MODEL,
            prompt=(
                f"Latest shareholding pattern of {name} (NSE: {nse_code}), today {today}: "
                f"promoter holding percentage, FII and DII holding, largest institutional "
                f"shareholders (LIC, mutual funds), and any well-known individual investors "
                f"or 'super investors' holding stakes, with their percentage holdings and "
                f"share counts. Include the quarter the data refers to."
            ),
        )
    except AgentUnavailable:
        _down_until = time.time() + _COOLDOWN_SECONDS
        raise
    source_lines = "\n".join(f"- {s['title']}: {s['url']}" for s in sources) or "(none)"
    result = structured_synthesis(
        model=HOLDERS_MODEL,
        system=SYSTEM,
        prompt=(
            f"Company: {name} ({symbol}).\n\n## Search findings\n{findings}\n\n"
            f"## Sources\n{source_lines}\n\nProduce the holders list."
        ),
        schema=HOLDERS_SCHEMA,
        groq_fallback=False,  # grounded data is the whole point
    )
    result["symbol"] = symbol
    result["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    result["source"] = "ai"
    return result
