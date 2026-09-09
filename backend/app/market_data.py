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
FUNDAMENTALS_TTL = 86400

_lock = threading.Lock()
_history_cache: Dict[str, dict] = {}  # symbol -> {ts, data}
_consensus_cache: Dict[str, dict] = {}  # symbol -> {ts, data}
_fundamentals_cache: Dict[str, dict] = {}  # symbol -> {ts, data}


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
    """5y closes per symbol, cached PER SYMBOL rather than per request.

    This used to key the cache on the whole sorted symbol list, so asking for 13
    symbols and then 32 overlapping ones issued two full downloads and cached
    them separately. Once /api/allocation started requesting the union of the
    watchlist and the screener picks, that doubled the load on Yahoo and it began
    dropping symbols from batches — INFY and ITC rendered as "no data available"
    while a direct fetch for them worked fine.

    Caching per symbol means the union request reuses whatever the watchlist
    already fetched, and only genuinely missing symbols go over the wire.
    """
    now = time.time()
    out: Dict[str, pd.Series] = {}
    missing: List[str] = []
    with _lock:
        for sym in symbols:
            entry = _history_cache.get(sym)
            if entry and now - entry["ts"] < PRICE_TTL:
                out[sym] = entry["data"]
            else:
                missing.append(sym)

    if missing:
        # Chunk: a single yf.download with 140 tickers is unreliable and drops
        # names silently. Chunks are also fetched in parallel, so a large screen
        # is bounded by the slowest chunk rather than by the total.
        # Chunks run SERIALLY. yf.download already parallelises internally, so
        # firing four chunks at once stacks concurrency on concurrency and Yahoo
        # answers with empties — measured: one 25-symbol chunk returns 25/25 in
        # 1.8 s, while four in parallel silently lost 113 of 140 symbols and the
        # screen reported them as ordinary rejections.
        chunks = [missing[i:i + 25] for i in range(0, len(missing), 25)]
        fetched: Dict[str, pd.Series] = {}
        for chunk in chunks:
            try:
                fetched.update(_download_history(chunk))
            except Exception:
                pass

        # Retry stragglers in PARALLEL and bounded. This loop used to be serial
        # and unbounded, which turned a partially-failed 140-symbol batch into
        # 140 sequential downloads and made the screen endpoint hang for minutes.
        still_missing = [s for s in missing if s not in fetched][:30]
        if still_missing:
            def one(sym):
                try:
                    return _download_history([sym])
                except Exception:
                    return {}
            with ThreadPoolExecutor(max_workers=3) as pool:
                for part in pool.map(one, still_missing):
                    fetched.update(part)

        with _lock:
            for sym, series in fetched.items():
                _history_cache[sym] = {"ts": now, "data": series}
        out.update(fetched)
    return out


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


def _ann_vol(closes: pd.Series) -> Optional[float]:
    """Annualised volatility from ~1y of daily log returns, in percent.

    Needed for position sizing: allocating equal rupees to a 20%-vol and a
    45%-vol stock gives the second one more than twice the risk contribution.
    """
    import numpy as np

    if len(closes) < 60:
        return None
    rets = np.log(closes.iloc[-253:]).diff().dropna()
    if rets.empty:
        return None
    vol = float(rets.std()) * (252 ** 0.5) * 100
    return round(vol, 1) if vol > 0 else None


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
        "ann_vol": _ann_vol(closes),
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


# ---------------------------------------------------------------- fundamentals

def _usdinr() -> Optional[float]:
    """Spot USDINR, used to repair Yahoo's mixed-currency ratios (see below)."""
    try:
        closes = get_closes(["USDINR=X"]).get("USDINR=X")
        if closes is not None and not closes.empty:
            return float(closes.iloc[-1])
    except Exception:
        pass
    return None


def _beta_vs_nifty(symbol: str) -> Optional[float]:
    """Beta against the Nifty 50 over ~2 years of daily returns.

    Yahoo's own `beta` for NSE tickers is measured against a US index, which
    produces values like -0.09 for ITC — not credible against its own market.
    We already hold the price history, so the honest number is cheap to compute.
    """
    try:
        data = get_closes([symbol, "^NSEI"])
        stock, index = data.get(symbol), data.get("^NSEI")
        if stock is None or index is None:
            return None
        import numpy as np

        df = pd.concat({"s": stock, "i": index}, axis=1).dropna().iloc[-504:]
        if len(df) < 120:
            return None
        rs = np.log(df["s"]).diff().dropna()
        ri = np.log(df["i"]).diff().dropna()
        aligned = pd.concat({"s": rs, "i": ri}, axis=1).dropna()
        var = float(aligned["i"].var())
        if var == 0:
            return None
        return round(float(aligned["s"].cov(aligned["i"])) / var, 2)
    except Exception:
        return None


