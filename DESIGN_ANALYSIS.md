# Design Analysis — options considered and why we chose what we chose

Companion to [DESIGN.md](DESIGN.md). Each section lists the realistic options,
the trade-offs, and the decision with its rationale.

## 1. Backend language & framework

| Option | Pros | Cons |
|---|---|---|
| **Python + FastAPI** ✅ | `yfinance` gives free NSE data in 3 lines; first-class SDKs for every AI provider; pandas for indicator math; fastest to build | GIL limits CPU parallelism (irrelevant here — workload is I/O) |
| Java + Spring Boot (original idea) | Familiar enterprise stack, strong typing | No good free market-data library; AI SDK support weaker; far more boilerplate for the same endpoints |
| Node.js + Express | One language with the frontend | Financial/indicator libraries much weaker than pandas |

**Decision:** Python/FastAPI. The deciding factor was the data layer: `yfinance`
plus pandas does in ~200 lines what Java would need a paid data vendor and a
math library for. (User approved the switch from Java.)

## 2. Market data source

| Option | Pros | Cons |
|---|---|---|
| **Yahoo Finance via yfinance** ✅ | Free, no key, no signup; covers NSE equities *and* ETFs (`GOLDBEES.NS`); 5y+ adjusted history; bonus: analyst consensus + price targets | Unofficial (can break), ~15 min delayed, occasional rate limits |
| Paid APIs (Alpha Vantage, Twelve Data) | Official, stable contracts | Free tiers far too small (e.g. 25 calls/day vs our 110-symbol screener); paid tiers cost money for a personal app |
| Scraping nseindia.com | Official exchange numbers | NSE actively blocks bots; breaks constantly; ToS risk |

**Decision:** Yahoo Finance. Mitigations for its downsides: all Yahoo access
isolated in one module (`market_data.py`) so it's swappable, batched downloads,
and 10-min/24-h caches. Delayed quotes are acceptable for a daily-decision
dashboard.

## 3. Storage

| Option | Pros | Cons |
|---|---|---|
| **SQLite (embedded)** ✅ | Zero setup, single file, survives restarts, perfect for one user | Not multi-writer |
| PostgreSQL/MySQL | Scales to multi-user | Requires install/Docker; overkill for a watchlist + two cache tables |
| JSON files | Simplest possible | No atomic updates, easy to corrupt |

**Decision:** SQLite via SQLAlchemy — the schema is three tiny tables; anything
heavier is pure operational overhead. (User picked embedded DB.)

## 4. Frontend

| Option | Pros | Cons |
|---|---|---|
| **React + Vite (SPA)** ✅ | User asked for React; Vite dev server + `/api` proxy is trivial; SPA fits a single-dashboard app | SEO irrelevant here anyway |
| Next.js | SSR, routing | Server rendering buys nothing for a private dashboard; heavier toolchain |
| Plain HTML/JS | No build step | Drawer/dialog/table state gets messy fast |

**Chart:** hand-rolled ~40-line SVG line chart instead of recharts/chart.js — we
render one simple close-price line; a charting dependency would be 100× the code
we actually use. Revisit if candlesticks/volume are ever wanted.

## 5. AI provider

The provider changed twice, each time for a practical reason, and each swap
touched only `agents/runner.py` + glue — validating the isolation of the agent
loop behind one module:

1. **Anthropic Claude** (original plan) — dropped for credential availability.
2. **OpenAI (gpt-5 family)** — dropped because the account had no free credits.
3. **Google Gemini free tier + Groq fallback** ✅ — current.

