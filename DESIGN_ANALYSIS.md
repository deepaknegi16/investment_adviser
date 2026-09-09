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
| **Split by task** ✅ | `gemini-3.5-flash` for the Analyst — the deepest reasoning available with search grounding on the free tier. `gemini-3.5-flash-lite` for the Screener and chat — highest daily quota for mechanical/conversational work. `gemini-embedding-001` for RAG (free, 10M tokens/min). Groq `llama-3.3-70b-versatile` as the fallback lane. All env-overridable. |

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

**Revised for the standing watcher (§15).** The verdict above still holds for the
Analyst, which runs on demand and can degrade to technicals-only when grounding
is unavailable. It did *not* hold for a monitor that must run unattended: the
first Gold Watch run returned zero events because the free search-grounding quota
was already spent, and it failed silently. RSS was reconsidered and won on the
one axis that matters for a watcher — it never runs out. The listed cons proved
manageable: Google News RSS returns a `<source>` element (no scraping needed) and
the relevance filtering the model was doing is now done by nine narrow per-factor
queries plus a scoring pass over the results.

## 10. AI cost & latency controls

Decisions stacked to keep spend near zero on idle days:

1. **Cache per day** — analysis keyed `(symbol, date)`, picks keyed `date`;
   repeat views cost nothing.
2. **AI only on demand** — nothing in the 60-second polling path calls a model.
3. **Free pre-screen** shrinks the screener's model workload 110 → 30 names.
4. **Cheaper model + low effort** where the task is mechanical (§6).
5. **Thinned tool payloads** (chart series capped at ~60 points) and an **8-turn
   loop limit** (`runner.MAX_TOOL_TURNS`) as a runaway guard.
6. **Unmetered discovery for the standing watcher** — the Gold Watch spends one
   model call per sweep regardless of how many headlines it reads (§15).

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

Retrieval feeds `gemini-3.5-flash-lite` (Groq fallback) together with a live watchlist snapshot, so the
chat can answer both "what did the research say" and "where is my portfolio now".

## 13. Voice input

| Option | Pros | Cons |
|---|---|---|
| **Browser Web Speech API** ✅ | Free, zero backend, instant, built into Chrome (the user's browser) | Chrome-centric; sends audio to the browser vendor's recognizer |
| Cloud transcription APIs (Whisper etc.) | Best accuracy, any browser | Audio upload plumbing + per-minute cost + more latency |
| Local speech models | Private | Heavy install for a convenience feature |

## 14. Secret handling

API keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `SMTP_PASSWORD`) live only in
`backend/.env` (gitignored, verified before every push). `.env.example` documents
the shape without the value. The key never appears in code, docs, logs, or the
frontend — the browser talks only to our backend.

`AUTH_PASSWORD` is the exception that needed extra care, because unlike an API
key its *absence* used to be survivable: the app fell back to a documented
default and kept serving. `.env.example` now ships an empty value, and the auth
gate refuses both the empty case and the old published default by name (§16).
Ephemeral copies count too — a `.env.bak` written during a password rotation is
untracked but **not** gitignored, so one `git add .` would commit every key in
it; rotation deletes its backups once the change is verified rather than leaving
them around.

## 15. Gold ETF coverage: a second agent, or a smarter first one?

| Option | Pros | Cons |
|---|---|---|
| **A separate Gold Watch agent** ✅ | A gold ETF's price is a macro chain, not a company; the questions ("did the duty change?") have no analogue in equity research. Its own factor taxonomy, its own schema, its own materiality rules | A second agent to maintain |
| Teach the Analyst about gold | One agent | The Analyst's whole shape — tool-call technicals, search company news, judge valuation — assumes an issuer. Branching inside it would mean two agents wearing one name |
| Generic "macro mode" for any ETF | Reusable | Speculative generality: one gold ETF is in the watchlist, and the factor taxonomy that makes this useful is gold-specific |

The decomposition `GOLDBEES = k × gold_usd × USDINR / 31.1035` is what justifies
the split. It is not presentational — it is the only way to tell "gold moved"
from "the rupee moved" from "the import duty changed", and those three need
completely different news watched. In 2026 to date the ETF gained ~13% while
dollar gold was flat; an agent watching only "gold news" would have explained
none of it.

### Discovery: RSS vs. AI search

