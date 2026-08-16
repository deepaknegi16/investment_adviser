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
  the deep dive [AGENTIC_AI_DESIGN.md](AGENTIC_AI_DESIGN.md).
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

- The app is protected by a **login page (JWT auth)**. Default credentials are
  `deepak` / `adviser@123` — change them by setting `AUTH_USERNAME` and
  `AUTH_PASSWORD` in `backend/.env`.
- The floating **💬 Research chat** answers questions grounded (via RAG) in the
  AI research this app has generated — per-stock analyses and screener runs —
  plus a live watchlist snapshot, and cites which research it used.
- **📎 Add your own files to the chat's knowledge**: upload PDFs or text/markdown/
  CSV notes (broker reports, your strategy rules) from the chat header; they are
  chunked, embedded, and used to ground answers. Manage them via the 📚 list.
- The chat's **🎙 mic button** takes voice commands (browser Web Speech API,
  works in Chrome): speak your question and it is transcribed and sent.

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
