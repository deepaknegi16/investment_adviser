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
  (provider breakdown, Groq-fallback rate, avg similarity, latency), and the
  latest eval results. Details: AGENTIC_AI_DESIGN.md §12.

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
