"""Chat endpoint: answers grounded in the agents' cached research via RAG."""
from __future__ import annotations

import os
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import market_data, rag
from ..agents.runner import AgentUnavailable, simple_response
from ..db import WatchlistItem, get_db

CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-5-mini")
MAX_HISTORY = 8

router = APIRouter(prefix="/api")

SYSTEM = """You are the assistant inside a personal Indian stock (NSE) dashboard.
Answer the user's questions using the RESEARCH CONTEXT below — it contains this
app's own AI-generated stock research (per-stock analyses and top-20 screener runs)
plus a live snapshot of the user's watchlist.

Rules:
- Ground answers in the provided context. When you use a piece of research, say
  which stock and date it came from. If the context doesn't cover the question,
  say so and suggest opening that stock's AI analysis to generate research first.
- Be concise and conversational; this is a chat panel, not a report.
- You are not a licensed adviser — for buy/sell questions, present the research's
  view with its reasoning, and note it is informational, not financial advice."""


class ChatBody(BaseModel):
    message: str
    history: List[Dict[str, str]] = []


def _watchlist_snapshot(db: Session) -> str:
    """Compact live table for context — served from the 10-min price cache."""
    items = db.query(WatchlistItem).all()
    symbols = [i.symbol for i in items]
    if not symbols:
        return "Watchlist is empty."
    try:
        closes_map = market_data.get_closes(symbols)
    except Exception:
        return "Live watchlist data unavailable right now."
    lines = []
    for item in items:
        closes = closes_map.get(item.symbol)
        if closes is None or closes.empty:
            continue
        m = market_data.compute_metrics(closes)
        lines.append(
            f"- {item.name} ({item.symbol}): price {m['price']}, 1w {m['ret_1w']}%, "
            f"1m {m['ret_1m']}%, 1y {m['ret_1y']}%, 5y {m['ret_5y']}%, RSI {m['rsi']}"
        )
    return "\n".join(lines)


@router.post("/chat")
def chat(body: ChatBody, db: Session = Depends(get_db)):
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Empty message.")

    chunks = rag.search(message)
    research = (
        "\n\n---\n\n".join(c["text"] for c in chunks)
        if chunks
        else "(No stored AI research matched this question yet.)"
    )
    instructions = (
        f"{SYSTEM}\n\n## RESEARCH CONTEXT\n\n### Stored AI research\n{research}\n\n"
        f"### Live watchlist snapshot\n{_watchlist_snapshot(db)}"
    )

    history = [
        {"role": h["role"], "content": h["content"]}
        for h in body.history[-MAX_HISTORY:]
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]
    try:
        reply = simple_response(
            model=CHAT_MODEL,
            instructions=instructions,
            input_items=history + [{"role": "user", "content": message}],
        )
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))

    sources = [
        {"doc_key": c["doc_key"], "symbol": c["symbol"], "date": c["date"]}
        for c in chunks
    ]
    return {"reply": reply, "sources": sources}
