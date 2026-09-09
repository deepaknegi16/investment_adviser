# Indian Stock Portfolio Adviser

Personal dashboard for tracking NSE shares: live-ish prices (Yahoo Finance, ~15 min
delayed), 1W/1M/1Y/5Y performance, a green/orange/red trend status, and a rule-based
buy/hold/sell recommendation per share. Clicking a share opens a detail view with a
price chart, technicals, and — powered by Claude agents — a recent-news digest,
short/long-term prediction, and an AI recommendation with reasoning. A second table
shows the AI screener's top-20 picks from the NSE large-cap universe.

> ⚠ Everything here is informational. It is **not financial advice**.

## Stack

- **Backend**: Python (FastAPI) + `yfinance` for market data + SQLite for the
  watchlist and AI-result caches.
- **Agentic AI (free tier)**: Google Gemini — `gemini-2.5-flash` analyst
  (function tools + Google Search grounding for news) and `gemini-2.5-flash-lite`
  screener/chat, with automatic Groq (Llama 3.3 70B) fallback when Gemini rate
  limits. Results are cached per day to stay well inside the free quotas.
  See [DESIGN.md](DESIGN.md), [DESIGN_ANALYSIS.md](DESIGN_ANALYSIS.md), and
  the deep dive [AGENTIC_AI_DESIGN.md](AGENTIC_AI_DESIGN.md). Interview
  prep on the AI concepts: [prepare.md](prepare.md).
- **Frontend**: React (Vite), dark dashboard UI.

## Setup

