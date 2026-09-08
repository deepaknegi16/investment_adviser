"""Quantitative snapshot of everything that moves GOLDBEES.

GOLDBEES is a rupee-denominated claim on domestic physical gold, so its price
is the product of three independent things:

    GOLDBEES  ~=  k  x  ( gold_usd_per_oz  x  USDINR / 31.1035 )

where k is the units of gold backing one ETF unit *times* the domestic premium
(import duty + local demand/supply + the fund's expense drag). Tracking k over
time is what separates "gold moved" from "the rupee moved" from "India changed
the import duty" — three drivers that need completely different news watching.

Everything here is deterministic maths over Yahoo data. No AI.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import market_data

TROY_OZ_GRAMS = 31.1035

ETF = "GOLDBEES.NS"
GOLD_USD = "GC=F"       # COMEX gold futures, USD/oz
USDINR = "USDINR=X"
DXY = "DX-Y.NYB"        # US dollar index
US10Y = "^TNX"          # US 10-year yield, already quoted in percent
BRENT = "BZ=F"          # oil — the 2026 inflation channel
NIFTY = "^NSEI"         # what GOLDBEES is diversifying against
SILVER = "SI=F"

SYMBOLS = [ETF, GOLD_USD, USDINR, DXY, US10Y, BRENT, NIFTY, SILVER]

# Baseline window used to define "normal" domestic premium: the four months
# before the 2026 import-duty cycle began.
PREMIUM_BASELINE = ("2025-01-01", "2025-04-30")


def _aligned() -> pd.DataFrame:
    """Daily closes for every driver on one calendar, forward-filled."""
    closes = market_data.get_closes(SYMBOLS)
    frame = {}
    for sym, series in closes.items():
        s = series.copy()
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        frame[sym] = s[~s.index.duplicated(keep="last")]
    df = pd.DataFrame(frame).ffill()
    return df.dropna(subset=[c for c in (ETF, GOLD_USD, USDINR) if c in df.columns])


def _chg(series: pd.Series, sessions: int) -> Optional[float]:
    if len(series) <= sessions:
        return None
    prev = float(series.iloc[-1 - sessions])
    if prev == 0:
        return None
    return round((float(series.iloc[-1]) / prev - 1) * 100, 2)


def _attribute(df: pd.DataFrame, sessions: int) -> Optional[Dict[str, float]]:
    """Split the ETF's move over `sessions` into gold / FX / premium legs."""
    if len(df) <= sessions:
        return None
    now, then = df.iloc[-1], df.iloc[-1 - sessions]
    return {
        "etf_pct": round((now[ETF] / then[ETF] - 1) * 100, 2),
        "gold_usd_pct": round((now[GOLD_USD] / then[GOLD_USD] - 1) * 100, 2),
        "usdinr_pct": round((now[USDINR] / then[USDINR] - 1) * 100, 2),
        "premium_pct": round((now["k"] / then["k"] - 1) * 100, 2),
    }


