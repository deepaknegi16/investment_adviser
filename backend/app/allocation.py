"""Suggested position sizing across a basket of shares.

Two ideas do the work, both standard and both deliberately transparent:

1. **Inverse-volatility weighting (risk parity).** Equal rupees is not equal
   risk. A 45%-volatility small cap and a 20%-volatility large cap held at the
   same weight contribute wildly different amounts of portfolio movement, and
   the volatile one quietly dominates the outcome. Dividing the target weight by
   volatility equalises the risk each name contributes.

2. **Conviction tilt.** The inverse-vol weight is multiplied by a 0-1 conviction
   score built from the same technical + consensus signals as the BUY/HOLD/SELL
   badge, tilted by the fundamental pillars where they exist. Names the model
   dislikes get nothing.

Then three hard constraints, because the failure mode of any scoring model is
concentration: a single-name cap, a sector cap, and a floor below which a
position is not worth holding.

WHAT THIS IS NOT: it does not know the user's income, age, horizon, tax
situation, existing assets or cash needs, so it cannot be personal advice. The
weights are a share of *this basket*, not of anyone's net worth, and they are
the mechanical output of the inputs listed above. `explain()` returns those
inputs so the number is never a black box.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MAX_SINGLE_PCT = 15.0     # no single name dominates the basket
MAX_SECTOR_PCT = 35.0     # nor does one sector
MIN_KEEP_PCT = 2.0        # below this a position is noise, not diversification
DEFAULT_VOL = 30.0        # used when history is too short to measure

# The model never suggests going all-in on weak conviction. The deployed share
# of the basket scales with average conviction, and the rest is explicitly cash.
MIN_DEPLOYED = 0.35
MAX_DEPLOYED = 1.0


def _conviction(blended_score: Optional[float], pillars: Optional[Dict[str, Any]]) -> float:
    """0..1 conviction from the technical/consensus score, tilted by fundamentals."""
    score = blended_score if blended_score is not None else 0.0
    # The blended score runs roughly -6..+6; -1 maps to zero so mildly negative
    # names get nothing rather than a token slice.
    base = (score + 1.0) / 6.0
    base = max(0.0, min(1.0, base))

    if pillars:
        net = sum(p.get("net", 0) for p in pillars.values())
        n = len(pillars) or 1
        # +/-20% swing at most, so fundamentals refine rather than override.
        base *= max(0.8, min(1.2, 1.0 + (net / n) * 0.1))
    return max(0.0, min(1.0, base))


def _apply_caps(weights: Dict[str, float], sectors: Dict[str, Optional[str]],
                budget: float) -> Dict[str, float]:
    """Normalise to `budget`, then enforce single-name and sector caps.

    The two caps interact: capping a name frees weight that can push a sector
    over, and scaling a sector down frees weight that can push a name over. They
    are therefore applied in an alternating loop until both hold, rather than
    once each — applying them in sequence lets the second silently undo the
    first.
    """
    def normalise(w: Dict[str, float], total: float) -> Dict[str, float]:
        s = sum(w.values())
        return {k: v / s * total for k, v in w.items()} if s > 0 else dict(w)

    w = normalise({k: v for k, v in weights.items() if v > 0}, budget)
    if not w:
        return {}

    for _ in range(25):
        changed = False

        # --- single-name cap: freeze offenders, redistribute over the rest ---
        over = {k for k, v in w.items() if v > MAX_SINGLE_PCT + 1e-6}
        if over:
            changed = True
            frozen = {k: MAX_SINGLE_PCT for k in over}
            rest = {k: v for k, v in w.items() if k not in over}
            remaining = budget - sum(frozen.values())
            if remaining <= 0 or not rest:
                w = frozen
                break
            w = {**frozen, **normalise(rest, remaining)}

        # --- sector cap: scale the sector down, spread the excess elsewhere ---
        totals: Dict[str, float] = {}
        for k, v in w.items():
            totals.setdefault(sectors.get(k) or "Unknown", 0.0)
            totals[sectors.get(k) or "Unknown"] += v
        breach = [sec for sec, t in totals.items()
                  if t > MAX_SECTOR_PCT + 1e-6 and sec != "Unknown"]
        for sec in breach:
            members = {k: v for k, v in w.items() if (sectors.get(k) or "Unknown") == sec}
            others = {k: v for k, v in w.items() if k not in members}
            if not others:
                continue  # single-sector basket: unsatisfiable, reported below
            changed = True
            scale = MAX_SECTOR_PCT / totals[sec]
            freed = sum(members.values()) * (1 - scale)
            for k in members:
                w[k] *= scale
            s = sum(others.values())
            for k in others:
                w[k] += freed * (others[k] / s) if s else 0.0

        if not changed:
            break

    # Drop dust, then renormalise — which can reintroduce a breach, so cap the
    # single-name limit once more and give any leftover back to cash.
    w = {k: v for k, v in w.items() if v >= MIN_KEEP_PCT}
    if not w:
        return {}
    w = normalise(w, budget)
    w = {k: min(v, MAX_SINGLE_PCT) for k, v in w.items()}
    return w


def suggest(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Suggested weights for a basket.

    Each item: {symbol, blended_score?, ann_vol?, sector?, pillars?}
    Returns {weights: {symbol: pct}, cash_pct, basis: {...}, per_symbol: {...}}
    """
    if not items:
        return {"weights": {}, "cash_pct": 100.0, "per_symbol": {}, "basis": {}}

    convictions: Dict[str, float] = {}
    vols: Dict[str, float] = {}
    sectors: Dict[str, Optional[str]] = {}
    raw: Dict[str, float] = {}

    for it in items:
        sym = it["symbol"]
        c = _conviction(it.get("blended_score"), it.get("pillars"))
        vol = it.get("ann_vol") or DEFAULT_VOL
        convictions[sym] = c
        vols[sym] = vol
        sectors[sym] = it.get("sector")
        raw[sym] = (c / vol) if c > 0 else 0.0

    positive = [c for c in convictions.values() if c > 0]
    mean_conv = sum(positive) / len(positive) if positive else 0.0
    deployed = max(MIN_DEPLOYED, min(MAX_DEPLOYED, mean_conv * 1.6))
    if not positive:
        deployed = 0.0
    budget = round(deployed * 100, 1)

    weights = _apply_caps(raw, sectors, budget)
    weights = {k: round(v, 1) for k, v in weights.items()}
    allocated = round(sum(weights.values()), 1)

    # Constraints that could not be honoured are stated, never silently dropped.
    warnings: List[str] = []
    sector_totals: Dict[str, float] = {}
    for sym, pct in weights.items():
        sec = sectors.get(sym) or "Unknown"
        sector_totals[sec] = sector_totals.get(sec, 0) + pct
    for sec, total in sector_totals.items():
        if sec != "Unknown" and total > MAX_SECTOR_PCT + 0.05:
            warnings.append(
                f"{sec} is {total:.0f}% of the suggested allocation, above the "
                f"{MAX_SECTOR_PCT:.0f}% sector cap. The cap cannot be applied "
                f"because this basket has too few names outside {sec} — the "
                f"concentration is in your list, not in the model."
            )
    n_held = len(weights)
    if n_held and allocated < budget - 1 and n_held * MAX_SINGLE_PCT < budget:
        warnings.append(
            f"Only {n_held} names clear the bar, and no single name may exceed "
            f"{MAX_SINGLE_PCT:.0f}%, so at most {n_held * MAX_SINGLE_PCT:.0f}% can "
            f"be deployed. The remainder sits in cash rather than being forced "
            f"into positions the model does not favour."
        )
    if not weights:
        warnings.append(
            "No name scores positively right now, so the model suggests holding "
            "cash rather than allocating into weakness."
        )

    per_symbol = {
        sym: {
            "suggested_pct": weights.get(sym, 0.0),
            "conviction": round(convictions[sym], 2),
            "ann_vol": round(vols[sym], 1),
            "sector": sectors.get(sym),
            "reason": _reason(sym, weights.get(sym, 0.0), convictions[sym], vols[sym]),
        }
        for sym in convictions
    }
    return {
        "weights": weights,
        "cash_pct": round(100 - allocated, 1),
        "warnings": warnings,
        "per_symbol": per_symbol,
        "basis": {
            "method": "conviction-tilted inverse-volatility (risk parity)",
            "deployed_pct": allocated,
            "mean_conviction": round(mean_conv, 2),
            "caps": {"single_name_pct": MAX_SINGLE_PCT, "sector_pct": MAX_SECTOR_PCT,
                     "drop_below_pct": MIN_KEEP_PCT},
            "note": (
                "A share of THIS basket, not of your net worth. Sized by risk "
                "contribution (weight ÷ volatility) and conviction, then capped "
                "for concentration. The model does not know your horizon, taxes, "
                "income or other assets, so it cannot be personal advice."
            ),
        },
    }


def _reason(symbol: str, pct: float, conviction: float, vol: float) -> str:
    if pct <= 0 and conviction <= 0:
        return "No allocation — the technical and consensus signals are negative."
    if pct <= 0:
        return (f"No allocation — conviction {conviction:.2f} was too low to clear "
                f"the {MIN_KEEP_PCT}% minimum once caps were applied.")
    band = "high" if conviction >= 0.6 else ("moderate" if conviction >= 0.4 else "low")
    vol_band = "low" if vol < 25 else ("elevated" if vol < 40 else "high")
    return (f"{pct}% — {band} conviction ({conviction:.2f}) at {vol_band} volatility "
            f"({vol:.0f}% annualised). Weight is conviction divided by volatility, "
            f"then capped at {MAX_SINGLE_PCT}% per name.")