### 1. Backend

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env       # then add your keys (see below)
.venv/bin/uvicorn app.main:app --port 8000
```

**API keys (both free, no card):**
- `GEMINI_API_KEY` — create at [aistudio.google.com/apikey](https://aistudio.google.com/apikey). Required for AI analysis, picks, and chat.
- `GROQ_API_KEY` — optional, from [console.groq.com](https://console.groq.com). Fallback for chat/screener when Gemini rate-limits.

Without keys the price table, chart, and add/remove features all work; the AI
features return a message explaining what's missing.

### 2. Frontend

```sh
cd frontend
npm install
npm run dev                # http://localhost:5173
```

The Vite dev server proxies `/api` to the backend on port 8000.

## Login, chat, and voice

- The app is protected by a **login page (JWT auth)**. There is **no default
  password** — set `AUTH_USERNAME` and `AUTH_PASSWORD` in `backend/.env` or the
  backend refuses to start. The old example value `adviser@123` is rejected by
  name, since it is published in this repo. Generate a strong one:

  ```bash
  python3 -c "import secrets,string; a=''.join(c for c in string.ascii_letters+string.digits if c not in 'O0Il1'); print('-'.join(''.join(secrets.choice(a) for _ in range(5)) for _ in range(3)))"
  ```

  Rotating the password does not end existing sessions on its own — JWTs stay
  valid for their 24-hour TTL. Delete `backend/jwt_secret.key` as well to force
  everyone to log in again.
- The floating **💬 Research chat** answers questions grounded (via RAG) in the
  AI research this app has generated — per-stock analyses and screener runs —
  plus a live watchlist snapshot, and cites which research it used.
- **📎 Add your own files to the chat's knowledge**: upload PDFs or text/markdown/
  CSV notes (broker reports, your strategy rules) from the chat header; they are
  chunked, embedded, and used to ground answers. Manage them via the 📚 list.
- The chat's **🎙 mic button** takes voice commands (browser Web Speech API,
  works in Chrome): speak your question and it is transcribed and sent.

## Quality: evaluation & metrics

- `backend/eval_rag.py` scores the RAG pipeline: retrieval (recall@5, MRR over
  an auto-generated golden set) and generation (Gemini-as-judge faithfulness +
  relevance). Run: `.venv/bin/python eval_rag.py [--no-judge] [--judge-sample N]`.
- `GET /api/metrics` (JWT) reports corpus health, cache state, chat quality
  (provider breakdown, Groq-fallback rate, avg similarity, latency), the latest
  RAG eval results, **gold-watch stats** (sweeps, bias, events by factor, email
  config state) and **guardrail counters** (violations by kind, trusted-event
  rate, suppressed alerts, last `eval_gold.py` verdict).
  Details: AGENTIC_AI_DESIGN.md §12.

## Notes

- The watchlist is seeded on first run with: Infosys, Wipro, Goldbees, Adani Green,
  HDFC Bank, ONGC, BEL, PNB, ATGL, ITC, LIC, SBI. Add more via **＋ Add share**
  (search by company name; NSE symbols only).
- AI analysis is cached per share per day in `backend/adviser.db`; use
  **↻ Refresh AI analysis** / **↻ Refresh picks** to force a rerun. A full agent run
  can take a few minutes.
- The screener universe lives in `backend/app/nifty100.json` — edit it to widen or
  narrow the top-20 candidate pool.
- Yahoo Finance access is unofficial and occasionally rate-limits; all Yahoo calls
  are isolated in `backend/app/market_data.py` and cached (prices 10 min, analyst
  consensus 24 h).

## Suggested position sizing

Both tables carry a **Suggested** column, and they read **one** allocation over
the union of your holdings and the screener candidates — so the two columns plus
cash sum to 100%, and a stock appearing in both lists shows a single number.
(They used to be sized as separate baskets, which implied 190% of a portfolio and
gave HAL 15% in one table and 5% in the other.)

Conviction is scored on **continuous cross-sectional ranks**, weighted by how
strong the published evidence for each factor actually is:

| Component | Weight |
|---|---|
| Momentum (12m excluding the last month) | 25% |
| Quality / profitability | 25% |
| Value | 25% |
| Long-term trend (vs 200-day) | 15% |
| Analyst consensus | 10% |

That conviction is then divided by volatility, so each position contributes
similar *risk* rather than similar rupees, and capped at 15% per name / 35% per
sector.

**The first version of this was measured and thrown away.** It scored from step
functions ("+1 if price > SMA50") and produced **17.4% one-way turnover per day**
— you would have been trading a sixth of your portfolio daily, handing the
difference to brokerage, STT and slab-rate short-term capital gains tax. It also
made analyst consensus the loudest input while fundamentals contributed nothing.
The rewrite brought daily turnover to **2.2%**.

These are **targets, not daily instructions**: weights are rounded to 0.5% steps
and the response carries a 3-percentage-point no-trade band. Rebalance quarterly
at most.

Nothing gives a name a higher weight for already being held — conviction depends
on the metrics only. The real limit is the opposite: the watchlist basket *is*
your holdings, so it can rebalance among them but never suggest something you do
not own. That is what the screener table is for.

**A share of that basket, not of your net worth.** The model knows nothing about
your income, horizon, taxes or other assets. Hover any cell for the arithmetic.

## Fundamentals

Open any share and the drawer now shows **20 fundamental metrics** — P/E (trailing
and forward), PEG, P/B, P/S, EV/EBITDA, ROE, ROA, margins, revenue and earnings
growth, debt-to-equity, current ratio, dividend yield, payout ratio, beta, market
cap, EPS and book value.

Click any metric to see **what it is, how to read it, and its caveat**. Above them,
five pillar ratings (value / quality / growth / safety / income) and any classic
**combination playbooks** the numbers currently match — quality compounder, GARP,
classic value, income, plus two warnings: *value trap* (low P/E with falling
revenue and earnings) and *leverage-flattered returns* (high ROE, low ROA, high
debt). Single ratios rarely decide anything; the combinations are where the signal
is.

Two things are corrected rather than shown raw, both invisible in Yahoo's own UI:

- **Mixed currencies.** Infosys quotes in INR but reports financials in USD, so
  Yahoo's P/S reads 205.98 instead of 2.17 and EV/EBITDA 977 instead of 10.28.
  Ratios spanning both currencies are rescaled and marked `fx`.
- **Beta against a US index.** Recomputed against the Nifty 50 — ITC's Yahoo beta
  of −0.09 is not a credible number against its own market.

Bands are sector-relative, and metrics that aren't real quantities for a sector
(a bank's debt-to-equity or gross margin) are hidden rather than flagged red.

`GET /api/stocks/{symbol}/fundamentals` · `GET /api/stocks/fundamentals/guide`

## Gold Watch — GOLDBEES factor monitor

A dedicated agent for `GOLDBEES.NS` (Nippon India ETF Gold BeES). A gold ETF has no
earnings or management, so instead of the stock-analyst pipeline it watches the chain
of macro factors that actually set its price:

```
US real rates / Fed  ->  gold in USD  ->  x USDINR  ->  x India import duty  ->  GOLDBEES
central bank buying      geopolitics / oil          fund expense drag
```

**Modules**

| File | Role |
|---|---|
| `app/gold_factors.py` | Deterministic factor board. Decomposes the ETF into `k × gold_usd × USDINR / 31.1035` and attributes every return window to the gold / rupee / domestic-premium legs. No AI. |
| `app/gold_news.py` | Quota-free RSS discovery across nine factor buckets (Google News per-factor queries + commodity desks). |
| `app/agents/gold_watch.py` | Tags each headline with its factor, direction and materiality; adds an optional Gemini search-grounded enrichment pass; synthesises the overall bias. |
| `app/agents/gold_alerts.py` | Dedupe, the materiality bar, and the HTML/plain-text alert email. |
| `app/gold_scenarios.py` | Scenario projection — explicit driver paths, a ±1σ realised-vol cone, and a gold × rupee sensitivity grid. |
| `app/notify.py` | SMTP delivery, provider-agnostic. |
| `gold_watch_run.py` | CLI entry point for cron. |

**An alert fires only when** a new high-impact event lands, three new medium events
cluster, GOLDBEES moves ≥2% in a session, the domestic premium moves ≥2% in a month
(the import-duty tripwire), the factor bias flips, or RSI-14 leaves the 30–70 band.
Everything else is stored and not mailed.

**Setup**

```bash
# 1. Gmail App Password (needs 2-Step Verification):
#    https://myaccount.google.com/apppasswords  -> paste into SMTP_PASSWORD in backend/.env
cd backend && .venv/bin/python gold_watch_run.py --test-email