| Option | Pros | Cons |
|---|---|---|
| **RSS discovery + AI scoring** ✅ | Free and unmetered — the watcher cannot go blind; ~2 s for ~70 headlines; real source URLs; the model does judgment, which is what it is good at | Nine query strings to curate; no relevance filtering before the model sees it |
| Gemini search grounding | Model filters relevance; citation URLs | Small free daily quota. Measured, not theorised: the first live run returned **zero events** |
| Paid news API | Reliable, structured | A subscription for a personal dashboard |

Kept as **best-effort enrichment**: grounding still runs when quota allows, but
its absence changes nothing. A second-order benefit — because sources now come
from RSS rather than Gemini's own citations, the synthesis step can fall back to
Groq, which the Analyst's news phase can never do.

### Alerting: what earns an interruption

| Option | Pros | Cons |
|---|---|---|
| **Materiality bar + content-hash dedupe** ✅ | Emails only regime changes, clustered pushes, ≥2% moves, premium shifts, bias flips, RSI extremes. The same story is mailed once | Rules are judgment calls and need tuning |
| Email every new event | Never miss anything | Muted within a week — and a muted watcher is worse than none |
| Daily digest only | Predictable | A duty change at 10am should not wait until 6pm |

Both exist: the bar drives event-triggered mail, `--digest` forces a scheduled
summary. Dedupe is the `gold_events` primary key (SHA-1 of factor + normalised
headline + source), so it is structural — a duplicate insert is a no-op by
construction, not by remembering to check.

### Surfacing it: reuse the drawer, or build a panel?

| Option | Pros | Cons |
|---|---|---|
| **Project the sweep into the Analyst's schema** ✅ | Zero frontend work; the StockDrawer, the per-day cache and the RAG indexer all work unchanged. Opening GOLDBEES simply shows the right analysis | Constrained to the Analyst's field shape; the factor board and scenarios have no UI |
| A dedicated React gold panel | Shows the factor board, attribution and scenario chart properly | A new component, new API client methods, new nav — and the drawer would *still* need routing, or it keeps showing equity analysis for a gold ETF |
| Leave it CLI/API-only | Nothing to build | The dashboard would keep showing the wrong analysis for a watchlist holding |

Routing uses an **explicit symbol list**, not a substring match on "GOLD":
`GOLDIAM` is a jewellery equity and a naive match would send it to a macro agent.

## 16. Failing closed on auth

| Option | Pros | Cons |
|---|---|---|
| **Refuse to start without `AUTH_PASSWORD`** ✅ | The failure is loud, immediate and precedes the first request; the message prints the exact fix | A fresh clone won't boot until configured — which looks like a broken build the first time it bites |
| Fall back to a documented default (the old behaviour) | Always starts | A missing or renamed `.env` silently downgrades the app from "locked" to "open on a password printed in the README", while still serving traffic |
| Warn in the logs and continue | Visible, non-blocking | Nobody reads logs of a working app. The whole failure mode is that it *looks* fine |

Rejecting the old example value **by name** matters as much as rejecting the
empty case: `.env.example` shipped a working password, so anyone copying it
verbatim got a login whose credentials are public in the repo.

Weak-but-valid passwords **warn rather than fail** — a length rule that hard-fails
can lock the owner out of their own dashboard, and this app has exactly one user
who is also its operator. Loud, not fatal, is the right severity there.

Rotating `AUTH_PASSWORD` alone does not end existing sessions: JWTs are signed
with `jwt_secret.key` and stay valid for their 24-hour TTL. A real rotation
deletes that file too. This is the documented trade-off of stateless JWTs from
§11 finally being paid.

## 17. Network exposure for phone access

| Option | Pros | Cons |
|---|---|---|
| **LAN-only: `vite host: true`, backend on `127.0.0.1`** ✅ | Works on any device on the same Wi-Fi in seconds; no third party; the API is reachable only through the dev-server proxy, never directly | Same-network only; the router's DHCP lease can change the IP |
| Tunnel (cloudflared / ngrok) | A public URL from anywhere | Puts a single-user app with no TLS termination of its own, no rate limiting and a hand-set password on the open internet |
| Deploy to a host (Render / Railway / Fly) | Permanent URL, real TLS | Real work: build pipeline, managed secrets, a hosted DB or a persistent disk for SQLite |

Only the SPA was bound to `0.0.0.0`. Exposing uvicorn as well would have been one
extra flag and strictly worse: the proxy already reaches the backend over
loopback, so binding it wide would add attack surface for no capability.

## 18. Guardrails and evaluation for an unattended agent

### Enforcement: drop, flag, or fail?

