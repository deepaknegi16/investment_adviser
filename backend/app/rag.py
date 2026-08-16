"""RAG index over the agents' cached research.

Every Analyst Agent run and Screener run is flattened to text and indexed in
the `rag_chunks` table with an OpenAI embedding (`text-embedding-3-small`).
The chat retrieves the most relevant chunks by cosine similarity; if
embeddings are unavailable (e.g. quota), it degrades to keyword scoring so
the chat can still ground itself in the research.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import numpy as np

from .agents import runner
from .db import RagChunk, SessionLocal

EMBEDDING_MODEL = "gemini-embedding-001"  # free tier: 10M tokens/min
TOP_K = 5


def _embed(texts: List[str]) -> Optional[List[List[float]]]:
    try:
        client = runner._gemini()
        resp = client.models.embed_content(model=EMBEDDING_MODEL, contents=texts)
        return [list(e.values) for e in resp.embeddings]
    except Exception:
        return None


def _index(doc_key: str, text: str, date: str, symbol: Optional[str] = None) -> None:
    vectors = _embed([text])
    with SessionLocal() as db:
        db.query(RagChunk).filter(RagChunk.doc_key == doc_key).delete()
        db.add(
            RagChunk(
                doc_key=doc_key,
                symbol=symbol,
                date=date,
                text=text,
                embedding=json.dumps(vectors[0]) if vectors else None,
            )
        )
        db.commit()


def index_analysis(symbol: str, name: str, payload: Dict[str, Any]) -> None:
    """Flatten one Analyst Agent result into a retrievable chunk."""
    date = payload.get("generated_at", "")[:10]
    pred = payload.get("prediction", {})
    news_lines = [
        f"- {n.get('headline')} ({n.get('source')}, {n.get('date')}): {n.get('summary')}"
        for n in payload.get("news", [])
    ]
    text = (
        f"AI research on {name} ({symbol}), generated {date}.\n"
        f"Recommendation: {payload.get('recommendation')}.\n"
        f"Short-term outlook (1-3 months): {pred.get('short_term')}\n"
        f"Long-term outlook (1-3 years): {pred.get('long_term')}\n"
        f"Confidence: {pred.get('confidence')}.\n"
        f"Reasoning: {payload.get('reasoning')}\n"
        f"Recent news:\n" + "\n".join(news_lines)
    )
    _index(f"analysis:{symbol}:{date}", text, date, symbol=symbol)


def index_picks(payload: Dict[str, Any]) -> None:
    """Flatten one Screener run into a retrievable chunk."""
    date = payload.get("generated_at", "")[:10]
    lines = [
        f"{p.get('rank')}. {p.get('name')} ({p.get('symbol')}) — {p.get('recommendation')}: "
        f"{p.get('rationale')} (price {p.get('price')}, 1m {p.get('ret_1m')}%, 1y {p.get('ret_1y')}%)"
        for p in payload.get("picks", [])
    ]
    text = (
        f"AI screener top-20 NSE picks, generated {date}. "
        f"Market note: {payload.get('market_note')}\n" + "\n".join(lines)
    )
    _index(f"picks:{date}", text, date)


def _keyword_score(query: str, text: str) -> float:
    words = {w for w in re.findall(r"[a-z]{3,}", query.lower())}
    if not words:
        return 0.0
    lower = text.lower()
    return sum(1.0 for w in words if w in lower) / len(words)


def search(query: str, k: int = TOP_K) -> Dict[str, Any]:
    """Return the k most relevant research chunks for a chat question.

    Returns {"chunks": [...], "mode": "semantic" | "keyword" | "empty"} —
    each chunk carries its relevance score for logging and evaluation.
    """
    with SessionLocal() as db:
        chunks = db.query(RagChunk).all()
        rows = [
            {
                "doc_key": c.doc_key,
                "symbol": c.symbol,
                "date": c.date,
                "text": c.text,
                "embedding": json.loads(c.embedding) if c.embedding else None,
            }
            for c in chunks
        ]
    if not rows:
        return {"chunks": [], "mode": "empty"}

    embedded = [r for r in rows if r["embedding"]]
    query_vec = _embed([query]) if embedded else None
    if query_vec:
        q = np.array(query_vec[0])
        # Guard against chunks embedded under a different model/dimension.
        embedded = [r for r in embedded if len(r["embedding"]) == len(q)]
    if query_vec and embedded:
        mode = "semantic"
        q_norm = np.linalg.norm(q) or 1.0
        for r in embedded:
            v = np.array(r["embedding"])
            r["score"] = float(np.dot(q, v) / (q_norm * (np.linalg.norm(v) or 1.0)))
        ranked = sorted(embedded, key=lambda r: r["score"], reverse=True)
    else:
        # Embeddings unavailable — degrade to keyword overlap over all chunks.
        mode = "keyword"
        for r in rows:
            r["score"] = _keyword_score(query, r["text"])
        ranked = sorted(rows, key=lambda r: r["score"], reverse=True)

    chunks = [
        {
            "doc_key": r["doc_key"],
            "symbol": r["symbol"],
            "date": r["date"],
            "text": r["text"],
            "score": round(r["score"], 4),
        }
        for r in ranked[:k]
        if r["score"] > 0
    ]
    return {"chunks": chunks, "mode": mode}


# ------------------------------------------------------- user-uploaded files

CHUNK_CHARS = 1400
MAX_CHUNKS_PER_FILE = 60


def _chunk_text(text: str) -> List[str]:
    """Split on paragraph boundaries into ~CHUNK_CHARS pieces."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""
    for p in paragraphs:
        while len(p) > CHUNK_CHARS:  # oversized paragraph: hard split
            chunks.append((current + "\n\n" + p[:CHUNK_CHARS]).strip())
            current, p = "", p[CHUNK_CHARS:]
        if len(current) + len(p) + 2 > CHUNK_CHARS:
            if current:
                chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}".strip()
    if current:
        chunks.append(current)
    return chunks[:MAX_CHUNKS_PER_FILE]


def index_file(filename: str, text: str, date: str) -> int:
    """Chunk + embed an uploaded document; replaces any previous version."""
    chunks = _chunk_text(text)
    if not chunks:
        return 0
    vectors = _embed(chunks)
    with SessionLocal() as db:
        db.query(RagChunk).filter(
            RagChunk.doc_key.like(f"file:{filename}#%")
        ).delete(synchronize_session=False)
        for i, chunk in enumerate(chunks):
            db.add(
                RagChunk(
                    doc_key=f"file:{filename}#{i}",
                    symbol=None,
                    date=date,
                    text=f"[From uploaded file '{filename}']\n{chunk}",
                    embedding=json.dumps(vectors[i]) if vectors else None,
                )
            )
        db.commit()
    return len(chunks)


def list_files() -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(RagChunk).filter(RagChunk.doc_key.like("file:%")).all()
    files: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        name = r.doc_key[len("file:"):].rsplit("#", 1)[0]
        entry = files.setdefault(name, {"name": name, "chunks": 0, "date": r.date})
        entry["chunks"] += 1
    return sorted(files.values(), key=lambda f: f["name"])


def delete_file(filename: str) -> int:
    with SessionLocal() as db:
        n = db.query(RagChunk).filter(
            RagChunk.doc_key.like(f"file:{filename}#%")
        ).delete(synchronize_session=False)
        db.commit()
    return n
