"""Fundamental metric catalogue: what each number means and how to read it.

This module is the reference half of fundamental analysis — definitions,
interpretation bands, caveats and the classic metric *combinations*. The data
fetching lives in market_data.get_fundamentals(); the scoring in recommend.py.

Two things make a catalogue like this non-trivial for NSE data:

1. **Bands are sector-relative.** A P/E of 30 is rich for a bank and ordinary for
   an FMCG name. Debt-to-equity is a red flag for a manufacturer and is the
   entire business model for a lender. Every band below can be overridden per
   sector, and metrics that are meaningless for a sector are suppressed rather
   than scored (a bank's "gross margin" is not a real quantity).

2. **Some Yahoo fields are wrong for Indian tickers** in a specific, correctable
   way — see market_data.get_fundamentals(). The catalogue flags which metrics
   were repaired so the UI can say so.

Nothing here is advice. It describes what these numbers conventionally indicate
and what combinations investors classically look for; it does not tell anyone
what to do with their money.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Sector families that need different reading. Yahoo's `sector` string is mapped
# onto these.
FIN_SECTORS = {"Financial Services", "Financial"}
CAPITAL_INTENSIVE = {"Utilities", "Energy", "Real Estate", "Basic Materials", "Industrials"}


def sector_family(sector: Optional[str]) -> str:
    if sector in FIN_SECTORS:
        return "financial"
    if sector in CAPITAL_INTENSIVE:
        return "capital_intensive"
    return "general"


# Each metric: what it is, why it matters, how to read it, and where the line is.
# `bands` are (good_below / good_above) style thresholds evaluated in score().
CATALOGUE: Dict[str, Dict[str, Any]] = {
    # ---------------------------------------------------------- valuation
    "trailing_pe": {
        "label": "P/E (trailing)",
        "group": "Valuation",
        "unit": "x",
        "what": "Price divided by the last 12 months of earnings per share. What you pay for ₹1 of current profit.",
        "read": "Lower is cheaper, but cheap often means the market expects profits to fall. Compare against the company's own history and its sector, never against a stock in a different industry.",
        "good": {"lt": 20, "watch_gt": 40},
        "sector_good": {"financial": {"lt": 18, "watch_gt": 30},
                        "capital_intensive": {"lt": 15, "watch_gt": 30}},
        "caveat": "Meaningless when earnings are negative or a one-off gain inflated them.",
        "direction": "lower_better",
    },
    "forward_pe": {
        "label": "P/E (forward)",
        "group": "Valuation",
        "unit": "x",
        "what": "The same ratio using analysts' forecast earnings for the next 12 months.",
        "read": "Forward well below trailing means analysts expect profits to grow. Forward above trailing means they expect a decline.",
        "good": {"lt": 18, "watch_gt": 35},
        "caveat": "Only as good as the forecasts, which cluster optimistic.",
        "direction": "lower_better",
    },
    "peg": {
        "label": "PEG ratio",
        "group": "Valuation",
        "unit": "x",
        "what": "P/E divided by the earnings growth rate. It asks whether a high P/E is justified by fast growth.",
        "read": "Around 1.0 is the classic 'fairly priced for its growth' marker. Below 1 suggests growth is not fully in the price; above 2 means you are paying up for it.",
        "good": {"lt": 1.5, "watch_gt": 3},
        "caveat": "Unstable when growth is near zero — the denominator collapses.",
        "direction": "lower_better",
    },
    "price_to_book": {
        "label": "P/B",
        "group": "Valuation",
        "unit": "x",
        "what": "Price divided by book value (net assets) per share.",
        "read": "The primary valuation tool for banks and asset-heavy businesses, where book value is real. Nearly useless for software or brands, whose value is not on the balance sheet.",
        "good": {"lt": 3, "watch_gt": 8},
        "sector_good": {"financial": {"lt": 2.5, "watch_gt": 5}},
        "caveat": "A P/B under 1 can mean a bargain or a business earning less than its assets are worth.",
        "direction": "lower_better",
    },
    "price_to_sales": {
        "label": "P/S",
        "group": "Valuation",
        "unit": "x",
        "what": "Market value divided by annual revenue.",
        "read": "Useful when profits are small, volatile or temporarily depressed, because revenue is harder to distort than earnings.",
        "good": {"lt": 4, "watch_gt": 10},
        "caveat": "Ignores profitability entirely — high revenue at no margin is not value.",
        "direction": "lower_better",
    },
    "ev_to_ebitda": {
        "label": "EV/EBITDA",
        "group": "Valuation",
        "unit": "x",
        "what": "Enterprise value (market cap plus net debt) against operating earnings before interest, tax, depreciation and amortisation.",
        "read": "Fairer than P/E when comparing companies with very different debt loads, because it prices the whole business rather than just the equity.",
        "good": {"lt": 12, "watch_gt": 20},
        "caveat": "Not meaningful for banks, where debt is raw material rather than financing.",
        "direction": "lower_better",
    },
    # ------------------------------------------------------- profitability
    "roe": {
        "label": "Return on equity",
        "group": "Profitability",
        "unit": "%",
        "what": "Annual profit as a percentage of shareholders' equity — how hard the owners' capital works.",
        "read": "Consistently above ~15% suggests a genuine competitive advantage. The single most-watched quality metric in Indian equity research.",
        "good": {"gt": 15, "watch_lt": 8},
        "caveat": "Debt flatters ROE: a heavily leveraged company can post a high ROE on a thin equity base. Always read it beside debt-to-equity.",
        "direction": "higher_better",
    },
    "roa": {
        "label": "Return on assets",
        "group": "Profitability",
        "unit": "%",
        "what": "Profit as a percentage of everything the company owns.",
        "read": "The leverage-proof companion to ROE. A high ROE with a low ROA means the returns are coming from borrowing, not from the business.",
        "good": {"gt": 8, "watch_lt": 3},
        "sector_good": {"financial": {"gt": 1.5, "watch_lt": 0.7}},
        "caveat": "Banks run ROA near 1-2% by design; do not compare them to manufacturers.",
        "direction": "higher_better",
    },
    "profit_margin": {
        "label": "Net profit margin",
        "group": "Profitability",
        "unit": "%",
        "what": "What fraction of every rupee of revenue survives to the bottom line.",
        "read": "Stable or rising margins point to pricing power. Falling margins on rising revenue means the company is buying growth.",
        "good": {"gt": 10, "watch_lt": 3},
        "direction": "higher_better",
    },
    "operating_margin": {
        "label": "Operating margin",
        "group": "Profitability",
        "unit": "%",
        "what": "Profit from core operations, before interest and tax.",
        "read": "Cleaner than net margin for judging the business itself, since it strips out financing and tax choices.",
        "good": {"gt": 15, "watch_lt": 5},
        "direction": "higher_better",
    },
    # -------------------------------------------------------------- growth
    "revenue_growth": {
        "label": "Revenue growth (y/y)",
        "group": "Growth",
        "unit": "%",
        "what": "Year-on-year change in sales.",
        "read": "The cleanest growth signal — harder to manipulate than earnings. Sustained double digits is strong for a large cap.",
        "good": {"gt": 10, "watch_lt": 0},
        "direction": "higher_better",
    },
    "earnings_growth": {
        "label": "Earnings growth (y/y)",
        "group": "Growth",
        "unit": "%",
        "what": "Year-on-year change in profit.",
        "read": "Earnings growing faster than revenue means margins are expanding — usually the healthiest combination there is.",
        "good": {"gt": 10, "watch_lt": 0},
        "caveat": "Very volatile off a low base; one weak prior year can manufacture a huge percentage.",
        "direction": "higher_better",
    },
    # ---------------------------------------------------- financial health
    "debt_to_equity": {
        "label": "Debt to equity",
        "group": "Financial health",
        "unit": "%",
        "what": "Borrowings as a percentage of shareholders' equity.",
        "read": "Under ~50% is comfortable for most businesses; over 100% means lenders have more at stake than owners, which magnifies both good and bad years.",
        "good": {"lt": 50, "watch_gt": 100},
        "sector_good": {"capital_intensive": {"lt": 100, "watch_gt": 200}},
        "caveat": "Not applicable to banks and NBFCs — borrowing IS the business.",
        "direction": "lower_better",
    },
    "current_ratio": {
        "label": "Current ratio",
        "group": "Financial health",
        "unit": "x",
        "what": "Short-term assets divided by short-term liabilities.",
        "read": "Above 1.5 means comfortable cover for the next year's bills. Below 1 means it depends on refinancing or new cash coming in.",
        "good": {"gt": 1.5, "watch_lt": 1.0},
        "direction": "higher_better",
    },
    # -------------------------------------------------------------- income
    "dividend_yield": {
        "label": "Dividend yield",
        "group": "Income",
        "unit": "%",
        "what": "Annual dividend as a percentage of the current price.",
        "read": "Cash returned to you regardless of share price. In India, dividends are taxed at your slab rate, which matters for high earners.",
        "good": {"gt": 2, "watch_lt": 0},
        "caveat": "An unusually high yield is often a falling price rather than a generous board — check whether the payout is sustainable.",
        "direction": "higher_better",
    },
    "payout_ratio": {
        "label": "Payout ratio",
        "group": "Income",
        "unit": "%",
        "what": "The share of profit paid out as dividends.",
        "read": "Under ~60% leaves room to keep paying through a bad year and still reinvest. Above 100% means the dividend exceeds profit and is being funded from reserves or debt.",
        "good": {"lt": 60, "watch_gt": 90},
        "direction": "lower_better",
    },
    # ---------------------------------------------------------- size & risk
    "beta": {
        "label": "Beta (vs Nifty 50)",
        "group": "Risk",
        "unit": "",
        "what": "How much the stock moves for a 1% move in the Nifty, measured over the last two years.",
        "read": "Above 1 amplifies the index in both directions; below 1 cushions it. This is computed here against the Nifty — Yahoo's own beta is measured against a US index and is not meaningful for an NSE stock.",
        "good": {"lt": 1.2, "watch_gt": 1.6},
        "caveat": "A backward-looking measure of volatility, not of business risk.",
        "direction": "lower_better",
    },
    "market_cap_cr": {
        "label": "Market cap",
        "group": "Risk",
        "unit": "₹ cr",
        "what": "Total value of all shares.",
        "read": "Above ₹1,00,000 cr is large cap territory — generally more liquid, better covered and less volatile. Small caps move further in both directions.",
        "direction": "none",
    },
    "eps": {
        "label": "EPS (trailing)",
        "group": "Per share",
        "unit": "₹",
        "what": "Profit attributable to each share over the last 12 months.",
        "read": "The denominator of the P/E. Growing EPS with a flat price is what makes a stock cheaper over time.",
        "direction": "none",
    },
    "book_value": {
        "label": "Book value per share",
        "group": "Per share",
        "unit": "₹",
        "what": "Net assets per share — what would theoretically remain for shareholders after paying every liability.",
        "read": "The denominator of P/B, and a rough floor for asset-backed businesses.",
        "direction": "none",
    },
}

# Metrics that are not meaningful for a sector family and are hidden rather than
# scored — showing a bank a "gross margin of 0.0%" is worse than showing nothing.
SUPPRESSED: Dict[str, set] = {
    "financial": {"debt_to_equity", "current_ratio", "ev_to_ebitda", "operating_margin"},
}


# The combinations. This is the part people actually want: single ratios almost
# never decide anything, and the same P/E means opposite things beside different
# companions.
PLAYBOOKS: List[Dict[str, Any]] = [
    {
        "name": "Quality compounder",
        "pattern": "High ROE · low debt · steady revenue growth · P/E at or a little above the sector",
        "means": "A business that earns well on its own capital without borrowing to do it. The market usually knows, so these rarely look cheap.",
        "watch": "You are paying for durability. The risk is overpaying at the top of a cycle, not the business breaking.",
        "requires": {"roe": ("gt", 15), "debt_to_equity": ("lt", 60), "revenue_growth": ("gt", 5)},
    },
    {
        "name": "Growth at a reasonable price (GARP)",
        "pattern": "PEG near or below 1 · earnings growth above revenue growth · moderate P/E",
        "means": "Growth that the price has not fully caught up with, and margins expanding rather than growth being bought with discounts.",
        "watch": "Verify the growth is not one weak base year flattering the percentage.",
        "requires": {"peg": ("lt", 1.5), "earnings_growth": ("gt", 10)},
    },
    {
        "name": "Classic value",
        "pattern": "Low P/E and low P/B · positive earnings · manageable debt",
        "means": "Priced below what the assets and current profits suggest. Works when the pessimism is temporary.",
        "watch": "The value trap: cheap because the business is genuinely deteriorating. Falling revenue alongside a low P/E is the warning sign, not a bargain.",
        "requires": {"trailing_pe": ("lt", 15), "price_to_book": ("lt", 2), "revenue_growth": ("gt", 0)},
    },
    {
        "name": "Income / dividend",
        "pattern": "Yield above ~3% · payout ratio under ~70% · stable margins",
        "means": "Cash in hand, with enough profit left over that the dividend can survive a weak year.",
        "watch": "A high yield with a payout above 100% is a dividend about to be cut. Indian dividends are taxed at your slab rate.",
        "requires": {"dividend_yield": ("gt", 3), "payout_ratio": ("lt", 70)},
    },
    {
        "name": "Value trap warning",
        "pattern": "Low P/E · falling revenue AND falling earnings · rising debt",
        "means": "Not a bargain. The multiple is low because profits are expected to keep shrinking, and the P/E will look expensive again once they do.",
        "watch": "This is the pattern most often mistaken for value. Cheapness is a conclusion, not a starting point.",
        "requires": {"trailing_pe": ("lt", 12), "revenue_growth": ("lt", 0), "earnings_growth": ("lt", 0)},
        "negative": True,
    },
    {
        "name": "Leverage-flattered returns",
        "pattern": "High ROE but low ROA · debt to equity above ~100%",
        "means": "The headline return on equity is coming from borrowing rather than from the business. It works until rates rise or a year goes badly.",
        "watch": "Always read ROE beside ROA. A wide gap between them is the tell.",
        "requires": {"roe": ("gt", 15), "roa": ("lt", 5), "debt_to_equity": ("gt", 100)},
        "negative": True,
    },
]


def band_for(key: str, family: str) -> Dict[str, Any]:
    """The good/watch thresholds for a metric, with any sector override applied."""
    meta = CATALOGUE.get(key, {})
    band = dict(meta.get("good") or {})
    override = (meta.get("sector_good") or {}).get(family)
    if override:
        band.update(override)
    return band


def verdict(key: str, value: Optional[float], family: str = "general") -> Optional[str]:
    """'good' | 'ok' | 'watch' for one metric, or None if it is not scored."""
    if value is None:
        return None
    meta = CATALOGUE.get(key, {})
    if meta.get("direction") in (None, "none"):
        return None
    band = band_for(key, family)
    if not band:
        return None
    if "lt" in band and value < band["lt"]:
        return "good"
    if "gt" in band and value > band["gt"]:
        return "good"
    if "watch_gt" in band and value > band["watch_gt"]:
        return "watch"
    if "watch_lt" in band and value < band["watch_lt"]:
        return "watch"
    return "ok"


def _cmp(value: Optional[float], op: str, threshold: float) -> bool:
    if value is None:
        return False
    return value > threshold if op == "gt" else value < threshold


def match_playbooks(values: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
    """Which classic patterns this company's numbers currently fit."""
    out = []
    for pb in PLAYBOOKS:
        needed = pb["requires"]
        known = {k: v for k, v in needed.items() if values.get(k) is not None}
        if len(known) < max(2, len(needed) - 1):
            continue  # too little data to claim a pattern
        hits = sum(1 for k, (op, t) in known.items() if _cmp(values.get(k), op, t))
        if hits == len(known):
            out.append({
                "name": pb["name"], "pattern": pb["pattern"], "means": pb["means"],
                "watch": pb["watch"], "negative": pb.get("negative", False),
                "matched_on": sorted(known),
            })
    return out