def _pct(value, scale=100.0):
    return round(value * scale, 2) if isinstance(value, (int, float)) else None


def get_fundamentals(symbol: str) -> dict:
    """Normalised fundamental metrics for an NSE symbol.

    Two Yahoo quirks are corrected here rather than shown to the user raw:

    1. **Mixed currencies.** Some Indian companies report financials in USD
       while their quote is in INR (Infosys: currency=INR, financialCurrency=USD).
       Yahoo divides an INR market cap by USD revenue, so P/S comes back as 206
       instead of 2.2 and EV/EBITDA as 977 instead of 10.3 — both off by exactly
       the USDINR rate. Ratios that mix the two are rescaled, and `repaired`
       records that it happened.
    2. **Beta against the wrong index** — recomputed against the Nifty 50.

    Returns {"available": False} for ETFs and anything without fundamentals.
    """
    with _lock:
        entry = _fundamentals_cache.get(symbol)
        if entry and time.time() - entry["ts"] < FUNDAMENTALS_TTL:
            return entry["data"]

    result: Dict[str, object] = {"available": False, "symbol": symbol}
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        info = {}

    if info.get("trailingPE") is None and info.get("marketCap") is None:
        # ETFs (GOLDBEES) and index funds have no fundamentals to show.
        result["reason"] = (
            "No fundamentals published for this symbol — ETFs and index funds "
            "hold assets rather than running a business, so P/E, ROE and margins "
            "do not exist for them."
        )
        with _lock:
            _fundamentals_cache[symbol] = {"ts": time.time(), "data": result}
        return result

    quote_ccy = info.get("currency")
    fin_ccy = info.get("financialCurrency")
    mixed = bool(quote_ccy and fin_ccy and quote_ccy != fin_ccy)
    fx = _usdinr() if mixed else None
    repaired: List[str] = []

    def fix_ratio(value, key):
        """Rescale a ratio built from an INR numerator and a USD denominator."""
        if value is None:
            return None
        if mixed and fx:
            repaired.append(key)
            return round(value / fx, 2)
        return round(value, 2)

    market_cap = info.get("marketCap")
    metrics = {
        "trailing_pe": round(info["trailingPE"], 2) if info.get("trailingPE") else None,
        "forward_pe": round(info["forwardPE"], 2) if info.get("forwardPE") else None,
        "peg": round(info["pegRatio"], 2) if info.get("pegRatio")
               else (round(info["trailingPegRatio"], 2) if info.get("trailingPegRatio") else None),
        "price_to_book": round(info["priceToBook"], 2) if info.get("priceToBook") else None,
        "price_to_sales": fix_ratio(info.get("priceToSalesTrailing12Months"), "price_to_sales"),
        "ev_to_ebitda": fix_ratio(info.get("enterpriseToEbitda"), "ev_to_ebitda"),
        "roe": _pct(info.get("returnOnEquity")),
        "roe_derived": False,
        "roa": _pct(info.get("returnOnAssets")),
        "profit_margin": _pct(info.get("profitMargins")),
        "operating_margin": _pct(info.get("operatingMargins")),
        "revenue_growth": _pct(info.get("revenueGrowth")),
        "earnings_growth": _pct(info.get("earningsGrowth")),
        # Yahoo already returns debtToEquity and dividendYield as percentages.
        "debt_to_equity": round(info["debtToEquity"], 1) if info.get("debtToEquity") is not None else None,
        "current_ratio": round(info["currentRatio"], 2) if info.get("currentRatio") else None,
        "dividend_yield": round(info["dividendYield"], 2) if info.get("dividendYield") else None,
        "payout_ratio": _pct(info.get("payoutRatio")),
        "beta": _beta_vs_nifty(symbol),
        "market_cap_cr": round(market_cap / 1e7) if market_cap else None,  # 1 crore = 1e7
        "eps": round(info["trailingEps"], 2) if info.get("trailingEps") else None,
        "book_value": round(info["bookValue"], 2) if info.get("bookValue") else None,
    }

    # Yahoo publishes returnOnEquity for only ~7% of Indian small caps but gives
    # EPS and book value per share for ~100% of them, and ROE is just the ratio
    # of the two. Without this a small-cap quality screen rejects almost
    # everything for missing data rather than for weak fundamentals — which is a
    # far worse error than a slightly imprecise ROE, since the derived figure
    # uses ending rather than average equity and lands within ~1.5 points of
    # Yahoo's own where both exist.
    if metrics["roe"] is None:
        eps, bvps = info.get("trailingEps"), info.get("bookValue")
        if eps is not None and bvps:
            metrics["roe"] = round(100.0 * eps / bvps, 2)
            metrics["roe_derived"] = True

    result = {
        "available": True,
        "symbol": symbol,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "quote_currency": quote_ccy,
        "financial_currency": fin_ccy,
        "mixed_currency": mixed,
        "usdinr_used": round(fx, 2) if fx else None,
        "repaired": repaired,
        "metrics": metrics,
    }
    with _lock:
        _fundamentals_cache[symbol] = {"ts": time.time(), "data": result}
    return result


