from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .auth import require_auth, router as auth_router  # noqa: E402
from .db import init_db  # noqa: E402
from .routers import analysis, chat, documents, picks, watchlist  # noqa: E402

app = FastAPI(title="Indian Stock Portfolio Adviser")

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


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health():
    return {"ok": True}