# 2. Run a sweep
.venv/bin/python gold_watch_run.py            # email only if material
.venv/bin/python gold_watch_run.py --digest   # always email
.venv/bin/python gold_watch_run.py --dry-run  # research, never email

# 3. Schedule it (crontab -e)
30 9,15 * * 1-5  cd /path/to/backend && .venv/bin/python gold_watch_run.py >> gold_watch.log 2>&1
45 18   * * 5    cd /path/to/backend && .venv/bin/python gold_watch_run.py --digest >> gold_watch.log 2>&1
```

**Guardrails + eval** — the watcher runs unattended, so its output is checked
before it reaches you: provenance (an event that matches no feed keeps its text
but loses its link), enum validity (the Groq fallback does not enforce schemas),
date sanity, prompt-injection detection on RSS headlines, advice-language and
numeric-drift scanning on the prose, alert-rate limits, and a **feed-health check
that sends a distinct "watcher degraded" email rather than reporting calm when
the feeds are dead**. Nothing is silently dropped — violations are stored in
`guardrail_violations` and surfaced in `/api/metrics`.

```bash
cd backend
.venv/bin/python eval_gold.py --adversarial   # 12 guardrail fixtures, no model, <1s
.venv/bin/python eval_gold.py --no-judge      # + tagging accuracy vs the golden set
.venv/bin/python eval_gold.py                 # + LLM judge on the prose
```

Exits non-zero on failure, so it works as a pre-push check.

**API** — `GET /api/gold/factors`, `/history`, `/scenarios`, `/sensitivity`, `/events`,
`/runs`, `/latest`, `/alerts/config`; `POST /api/gold/watch`, `/alerts/test`.

**Dashboard integration** — `/api/stocks/{symbol}/analysis` routes gold ETFs
(`gold_watch.GOLD_ETF_SYMBOLS`) to this agent instead of the equity analyst, projected
into the analyst's payload shape by `gold_alerts.as_stock_analysis()`. Opening GOLDBEES
in the StockDrawer therefore shows factor news and a driver-based outlook with no
frontend change, and the sweep is picked up by the existing RAG indexer so the chat
panel can cite it. Sweep counts, bias history, event breakdown and email-config status
appear under `gold_watch` in `GET /api/metrics`.

There is **no dedicated gold panel in the React app** — the agent is reachable through
the existing drawer, the `/api/gold/*` endpoints and the CLI.
