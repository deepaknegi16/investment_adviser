"""Scenario projection for GOLDBEES, built from its actual price identity.

    GOLDBEES  =  k  x  gold_usd_per_oz  x  USDINR / 31.1035

Forecasting the ETF directly is guesswork. Forecasting its three inputs is a
research question with published answers: sell-side gold targets, rupee
forecasts, and India's import-duty regime. So each scenario is an explicit path
for the three drivers, and the ETF price falls out of the arithmetic. That
makes every projection auditable — you can disagree with an input and see
exactly what it does to the output.

The uncertainty band is a lognormal cone at GOLDBEES' own realised volatility,
not a confidence interval on the scenarios. The scenarios say where the drivers
could go; the cone says how wide the distribution is regardless.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List

from . import gold_factors

TROY_OZ_GRAMS = gold_factors.TROY_OZ_GRAMS

# Anchors are dated so the model ages visibly instead of silently.
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "bear": {
        "label": "Higher-for-longer",
        "probability": 0.25,
        "thesis": (
            "The Fed follows through on a hiking cycle to break the energy-driven "
            "inflation impulse. Real yields grind higher, Western ETF outflows "
            "continue, an Iran de-escalation drains the war premium, and a calmer "
            "oil price lets the rupee stabilise. India partially rolls back the "
            "May-2026 duty hike once the current-account panic fades."
        ),
        "gold_usd": {"2026-12": 3900, "2027-12": 3700},
        "usdinr": {"2026-12": 93.0, "2027-12": 94.0},
        "k": {"2026-12": 0.00915, "2027-12": 0.00900},
    },
    "base": {
        "label": "Range-bound, rupee does the work",
        "probability": 0.50,
        "thesis": (
            "Gold consolidates the 2025-26 melt-up in a broad $4,000-5,000 range: "
            "central-bank buying (record Q2 pace, PBoC still adding) absorbs "
            "Western ETF selling, while high real yields cap the upside. The rupee "
            "keeps depreciating ~3%/yr on an oil-inflated import bill, and the 15% "
            "import duty stays. Rupee gold outperforms dollar gold, as it has all "
            "year."
        ),
        "gold_usd": {"2026-12": 4600, "2027-12": 5000},
        "usdinr": {"2026-12": 96.0, "2027-12": 99.0},
        "k": {"2026-12": 0.00930, "2027-12": 0.00930},
    },
    "bull": {
        "label": "Debasement trade resumes",
        "probability": 0.25,
        "thesis": (
            "The Fed blinks on hiking into a slowing economy, real yields roll "
            "over, and the de-dollarisation bid broadens. Sell-side targets in the "
            "$6,000-6,300 range (J.P. Morgan, Wells Fargo for end-2027) are met. "
            "A wider conflict or a fiscal scare adds a war premium; the rupee "
            "slides harder and Indian physical demand widens the domestic premium."
        ),
        "gold_usd": {"2026-12": 5400, "2027-12": 6300},
        "usdinr": {"2026-12": 98.0, "2027-12": 103.0},
        "k": {"2026-12": 0.00940, "2027-12": 0.00950},
    },
}

SOURCES = [
    {"label": "J.P. Morgan — $6,000/oz avg Q4-2026, ~$6,300 end-2027",
     "url": "https://www.jpmorgan.com/insights/global-research/commodities/gold-prices"},
    {"label": "Goldman Sachs / HSBC / StoneX — end-2026 revised into $4,000-4,900",
     "url": "https://goldsilver.com/industry-news/article/gold-price-forecast-2026-2027-key-predictions-from-top-analysts/"},
    {"label": "Wells Fargo — $5,300-5,500 end-2026, $5,800-6,000 end-2027",
     "url": "https://goldsilver.com/industry-news/article/gold-price-forecast-predictions/"},
    {"label": "World Gold Council — mid-year outlook and Q1/Q2 2026 demand",
     "url": "https://www.gold.org/goldhub/research/gold-mid-year-outlook-2026"},
    {"label": "India import duty raised to 15% (May 2026)",
     "url": "https://www.cnbc.com/2026/05/13/india-hikes-bullion-import-duties-to-arrest-rupee-slide.html"},
]


def _month_ends(start: dt.date, end: dt.date) -> List[dt.date]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        out.append(nxt - dt.timedelta(days=1))
        y, m = nxt.year, nxt.month
    return out


def _interp(anchors: Dict[str, float], spot: float, today: dt.date, when: dt.date) -> float:
    """Log-linear path from today's spot through the dated anchors."""
    pts = [(today, spot)] + [
        (dt.date(int(k[:4]), int(k[5:7]), 28), v) for k, v in sorted(anchors.items())
    ]
    if when <= pts[0][0]:
        return pts[0][1]
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if when <= d1:
            span = (d1 - d0).days or 1
            w = (when - d0).days / span
            return math.exp(math.log(v0) * (1 - w) + math.log(v1) * w)
    return pts[-1][1]


