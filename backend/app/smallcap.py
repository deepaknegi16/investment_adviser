"""Small- and mid-cap screen: solid base first, promise second.

The Top-20 screener runs over the Nifty 100, where the smallest name is still
~Rs 3 lakh crore. This covers the other end — Rs 1,000 to 60,000 crore — where
returns are larger and so is everything that can go wrong.

The order of operations is deliberate and is the whole design: a name must clear
a **hard base** before its promise is scored at all. Ranking first and filtering
later is how screens surface the exciting small cap that turns out to be
loss-making, leveraged and untradeable.

The base gates, and why each one is there:

| Gate | Threshold | Why |
|---|---|---|
| Liquidity | median daily turnover >= Rs 3 cr | The gate that matters most and that no other view in this app applies. On a name doing Rs 2 cr a day your own order moves the price, and in a falling market you may not get out at all |
| Profitable | trailing P/E > 0 and EPS > 0 | A small cap without earnings is a story, and stories are priced on sentiment |
| Return on equity | >= 12% | "Solid base" in the sense that matters: the business earns on its own capital |
| Leverage | debt/equity <= 100% (waived for lenders) | Small caps fail through the balance sheet far more often than the income statement |
| Revenue not shrinking | >= 0% y/y | Cheap and shrinking is a value trap, not a base |
| Above the 200-day average | price > SMA200 | A base is something price has built ON, not something it is falling through |
| Not already vertical | RSI <= 75 and < 40% above SMA200 | After a parabolic move you are buying the momentum of everyone who got there first |

Survivors are then ranked by the same research-weighted conviction model the
rest of the app uses, so a small cap and a large cap are scored on identical
criteria.

Rejections are recorded with their reason rather than silently dropped — the
names that *just* miss are often more informative than the ones that pass.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import conviction as cv
from . import fundamentals as fnd
from . import market_data

UNIVERSE_PATH = Path(__file__).resolve().parent / "smallmid.json"

MIN_MCAP_CR = 1_000       # below this, liquidity and governance risk dominate
MAX_MCAP_CR = 60_000      # above this it is a large cap the Top-20 already covers
MIN_TURNOVER_CR = 3.0     # median daily traded value
MIN_ROE = 12.0
MAX_DEBT_EQUITY = 100.0
MAX_RSI = 75.0
MAX_EXTENSION_PCT = 40.0  # how far above the 200-day average is still a base


def universe() -> List[Dict[str, str]]:
    return json.loads(UNIVERSE_PATH.read_text())


def _gates(m: Dict[str, Any], f: Dict[str, Any], turnover: Optional[float],
           sector: Optional[str]) -> List[str]:
    """Reasons this name fails the base. Empty list means it passes."""
    fails: List[str] = []
    metrics = (f or {}).get("metrics") or {}
    family = fnd.sector_family(sector)

    mcap = metrics.get("market_cap_cr")
    if mcap is None:
        fails.append("no market cap")
    elif not (MIN_MCAP_CR <= mcap <= MAX_MCAP_CR):
        fails.append(f"market cap Rs{mcap:,} cr outside the small/mid band")

    if turnover is None:
        fails.append("no liquidity data")
    elif turnover < MIN_TURNOVER_CR:
        fails.append(f"illiquid — Rs{turnover} cr traded a day, under Rs{MIN_TURNOVER_CR} cr")

    pe, eps = metrics.get("trailing_pe"), metrics.get("eps")
    if pe is None or pe <= 0 or (eps is not None and eps <= 0):
        fails.append("not profitably valued (no positive trailing earnings)")

    roe = metrics.get("roe")
    if roe is None:
        fails.append("no ROE")
    elif roe < MIN_ROE:
        fails.append(f"ROE {roe}% below {MIN_ROE}%")

    de = metrics.get("debt_to_equity")
    if family != "financial" and de is not None and de > MAX_DEBT_EQUITY:
        fails.append(f"debt/equity {de}% above {MAX_DEBT_EQUITY}%")

    rev = metrics.get("revenue_growth")
    if rev is not None and rev < 0:
        fails.append(f"revenue shrinking ({rev}% y/y)")

    price, sma200 = m.get("price"), m.get("sma200")
    if price is None or sma200 is None:
        fails.append("no 200-day average yet")
    else:
        ext = (price / sma200 - 1) * 100
        if ext < 0:
            fails.append(f"below its 200-day average ({ext:.0f}%)")
        elif ext > MAX_EXTENSION_PCT:
            fails.append(f"{ext:.0f}% above its 200-day average — extended, not basing")

    rsi = m.get("rsi")
    if rsi is not None and rsi > MAX_RSI:
        fails.append(f"RSI {rsi} — overbought")

    return fails


def screen(limit: int = 12) -> Dict[str, Any]:
    """Run the screen. Returns passers (ranked), near-misses, and the gate list."""
    names = universe()
    symbols = [n["symbol"] for n in names]
    by_symbol = {n["symbol"]: n for n in names}

    closes_map = market_data.get_closes(symbols)
    fund_map = market_data.get_fundamentals_bulk(symbols)
    turnover_map = market_data.get_liquidity(symbols)
    cons_map = market_data.get_consensus_bulk(symbols)

    passed: List[Dict[str, Any]] = []
    near: List[Dict[str, Any]] = []
    unassessable: List[str] = []
    for sym in symbols:
        closes = closes_map.get(sym)
        if closes is None or closes.empty:
            unassessable.append(sym)   # no price history is a gap, not a verdict
            continue
        m = market_data.compute_metrics(closes)
        f = fund_map.get(sym) or {}
        sector = f.get("sector")
        if not (f or {}).get("available"):
            # No fundamentals came back — that is a data gap, not a verdict on
            # the company, and must not be reported as a rejection.
            unassessable.append(sym)
            continue
        fails = _gates(m, f, turnover_map.get(sym), sector)
        row = {
            "symbol": sym,
            "name": by_symbol[sym]["name"],
            "sector": sector,
            "price": m.get("price"),
            "market_cap_cr": ((f.get("metrics") or {}).get("market_cap_cr")),
            "turnover_cr": turnover_map.get(sym),
            "roe": (f.get("metrics") or {}).get("roe"),
            "debt_to_equity": (f.get("metrics") or {}).get("debt_to_equity"),
            "trailing_pe": (f.get("metrics") or {}).get("trailing_pe"),
            "revenue_growth": (f.get("metrics") or {}).get("revenue_growth"),
            "ret_1y": m.get("ret_1y"),
            "ret_1m": m.get("ret_1m"),
            "ann_vol": m.get("ann_vol"),
            "rsi": m.get("rsi"),
            "pct_from_high52": m.get("pct_from_high52"),
        }
        if fails:
            if len(fails) == 1:
                near.append({**row, "missed_on": fails})
        else:
            row["_conv_input"] = {
                "symbol": sym, "price": m.get("price"), "sma200": m.get("sma200"),
                "ret_1y": m.get("ret_1y"), "ret_1m": m.get("ret_1m"),
                "ann_vol": m.get("ann_vol"),
                "consensus_mean": (cons_map.get(sym) or {}).get("mean"),
                "sector": sector, "fundamentals": f.get("metrics"),
            }
            passed.append(row)

    # Rank survivors on the same factors the rest of the app uses.
    scored = cv.score_basket([p["_conv_input"] for p in passed]) if passed else {}
    for p in passed:
        entry = scored.get(p["symbol"], {})
        p["conviction"] = entry.get("conviction", 0.0)
        p["signal"] = cv.explain(entry)
        p.pop("_conv_input", None)
    passed.sort(key=lambda r: -(r["conviction"] or 0))

    near.sort(key=lambda r: -(r.get("ret_1y") or 0))
    return {
        "shares": passed[:limit],
        "near_misses": near[:6],
        "n_universe": len(symbols),
        "n_passed": len(passed),
        "n_assessed": len(symbols) - len(unassessable),
        "n_unassessable": len(unassessable),
        "gates": {
            "market_cap_cr": [MIN_MCAP_CR, MAX_MCAP_CR],
            "min_daily_turnover_cr": MIN_TURNOVER_CR,
            "min_roe_pct": MIN_ROE,
            "max_debt_equity_pct": MAX_DEBT_EQUITY,
            "max_rsi": MAX_RSI,
            "max_extension_above_sma200_pct": MAX_EXTENSION_PCT,
        },
        "coverage_note": (
            f"Screened {len(symbols) - len(unassessable)} of {len(symbols)} names; "
            f"{len(unassessable)} could not be assessed because Yahoo returned no "
            f"price history or fundamentals for them. Those are data gaps, not "
            f"rejections."
            if unassessable else None
        ),
        "risk_note": (
            "Small caps are not large caps with more upside — they are a "
            "different risk. Expect wider spreads, deeper drawdowns in a "
            "correction, thinner analyst coverage, and single-stock events "
            "(promoter pledging, governance, one lost customer) that a "
            "diversified large cap absorbs. The liquidity gate above is the one "
            "that protects you when everyone wants out at once. Informational "
            "research, not personalised financial advice."
        ),
    }