| Option | Pros | Cons |
|---|---|---|
| **Flag and keep** ✅ | Nothing disappears; the reader sees both the event and the doubt; violations stay auditable in `guardrail_violations` | Junk still reaches the inbox, labelled |
| Drop silently, count it | Inbox stays clean | A noisy model quietly shrinks the report, and a monitor that silently omits things is worse than one that shows you doubt |
| Fail the whole sweep | Regressions are impossible to miss | One bad headline costs the entire sweep — far too brittle for a cron job |

One carve-out from "keep everything": an unverifiable **URL** is stripped rather
than labelled, because a link cannot be marked untrusted in a way that survives a
click. The event text stays, the href goes, and the drawer's existing
`url ? <a> : <b>` renders it as plain text with no frontend change.

### Ground truth: labelled set, judge, or self-consistency?

| Option | Pros | Cons |
|---|---|---|
| **Hand-labelled golden set + judge for prose** ✅ | Deterministic and free for the structured tags; a model judge only where the output is free text and there is no single right answer | ~42 labels to write and maintain by hand |
| LLM judge for everything | No labelling | Burns quota, non-deterministic, and a judge sharing the agent's blind spots agrees with its mistakes |
| Self-consistency (run twice, compare) | Zero labelling, catches flakiness | A model that is confidently and consistently wrong scores perfectly |

The golden set deliberately includes **12 real off-topic headlines** the feeds
actually returned (Solana ETF flows, Samsung layoffs, a cotton import-duty
waiver). Roughly 29% of live feed output is noise, so rejecting it is as much of
the job as labelling the rest — and "cotton import duty waiver" is exactly the
near-miss that a keyword-matching agent would tag as `india_policy`.

### What to floor, and what to merely report

The first run of the harness failed at 0.267 factor accuracy while **every label
the agent had produced was correct**. The metric was wrong, not the agent: it
counted a relevant headline the agent chose not to surface as a tagging error,
when `SYNTHESIS_SYSTEM` explicitly instructs it to select the ~12 most material
items and discard duplicates.

Precision and recall had to be separated, and only precision floored:

| Metric | Floored? | Why |
|---|---|---|
| Provenance | **Yes, at 1.0** | The shortfall is literally the hallucination rate. Zero tolerance |
| Factor precision | Yes, 0.70 (only when ≥ 8 items tagged) | Below that sample a ratio is noise |
| Noise rejection | Yes, 0.75 | Low-variance and high-value |
| Events produced | Yes, ≥ 3 | Collapse detector — "the agent stopped finding news" |
| Coverage | **No** | Selection is by design; it varied 8 → 3 → 4 events on identical input. A floor here is a flaky test |
| Impact precision | **No** | "high vs medium" is the most subjective label in the set, and the golden values are one person's judgment |

Resisting the urge to lower a floor until the suite passes is the whole point of
having floors.

### Why no pytest

There is no test infrastructure anywhere in this repo — no `tests/`, no
`conftest.py`, no CI, and no test dependency in `requirements.txt`. The
established convention is a runnable eval script (`eval_rag.py`), so `eval_gold.py`
matches it. The adversarial suite needs no model and no network, so it runs in
under a second and is usable as a pre-push check; unlike `eval_rag.py` it exits
non-zero on failure, because it guards a safety layer rather than reporting a
quality score.

### Why a separate `gold_eval_runs` table

`/api/metrics` renders the newest `eval_runs` row as the RAG scorecard, and
`EvalRun` has no run-type discriminator. Writing gold results there would have
silently replaced the RAG numbers with numbers of a different shape — a
dashboard quietly showing the wrong thing, which is the failure class this whole
change exists to prevent.

## 19. Fundamental metrics

### Trusting the vendor's ratios, or recomputing them?

| Option | Pros | Cons |
|---|---|---|
| **Show Yahoo's fields, but repair the ones that are provably wrong** ✅ | Free and instant for ~20 metrics; the two broken classes are detectable (`currency != financialCurrency`) and correctable with an FX rate already in the app | Repair logic to maintain, and it only fixes what we know is broken |
| Show Yahoo's fields as-is | No work | Ships P/S of 206 and EV/EBITDA of 977 for Infosys, and a −0.09 beta for ITC. Numbers that are visibly absurd destroy trust in the ones that are fine |
| Compute everything from the financial statements | Fully controlled | `yf.Ticker().financials` is sparse and inconsistent for NSE names; this is a personal dashboard, not a data vendor |
| Pay for a fundamentals API | Clean, reliable | A subscription for a single-user app |