def project(horizon: str = "2027-12") -> Dict[str, Any]:
    """Monthly scenario paths plus a realised-vol cone around the base case."""
    snap = gold_factors.snapshot()
    today = dt.date.fromisoformat(snap["as_of"])
    spot_gold = snap["drivers"]["gold_usd_oz"]["last"]
    spot_fx = snap["drivers"]["usdinr"]["last"]
    spot_k = snap["domestic"]["k_now"]
    spot_etf = snap["etf"]["price"]
    vol = snap["etf"]["ann_vol_1y_pct"] / 100.0

    end = dt.date(int(horizon[:4]), int(horizon[5:7]), 28)
    months = _month_ends(today, end)

    paths: Dict[str, List[Dict[str, Any]]] = {}
    for name, sc in SCENARIOS.items():
        rows = []
        for d in months:
            g = _interp(sc["gold_usd"], spot_gold, today, d)
            fx = _interp(sc["usdinr"], spot_fx, today, d)
            k = _interp(sc["k"], spot_k, today, d)
            rows.append({
                "t": d.isoformat(),
                "etf": round(k * g * fx / TROY_OZ_GRAMS, 2),
                "gold_usd": round(g, 0),
                "usdinr": round(fx, 2),
                "k": round(k, 6),
            })
        paths[name] = rows

    cone = []
    for i, d in enumerate(months):
        years = max((d - today).days, 1) / 365.25
        sd = vol * math.sqrt(years)
        mid = paths["base"][i]["etf"]
        cone.append({
            "t": d.isoformat(),
            "lo": round(mid * math.exp(-sd), 2),
            "hi": round(mid * math.exp(sd), 2),
        })

    expected = {}
    for horizon_key in ("2026-12", "2027-12"):
        idx = next((i for i, d in enumerate(months) if d.strftime("%Y-%m") == horizon_key), None)
        if idx is None:
            continue
        ev = sum(SCENARIOS[n]["probability"] * paths[n][idx]["etf"] for n in SCENARIOS)
        expected[horizon_key] = {
            "probability_weighted": round(ev, 2),
            "vs_spot_pct": round((ev / spot_etf - 1) * 100, 1),
            "bear": paths["bear"][idx]["etf"],
            "base": paths["base"][idx]["etf"],
            "bull": paths["bull"][idx]["etf"],
        }

    return {
        "as_of": snap["as_of"],
        "spot": {"etf": spot_etf, "gold_usd": spot_gold, "usdinr": spot_fx, "k": spot_k},
        "scenarios": {
            n: {kk: sc[kk] for kk in ("label", "probability", "thesis", "gold_usd", "usdinr", "k")}
            for n, sc in SCENARIOS.items()
        },
        "paths": paths,
        "cone_1sd": cone,
        "realised_vol_pct": snap["etf"]["ann_vol_1y_pct"],
        "expected": expected,
        "sources": SOURCES,
        "disclaimer": (
            "Scenario arithmetic over published third-party driver forecasts, not "
            "a prediction. Informational research, not personalised financial advice."
        ),
    }


def sensitivity(gold_grid=None, fx_grid=None) -> Dict[str, Any]:
    """End-2027 GOLDBEES across a gold x rupee grid, holding the premium fixed."""
    snap = gold_factors.snapshot()
    k = snap["domestic"]["k_now"]
    gold_grid = gold_grid or [3500, 4000, 4500, 5000, 5500, 6000, 6500]
    fx_grid = fx_grid or [90, 94, 98, 102, 106]
    return {
        "k": round(k, 6),
        "gold_grid": gold_grid,
        "fx_grid": fx_grid,
        "table": [
            [round(k * g * fx / TROY_OZ_GRAMS, 1) for g in gold_grid] for fx in fx_grid
        ],
    }
