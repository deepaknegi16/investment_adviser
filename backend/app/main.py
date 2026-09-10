from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import auth  # noqa: E402
from . import cache, ratelimit  # noqa: E402
from .auth import require_auth, router as auth_router  # noqa: E402
from .db import init_db  # noqa: E402
from .routers import (  # noqa: E402
    analysis, chat, documents, gold, metrics, picks, portfolio, watchlist,
)

app = FastAPI(title="Indian Stock Portfolio Adviser")

# Rate limiting sits outside CORS so a throttled response still carries the
# headers the browser needs to read it.
app.middleware("http")(ratelimit.middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public: login + health. Everything else requires a valid JWT.
app.include_router(auth_router)
protected = [Depends(require_auth)]
app.include_router(watchlist.router, dependencies=protected)
app.include_router(analysis.router, dependencies=protected)
app.include_router(picks.router, dependencies=protected)
app.include_router(chat.router, dependencies=protected)
app.include_router(documents.router, dependencies=protected)
app.include_router(metrics.router, dependencies=protected)
app.include_router(gold.router, dependencies=protected)
app.include_router(portfolio.router, dependencies=protected)


@app.on_event("startup")
def startup() -> None:
    # Fail before serving a single request if the password is missing or is the
    # published example — never silently fall back to a default.
    auth.verify_configured()
    init_db()


@app.get("/api/health")
def health():
    """Liveness — is the process up? Used by Docker and nginx."""
    return {"ok": True}


@app.get("/api/ready")
def ready():
    """Readiness — can this worker actually serve? Reports its dependencies.

    Distinct from /health on purpose: a worker whose Redis vanished is still
    alive and still correct (the cache degrades to per-process), so it must not
    be pulled out of the load balancer for it. This endpoint says which mode it
    is running in rather than failing.
    """
    from .db import engine

    db_ok = True
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception:
        db_ok = False
    return {
        "ok": db_ok,
        "worker_pid": os.getpid(),
        "database": "ok" if db_ok else "unreachable",
        "cache": cache.status(),
        "rate_limits": ratelimit.status(),
    }
