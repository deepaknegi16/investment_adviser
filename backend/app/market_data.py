"""Yahoo Finance data layer: quotes, returns, technicals, analyst consensus.

All network access to Yahoo goes through this module so it can be swapped out
if yfinance breaks. Results are cached in-process (prices ~10 min, analyst
consensus 24 h) — Yahoo data is ~15 min delayed anyway.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import pandas as pd
import requests
import yfinance as yf

PRICE_TTL = 600  # seconds
CONSENSUS_TTL = 86400

_lock = threading.Lock()
_history_cache: Dict[str, dict] = {}  # key: sorted symbols tuple -> {ts, data}
_consensus_cache: Dict[str, dict] = {}  # symbol -> {ts, data}


def _rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    last_gain, last_loss = gain.iloc[-1], loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return round(100 - 100 / (1 + rs), 1)


def _pct(closes: pd.Series, trading_days: int) -> Optional[float]:
    """Return % change over roughly `trading_days` sessions."""
    if len(closes) <= trading_days:
        return None
    past = closes.iloc[-(trading_days + 1)]
    now = closes.iloc[-1]
    if pd.isna(past) or pd.isna(now) or past == 0:
        return None
    return round((now / past - 1) * 100, 2)


def _pct_over_days(closes: pd.Series, calendar_days: int) -> Optional[float]:
    """% change vs the last close on/before `calendar_days` ago (calendar time)."""
    now = closes.iloc[-1]
    target = closes.index[-1] - pd.Timedelta(days=calendar_days)
    prior = closes[closes.index <= target]
    if prior.empty:
        # Series starts just after the target (e.g. Yahoo's "5y" window is a
        # few sessions short) — accept the first close within a 30-day grace.
        if (closes.index[0] - target).days <= 30:
            past = closes.iloc[0]
        else:
            return None
    else:
        past = prior.iloc[-1]
    if pd.isna(past) or pd.isna(now) or past == 0:
        return None
    return round((now / past - 1) * 100, 2)


def _download_history(symbols: List[str]) -> Dict[str, pd.Series]:
    """5y of adjusted closes per symbol, batched into one request."""
    data = yf.download(
        tickers=" ".join(symbols),
        period="5y",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    out: Dict[str, pd.Series] = {}
    for sym in symbols:
        closes = None
        try:
            if isinstance(data.columns, pd.MultiIndex):
                level0 = data.columns.get_level_values(0)
                if sym in level0:  # (ticker, field) layout
                    closes = data[sym].get("Close")
                elif "Close" in level0:  # (field, ticker) layout
                    sub = data["Close"]
                    closes = sub[sym] if sym in sub.columns else (
                        sub.squeeze() if len(symbols) == 1 else None
                    )
            elif "Close" in data.columns:  # flat single-ticker layout
                closes = data["Close"]
        except (KeyError, TypeError):
            closes = None
        if closes is not None:
            closes = closes.dropna()
            if not closes.empty:
                out[sym] = closes
    return out


def get_closes(symbols: List[str]) -> Dict[str, pd.Series]:
    key = "|".join(sorted(symbols))
    with _lock:
        entry = _history_cache.get(key)
        if entry and time.time() - entry["ts"] < PRICE_TTL:
            return entry["data"]
    data = _download_history(symbols)
    with _lock:
        _history_cache[key] = {"ts": time.time(), "data": data}
    return data


def get_consensus(symbol: str) -> dict:
    """Analyst consensus from Yahoo (recommendationMean 1=Strong Buy..5=Sell)."""
    with _lock:
        entry = _consensus_cache.get(symbol)
        if entry and time.time() - entry["ts"] < CONSENSUS_TTL:
            return entry["data"]
    result = {
        "mean": None, "label": None, "target": None, "analysts": None,
        "ownership": None,
    }
    try:
        info = yf.Ticker(symbol).info
        mean = info.get("recommendationMean")
        target = info.get("targetMeanPrice")
        insiders = info.get("heldPercentInsiders")
        institutions = info.get("heldPercentInstitutions")
        result = {
            "mean": round(mean, 1) if mean is not None else None,
            "label": (info.get("recommendationKey") or "").replace("_", " ") or None,
            "target": round(target, 2) if target is not None else None,
            "analysts": info.get("numberOfAnalystOpinions"),
            # Insiders ≈ promoter group for Indian listings.
            "ownership": {
                "promoters_pct": round(insiders * 100, 1) if insiders is not None else None,
                "institutions_pct": round(institutions * 100, 1) if institutions is not None else None,
            },
        }
    except Exception:
        pass
    with _lock:
        _consensus_cache[symbol] = {"ts": time.time(), "data": result}
    return result


def get_consensus_bulk(symbols: List[str]) -> Dict[str, dict]:
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(get_consensus, symbols))
    return dict(zip(symbols, results))


def compute_metrics(closes: pd.Series) -> dict:
    """All derived per-symbol numbers from a 5y close series."""
    price = float(closes.iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None
    sma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
    year = closes.iloc[-252:] if len(closes) >= 2 else closes
    high52, low52 = float(year.max()), float(year.min())
    return {
        "price": round(price, 2),
        "day_change_pct": _pct(closes, 1),
        "ret_1w": _pct_over_days(closes, 7),
        "ret_1m": _pct_over_days(closes, 30),
        "ret_1y": _pct_over_days(closes, 365),
        "ret_5y": _pct_over_days(closes, 1826),
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "rsi": _rsi(closes),
        "high52": round(high52, 2),
        "low52": round(low52, 2),
        "pct_from_high52": round((price / high52 - 1) * 100, 1) if high52 else None,
    }


def get_chart(symbol: str, period: str) -> List[dict]:
    """Close series for the chart. period: 1w|1m|1y|5y."""
    yf_period = {"1w": "5d", "1m": "1mo", "1y": "1y", "5y": "5y"}.get(period, "1y")
    interval = "1h" if period == "1w" else "1d"
    hist = yf.Ticker(symbol).history(period=yf_period, interval=interval, auto_adjust=True)
    closes = hist["Close"].dropna()
    return [
        {"t": ts.strftime("%Y-%m-%d %H:%M" if interval == "1h" else "%Y-%m-%d"),
         "c": round(float(v), 2)}
        for ts, v in closes.items()
    ]


def search_nse(query: str) -> List[dict]:
    """Yahoo symbol lookup, filtered to NSE (.NS) equities/ETFs."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 15, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        r.raise_for_status()
        quotes = r.json().get("quotes", [])
    except Exception:
        return []
    return [
        {"symbol": q["symbol"], "name": q.get("longname") or q.get("shortname") or q["symbol"]}
        for q in quotes
        if q.get("symbol", "").endswith(".NS")
    ]


_holders_cache: Dict[str, dict] = {}  # symbol -> {ts, data}
HOLDERS_TTL = 86400


def get_named_holders(symbol: str) -> List[dict]:
    """Named institutional holders from Yahoo (sparse/stale for NSE — best effort)."""
    with _lock:
        entry = _holders_cache.get(symbol)
        if entry and time.time() - entry["ts"] < HOLDERS_TTL:
            return entry["data"]
    holders: List[dict] = []
    try:
        df = yf.Ticker(symbol).institutional_holders
        if df is not None and not df.empty and "Holder" in df.columns:
            for _, row in df.head(8).iterrows():
                pct = row.get("pctHeld")
                shares = row.get("Shares")
                holders.append({
                    "name": str(row.get("Holder")),
                    "pct": round(float(pct) * 100, 2) if pct == pct and pct is not None else None,
                    "shares": int(shares) if shares == shares and shares is not None else None,
                    "as_of": str(row.get("Date Reported"))[:10] if row.get("Date Reported") is not None else None,
                })
    except Exception:
        pass
    with _lock:
        _holders_cache[symbol] = {"ts": time.time(), "data": holders}
    return holders