The mixed-currency bug is worth stating precisely because it is invisible: the
market cap is INR, the revenue is USD, and the resulting ratio is off by the
USDINR rate (~94x) with no error anywhere. Yahoo's own website shows these
uncorrected.

### Sector-relative bands, and suppression

Thresholds carry per-sector overrides, and metrics that are not real quantities
for a sector are **hidden rather than scored**. Yahoo reports a gross margin of
`0.0` for HDFC Bank; debt-to-equity for a lender measures the business model, not
risk. Showing a bank a red "debt too high" flag would be actively misleading —
worse than showing nothing, because it looks like a finding.

### Why playbooks rather than a single fundamental score

| Option | Pros | Cons |
|---|---|---|
| **Pillars + named combination patterns** ✅ | Matches how the metrics are actually used — the same P/E means opposite things beside different companions; the two negative patterns (value trap, leverage-flattered ROE) catch the expensive mistakes | Patterns are conventions and need judgment |
| One blended 0-100 "fundamental score" | Simple to display, sortable | Collapses the reason into a number. A 62 tells you nothing about whether to worry about debt or growth |
| Raw metrics, no interpretation | Neutral | The user asked what these mean; a table of ratios is what they already can't read |

### Not folded into BUY/HOLD/SELL

The advice badge is a technical + consensus call. Adding fundamentals to it would
have moved every badge in the portfolio table without being asked — a silent
change to the meaning of an existing number. The factors are computed and
returned so blending is a one-line change if it is ever wanted, but the default
stays put.

## 20. Position sizing

### What drives the weight?

| Option | Pros | Cons |
|---|---|---|
| **Conviction-tilted inverse volatility** ✅ | Equalises *risk* contribution rather than rupees, which is what actually determines portfolio outcomes; uses signals the app already computes; every number is explainable | Volatility is backward-looking and says nothing about business risk |
| Equal weight | Trivial, hard to argue with | A 45%-vol name and a 20%-vol name at equal weight are not equal bets; the volatile one dominates |
| Rank-proportional | Simple, follows the screen | Ignores risk entirely — the top-ranked name is often the most volatile |
| Mean-variance optimisation (Markowitz) | Theoretically optimal | Notoriously unstable: tiny changes in estimated returns produce wildly different portfolios, and it needs a covariance matrix estimated from data this app does not keep |

### Caps, and why they are applied in a loop

Single-name 15%, sector 35%, drop-below 2%. The first implementation applied the
name cap then the sector cap once each, and the sector redistribution pushed a
name back to **40% against a 15% cap** — the second pass silently undid the first.
They now alternate until both hold. Any scoring model's characteristic failure is
concentration, so the caps are the part that most needed testing, and they are
checked against four baskets including degenerate ones.

### Stating what cannot be satisfied

A single-sector basket cannot honour a sector cap. A basket where five names clear
the bar cannot deploy more than 75% under a 15% name cap. Both are surfaced as
warnings rather than quietly ignored, because a number that looks like a
recommendation while silently violating its own stated constraint is worse than no
number.

### Cash as a first-class output

The deployed share scales with mean conviction, and the remainder is shown as
cash. The alternative — always normalising to 100% invested — would imply the
model is equally confident in every market, and would push weight into names it
actively dislikes purely to make the column add up.

### Why the watchlist does not fetch sectors

Sector data comes from `market_data.cached_sector()`, which reads the fundamentals
cache and **never triggers a fetch**. The watchlist is on a 60-second polling path;
blocking it on ~13 Yahoo `info` calls would make the main table as slow as the AI
panels it deliberately never waits on. Sector arrives once a drawer has been
opened, and until then the sector cap treats that name as unknown.

## 21. Rebuilding the sizing model after measuring it

§20 described the first sizing model. Measuring it disqualified it, and the
measurements are worth keeping because each one points at a different mistake.

| Measurement | Result | What it revealed |
|---|---|---|
| One-day turnover | 17.4% | Step-function factors flip on noise |
| Loudest input | Analyst consensus, 1.11 | One vendor field outweighed four technical factors |
| Fundamentals' score contribution | 0.00 | The best-evidenced premia counted for nothing |

### Step functions vs cross-sectional ranks