| Option | Pros | Cons |
|---|---|---|
| **Gemini free tier** ✅ | The only free tier covering *all* our needs: function calling, **Google Search grounding** (the analyst's core need), JSON-schema output, and free embeddings; ~1,000–1,500 req/day on Flash/Flash-Lite | Flash-tier quality below paid flagships; free-tier data may be used by Google to improve products; Pro models are paid-only since Apr 2026 |
| Groq free tier | Independent quota, very fast Llama 3.3 70B, no card | No web search; tight tokens/min |
| OpenRouter `:free` pool | Many models | 50 req/day, no search, quality varies |
| Local (Ollama) | Unlimited + private | No web search; weaker models for the analyst's job |
| Paid (OpenAI/Anthropic) | Best quality | Requires credits — the constraint this switch removes |

**Combination chosen:** Gemini primary for everything; Groq as an automatic
fallback for the screener synthesis and chat when Gemini's per-minute/day limits
trip. The analyst's news phase stays Gemini-only, since Groq cannot search.
Gemini's constraint that search grounding, function tools, and JSON schemas
can't share one request shaped the runner into a three-phase pipeline
(tool research → grounded news → synthesis) instead of a single tool loop.

## 6. Model selection per task

| Option | Analysis |
|---|---|
| One model for everything | Simple, but burns the scarce quota (Flash: fewer daily requests) on mechanical tasks |
| **Split by task** ✅ | `gemini-2.5-flash` for the Analyst — the deepest reasoning available with search grounding on the free tier. `gemini-2.5-flash-lite` for the Screener and chat — highest daily quota for mechanical/conversational work. `gemini-embedding-001` for RAG (free, 10M tokens/min). Groq `llama-3.3-70b-versatile` as the fallback lane. All env-overridable. |

On a free stack the split is about *quota allocation* as much as cost: spend the
better model's limited daily requests only where quality shows.

## 7. Agentic architecture

| Option | Pros | Cons |
|---|---|---|
| Single prompt, no tools ("stuff data in, ask for JSON") | One API call, cheap | Model can't fetch what it discovers it needs; news would be from training data → stale/hallucinated |
| **Tool-using agent loop** ✅ | Model *pulls* technicals/history on demand and searches live news; every claim grounded in a tool result | More round-trips, needs loop/turn-limit machinery |
| Multi-agent (separate news, technical, ranking agents + orchestrator) | Separation of concerns | 3–4× the API calls and latency for a two-task app; coordination complexity buys nothing at this scale |

**Decision:** shared agentic primitives (tool loop, grounded search, structured
synthesis), composed per agent. That is the smallest design that is genuinely
agentic — the model decides what to look up in its research phase — without
multi-agent overhead. On Gemini the primitives run as separate phases (its API
won't mix them in one request); the behavior is the same.

**Screener specifically — hybrid over pure-AI:** letting the model screen all
110 names would mean ~110 tool calls per refresh. Instead code computes the
momentum/trend scores (deterministic, free, instant) and the model only judges
the 30 survivors. Rule of thumb applied throughout: *arithmetic in code, judgment
in the model.*

## 8. Recommendation engine

| Option | Pros | Cons |
|---|---|---|
| AI for every table row | Richest reasoning | A dozen model calls per table refresh — slow and expensive for a 60-second polling loop |
| Rules only | Free, instant, explainable | Blind to news and context |
| **Hybrid** ✅ | Table uses transparent rules (trend + RSI + momentum blended with Yahoo analyst consensus); AI recommendation appears in the detail view, on demand, cached | Two advice values can disagree — surfaced deliberately as "base advice" vs "AI advice" |

The status color is intentionally simple and explainable: 🟢 price > SMA50 >
SMA200 with positive 1-month return; 🔴 the mirror image; 🟠 anything mixed.

## 9. News sourcing

| Option | Pros | Cons |
|---|---|---|
| **Gemini Google Search grounding** ✅ | Fresh, server-side, free-tier grounded quota, returns citation URLs the UI links to; the model filters for relevance | Grounding can't share a request with function tools (hence the phased pipeline) |
| Google News RSS scraping | Free | Fragile parsing, no relevance filtering, redirects instead of source URLs |
| Paid news APIs | Structured | Another key + subscription for a personal app |

## 10. AI cost & latency controls

Decisions stacked to keep spend near zero on idle days:

1. **Cache per day** — analysis keyed `(symbol, date)`, picks keyed `date`;
   repeat views cost nothing.
2. **AI only on demand** — nothing in the 60-second polling path calls a model.
3. **Free pre-screen** shrinks the screener's model workload 110 → 30 names.
4. **Cheaper model + low effort** where the task is mechanical (§6).
5. **Thinned tool payloads** (chart series capped at ~60 points) and a **12-turn
   loop limit** as a runaway guard.

## 11. Authentication

| Option | Pros | Cons |
|---|---|---|
| **JWT (HS256) + env-configured credentials** ✅ | Stateless (no session store), standard Bearer flow, one dependency (PyJWT); creds live next to the other secrets in `.env` | Tokens can't be revoked before expiry (24 h TTL bounds the exposure) |
| Session cookies + server-side sessions | Revocable | Needs session storage and CSRF handling; the SPA + Bearer pattern is simpler |
| Users table with hashed passwords | Multi-user ready | Password management (hashing, reset flows) for an app with exactly one user |
| OAuth (Google etc.) | No password at all | External app registration + callback plumbing — heavy for localhost |

The JWT secret is auto-generated into a gitignored file so restarts don't
invalidate sessions and nothing secret enters the repo.

## 12. Chat grounding (RAG)

| Option | Pros | Cons |
|---|---|---|
| **Embeddings in SQLite + in-process cosine search** ✅ | Zero new infrastructure; corpus is tiny (one chunk per analysis/screener run) so brute-force cosine over numpy is instant; degrades to keyword search if embeddings fail | Wouldn't scale to millions of chunks — irrelevant here |
| Vector DB (Chroma, Qdrant, pgvector) | Scales, filters | A whole service + dependency for a corpus that fits in memory |
| No RAG — stuff all research into the prompt | Simple | Grows unboundedly with usage; retrieval keeps the prompt small and the answer focused |
| Agent-with-tools chat (chat calls the analyst live) | Always fresh | Minutes of latency + fresh cost per chat message; RAG over cached research answers instantly |

Retrieval feeds `gemini-2.5-flash-lite` (Groq fallback) together with a live watchlist snapshot, so the
chat can answer both "what did the research say" and "where is my portfolio now".

## 13. Voice input

| Option | Pros | Cons |
|---|---|---|
| **Browser Web Speech API** ✅ | Free, zero backend, instant, built into Chrome (the user's browser) | Chrome-centric; sends audio to the browser vendor's recognizer |
| Cloud transcription APIs (Whisper etc.) | Best accuracy, any browser | Audio upload plumbing + per-minute cost + more latency |
| Local speech models | Private | Heavy install for a convenience feature |

## 14. Secret handling

API keys (`GEMINI_API_KEY`, `GROQ_API_KEY`) live only in `backend/.env` (gitignored, verified before every
push). `.env.example` documents the shape without the value. The key never
appears in code, docs, logs, or the frontend — the browser talks only to our
backend.