_warming: set = set()


def warm_fundamentals(symbols: List[str]) -> None:
    """Populate the fundamentals cache in the background, once per symbol.

    Without this the suggested-weight column changes as you browse: value and
    quality carry half the conviction weighting, and they only exist for symbols
    whose fundamentals have been fetched. Fetching them inline would put ~13
    Yahoo `info` calls on a 60-second poll; fetching them in a background thread
    costs the first load nothing and every later load is a 24 h cache hit.
    """
    todo = [s for s in symbols
            if s not in _warming and cached_fundamentals(s) is None]
    if not todo:
        return
    _warming.update(todo)

    def run():
        try:
            for sym in todo:
                try:
                    get_fundamentals(sym)
                except Exception:
                    pass  # a warm-up must never surface an error
        finally:
            _warming.difference_update(todo)

    threading.Thread(target=run, daemon=True, name="warm-fundamentals").start()


def cached_fundamentals(symbol: str) -> Optional[dict]:
    """Fundamentals from cache only — never triggers a fetch (60 s poll path)."""
    with _lock:
        entry = _fundamentals_cache.get(symbol)
    return (entry or {}).get("data")


def cached_sector(symbol: str) -> Optional[str]:
    """Sector from the fundamentals cache only — never triggers a fetch.

    The watchlist is polled every 60 seconds; blocking it on ~13 Yahoo `info`
    calls would make the main table as slow as the AI panels it deliberately
    avoids waiting on. Sector arrives once the drawer has been opened for a
    symbol, and until then allocation simply treats it as unknown.
    """
    with _lock:
        entry = _fundamentals_cache.get(symbol)
    if not entry:
        return None
    return (entry.get("data") or {}).get("sector")


def get_fundamentals_bulk(symbols: List[str], retry: bool = True) -> Dict[str, dict]:
    """Fundamentals for several symbols at once (24 h cache, so usually free).

    Yahoo starts returning empty `info` under load, and on a 140-symbol screen
    that silently looks identical to "this company has no fundamentals". One
    slower retry pass over the blanks converts most of them, and modest
    concurrency avoids provoking the limit in the first place.
    """
    with ThreadPoolExecutor(max_workers=4) as pool:
        out = dict(zip(symbols, pool.map(get_fundamentals, symbols)))
    if not retry:
        return out
    blanks = [s for s, f in out.items() if not (f or {}).get("available")]
    if blanks:
        time.sleep(1.5)
        with _lock:
            for s in blanks:
                _fundamentals_cache.pop(s, None)  # force a real refetch
        with ThreadPoolExecutor(max_workers=2) as pool:
            out.update(dict(zip(blanks, pool.map(get_fundamentals, blanks))))
    return out


_liquidity_cache: Dict[str, dict] = {}


def get_liquidity(symbols: List[str]) -> Dict[str, Optional[float]]:
    """Median daily traded value in Rs crore, over ~3 months.

    The metric that matters most for a small cap and is missing from every other
    view in this app. A company can screen perfectly on ROE and still be
    untradeable: on a name doing Rs 2 crore a day, a retail order moves the price
    against you and an exit during a fall may not clear at all. Screening small
    caps without a liquidity floor is the single most expensive omission possible.
    """
    now = time.time()
    out: Dict[str, Optional[float]] = {}
    missing: List[str] = []
    with _lock:
        for sym in symbols:
            entry = _liquidity_cache.get(sym)
            if entry and now - entry["ts"] < CONSENSUS_TTL:
                out[sym] = entry["data"]
            else:
                missing.append(sym)
    if not missing:
        return out
    try:
        data = yf.download(tickers=" ".join(missing), period="3mo", interval="1d",
                           auto_adjust=False, progress=False, group_by="ticker",
                           threads=True)
    except Exception:
        data = None
    for sym in missing:
        value = None
        try:
            if data is not None and isinstance(data.columns, pd.MultiIndex) and sym in data.columns.get_level_values(0):
                df = data[sym]
                turnover = (df["Close"] * df["Volume"]).dropna()
                if not turnover.empty:
                    value = round(float(turnover.median()) / 1e7, 2)  # Rs crore
            elif data is not None and "Volume" in getattr(data, "columns", []):
                turnover = (data["Close"] * data["Volume"]).dropna()
                if not turnover.empty:
                    value = round(float(turnover.median()) / 1e7, 2)
        except Exception:
            value = None
        out[sym] = value
        with _lock:
            _liquidity_cache[sym] = {"ts": now, "data": value}
    return out