| Option | Pros | Cons |
|---|---|---|
| **Continuous percentile ranks within the basket** ✅ | A small move nudges a rank instead of flipping a threshold — turnover fell 17.4% → 2.2% per day; unlike quantities become comparable | Scores are relative to the basket, so a uniformly poor basket still produces a "best" name |
| Step functions (the original) | Trivially explainable | Flips on noise; unusable turnover |
| Absolute thresholds per metric | Stable, comparable across baskets | Requires calibrated sector-by-sector bands for every metric, and they drift |

The basket-relative caveat is real and is disclosed in the UI string rather than
engineered away: with the funding threshold at 0.45 a genuinely weak basket
still funds its least-bad names.

### Component weights from evidence, not convenience

The original weights were an accident of what was already computed. The rewrite
sets them from the published cross-sectional literature — momentum, quality and
value at 25% each, trend 15%, analyst consensus **cut from loudest input to 10%**
because the evidence supports revisions rather than levels. 1-month momentum was
removed outright: at that horizon the effect reverses, so the old model was
plausibly scoring it with the wrong sign.

### Turnover is a first-class constraint

A sizing model that is correct but implies daily trading is wrong in practice —
in India the round trip costs brokerage and STT and can convert a 12.5%
long-term gain into a slab-rate short-term one. Hence 0.5% rounding, a stated
3pp no-trade band, and an explicit "targets, not instructions" note. Turnover was
measured before and after rather than assumed.

### Why the watchlist warms fundamentals in the background

Value and quality are half the weighting scheme and exist only for symbols whose
fundamentals have been fetched, so on a cold cache the column funded 2 names and
on a warm one 8 — visibly changing as you browsed. Fetching inline would put ~13
Yahoo `info` calls on a 60-second poll. A daemon thread warms the cache once per
symbol, so the first load costs nothing and every later load is a 24 h cache hit.

## 22. One allocation or two?

| Option | Pros | Cons |
|---|---|---|
| **One allocation over the union** ✅ | The columns actually sum to 100%; a symbol in both lists gets one number; holdings and candidates compete directly, which is what makes "no incumbency bonus" real | Needs a shared endpoint both tables read, and the comparison needs a selection-effect caveat |
| Two independent baskets (the original) | Each table is self-contained | Two tables each summing to 100% implies 190% of a portfolio, and HAL showed 15% in one and 5% in the other. Indefensible once noticed |
| Holdings only, picks unsized | Simple, no double-count | Removes the sizing from the table where a new idea most needs it |

The bug was reported as "the percentages don't add to 100". They did — each
basket summed to 100 individually. The defect was that there were two baskets at
all, which is a design error rather than an arithmetic one, and the same stock
carrying two different weights is the proof.

### Disclosing the selection effect

With one cross-section the candidates take ~88% and the holdings ~10%. Shipping
that without comment would read as "sell almost everything you own". It is an
artefact: the screener pre-selects on momentum and trend, which are 40% of the
conviction weighting, so candidates are expected to win on the axes that chose
them. The caveat is the first warning in the response — the alternative, silently
handicapping candidates, would hide a real signal behind an unstated fudge.

## 23. Screening small caps

### Gate first, or rank first?

| Option | Pros | Cons |
|---|---|---|
| **Hard gates, then rank the survivors** ✅ | The exciting-but-unsound name never reaches the list; each rejection has one stated reason | A good business one point under a threshold is excluded — mitigated by surfacing near-misses |
| Rank everything, show the top N | Nothing is lost | This is how screens surface loss-making, leveraged, untradeable small caps with great momentum |
| Score gates as weights | Nuanced | A high momentum score can outvote "cannot be sold", which is not a trade-off worth offering |

### Liquidity is the gate that had to exist

No other view in this app measures tradability, and for a large cap it barely
matters. For a small cap it is the difference between a position and a trap: at
₹2 cr of median daily turnover a retail order moves the price against you, and
during a correction there may be no bid. It is deliberately the first gate.

### Where the universe came from

There was none — `nifty100.json` is entirely large caps. A 169-ticker candidate
list was written and then **validated against live data before being saved**;
six symbols failed and were dropped, which is exactly why the validation step
existed rather than trusting a hand-written list.

### Deriving ROE rather than rejecting on its absence

Yahoo publishes `returnOnEquity` for ~7% of Indian small caps and EPS plus book
value for ~100%, and ROE is the ratio of the two. Without the fallback a quality
screen rejects almost everything for missing data, which is a far worse error
than a derived figure sitting ~2 points from the vendor's (ending vs average
equity). Derived values are flagged, not passed off as reported.