def snapshot() -> Dict[str, Any]:
    """The full factor board: levels, momentum, attribution, correlations."""
    df = _aligned()
    df["intl_inr_per_gram"] = df[GOLD_USD] * df[USDINR] / TROY_OZ_GRAMS
    df["k"] = df[ETF] / df["intl_inr_per_gram"]

    base = df["k"].loc[PREMIUM_BASELINE[0]:PREMIUM_BASELINE[1]]
    base_k = float(base.mean()) if not base.empty else float(df["k"].iloc[0])
    premium_now = (float(df["k"].iloc[-1]) / base_k - 1) * 100

    etf = df[ETF]
    ret = np.log(etf).diff().dropna()
    year = etf.iloc[-252:]

    ytd_start = df.loc[f"{dt.date.today().year - 1}-12-20":]
    ytd = None
    if not ytd_start.empty:
        s, e = ytd_start.iloc[0], df.iloc[-1]
        ytd = {
            "etf_pct": round((e[ETF] / s[ETF] - 1) * 100, 2),
            "gold_usd_pct": round((e[GOLD_USD] / s[GOLD_USD] - 1) * 100, 2),
            "usdinr_pct": round((e[USDINR] / s[USDINR] - 1) * 100, 2),
            "premium_pct": round((e["k"] / s["k"] - 1) * 100, 2),
        }

    def level(sym: str, scale: float = 1.0) -> Dict[str, Any]:
        if sym not in df.columns:
            return {"available": False}
        s = df[sym].dropna()
        return {
            "available": True,
            "last": round(float(s.iloc[-1]) * scale, 2),
            "chg_1w": _chg(s, 5),
            "chg_1m": _chg(s, 21),
            "chg_3m": _chg(s, 63),
            "chg_1y": _chg(s, 252),
        }

    corr_window = ret.iloc[-252:]
    def corr(sym: str) -> Optional[float]:
        if sym not in df.columns:
            return None
        other = np.log(df[sym]).diff().reindex(corr_window.index)
        c = corr_window.corr(other)
        return None if pd.isna(c) else round(float(c), 2)

    return {
        "as_of": df.index[-1].date().isoformat(),
        "etf": {
            "symbol": ETF,
            "price": round(float(etf.iloc[-1]), 2),
            "chg_1d": _chg(etf, 1),
            "chg_1w": _chg(etf, 5),
            "chg_1m": _chg(etf, 21),
            "chg_3m": _chg(etf, 63),
            "chg_6m": _chg(etf, 126),
            "chg_1y": _chg(etf, 252),
            "sma20": round(float(etf.rolling(20).mean().iloc[-1]), 2),
            "sma50": round(float(etf.rolling(50).mean().iloc[-1]), 2),
            "sma200": round(float(etf.rolling(200).mean().iloc[-1]), 2),
            "rsi14": market_data._rsi(etf),
            "high52": round(float(year.max()), 2),
            "low52": round(float(year.min()), 2),
            "pct_from_high52": round((float(etf.iloc[-1]) / float(year.max()) - 1) * 100, 1),
            "ann_vol_1y_pct": round(float(ret.iloc[-252:].std()) * np.sqrt(252) * 100, 1),
        },
        "drivers": {
            "gold_usd_oz": level(GOLD_USD),
            "usdinr": level(USDINR),
            "dxy": level(DXY),
            "us10y_pct": level(US10Y),
            "brent_usd": level(BRENT),
            "nifty50": level(NIFTY),
            "silver_usd_oz": level(SILVER),
        },
        "domestic": {
            "k_now": round(float(df["k"].iloc[-1]), 6),
            "k_baseline": round(base_k, 6),
            "baseline_window": f"{PREMIUM_BASELINE[0]}..{PREMIUM_BASELINE[1]}",
            "premium_vs_baseline_pct": round(premium_now, 2),
            "intl_inr_per_gram": round(float(df["intl_inr_per_gram"].iloc[-1]), 1),
        },
        "attribution": {
            "1m": _attribute(df, 21),
            "3m": _attribute(df, 63),
            "1y": _attribute(df, 252),
            "ytd": ytd,
        },
        "correlations_1y": {
            "gold_usd": corr(GOLD_USD),
            "usdinr": corr(USDINR),
            "dxy": corr(DXY),
            "nifty50": corr(NIFTY),
        },
    }


def history(days: int = 1100) -> List[Dict[str, Any]]:
    """Daily series for charting: ETF, gold USD, USDINR and the premium ratio."""
    df = _aligned().iloc[-days:]
    df = df.assign(k=df[ETF] / (df[GOLD_USD] * df[USDINR] / TROY_OZ_GRAMS))
    return [
        {
            "t": idx.date().isoformat(),
            "etf": round(float(row[ETF]), 2),
            "gold_usd": round(float(row[GOLD_USD]), 1),
            "usdinr": round(float(row[USDINR]), 3),
            "k": round(float(row["k"]), 6),
        }
        for idx, row in df.iterrows()
    ]
