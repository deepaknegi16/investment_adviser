"""RAG evaluation harness.

Measures the two halves of the RAG pipeline:

1. RETRIEVAL — did search() surface the right chunk?
   A golden set of (question → expected doc_key prefix) pairs is built
   automatically from whatever is in the corpus (every cached analysis,
   screener run, and uploaded file), plus optional hand-written cases in
   eval_golden.json:  [{"question": "...", "expected_prefix": "analysis:INFY"}]
   Metrics: recall@5 (hit rate in the top 5) and MRR (mean reciprocal rank).

2. GENERATION — is the answer faithful and relevant?
   For a sample of questions, the real chat pipeline produces an answer, and
   a Gemini judge scores it 1-5 on:
     - faithfulness: every claim supported by the retrieved context
     - relevance:    the answer actually addresses the question
   listing any unsupported claims it finds.

Results are printed and stored in the eval_runs table, surfaced by /api/metrics.

Usage (from backend/):
    .venv/bin/python eval_rag.py                  # retrieval + judge (3 samples)
    .venv/bin/python eval_rag.py --no-judge       # retrieval only (no quota spend on answers)
    .venv/bin/python eval_rag.py --judge-sample 5
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from app import rag  # noqa: E402
from app.agents.runner import AgentUnavailable, structured_synthesis  # noqa: E402
from app.db import EvalRun, RagChunk, SessionLocal, WatchlistItem, init_db  # noqa: E402
from app.routers.chat import answer_question  # noqa: E402

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", os.environ.get("CHAT_MODEL", "gemini-3.5-flash-lite"))
GOLDEN_PATH = Path(__file__).resolve().parent / "eval_golden.json"

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "faithfulness": {"type": "integer", "minimum": 1, "maximum": 5},
        "relevance": {"type": "integer", "minimum": 1, "maximum": 5},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "comment": {"type": "string"},
    },
    "required": ["faithfulness", "relevance", "unsupported_claims", "comment"],
}

JUDGE_SYSTEM = """You are a strict evaluator of a RAG (retrieval-augmented generation)
answer. You are given the retrieved CONTEXT, the user's QUESTION, and the ANSWER.

Score:
- faithfulness (1-5): 5 = every factual claim in the answer is directly supported
  by the context (live watchlist claims count as supported if plausible from the
  snapshot); 1 = mostly unsupported/invented.
- relevance (1-5): 5 = fully answers the question asked; 1 = off-topic.
List any specific unsupported claims. Judge only against the given context."""


def _name_for(symbol: str, db) -> str:
    item = db.get(WatchlistItem, symbol)
    return item.name if item else symbol.replace(".NS", "")


def build_golden_set() -> List[Dict[str, str]]:
    """One or two questions per corpus document, plus optional hand-written cases."""
    golden: List[Dict[str, str]] = []
    with SessionLocal() as db:
        keys = {c.doc_key for c in db.query(RagChunk).all()}
        symbols_seen, picks_seen, files_seen = set(), False, set()
        for key in sorted(keys):
            if key.startswith("analysis:"):
                symbol = key.split(":")[1]
                if symbol in symbols_seen:
                    continue
                symbols_seen.add(symbol)
                name = _name_for(symbol, db)
                golden.append({
                    "question": f"What did the research conclude about {name}?",
                    "expected_prefix": f"analysis:{symbol}",
                })
                golden.append({
                    "question": f"Should I hold or sell {name} right now?",
                    "expected_prefix": f"analysis:{symbol}",
                })
            elif key.startswith("picks:") and not picks_seen:
                picks_seen = True
                golden.append({
                    "question": "Which stocks are the current top screener picks?",
                    "expected_prefix": "picks:",
                })
            elif key.startswith("file:"):
                fname = key[len("file:"):].rsplit("#", 1)[0]
                if fname in files_seen:
                    continue
                files_seen.add(fname)
                golden.append({
                    "question": f"Summarize what the uploaded file {fname} says.",
                    "expected_prefix": f"file:{fname}",
                })
    if GOLDEN_PATH.exists():
        golden.extend(json.loads(GOLDEN_PATH.read_text()))
    return golden


def eval_retrieval(golden: List[Dict[str, str]]) -> Dict:
    per_item, hits, rr_sum = [], 0, 0.0
    for item in golden:
        chunks = rag.search(item["question"])["chunks"]
        rank: Optional[int] = None
        for i, c in enumerate(chunks, start=1):
            if c["doc_key"].startswith(item["expected_prefix"]):
                rank = i
                break
        if rank:
            hits += 1
            rr_sum += 1.0 / rank
        per_item.append({
            "question": item["question"],
            "expected": item["expected_prefix"],
            "rank": rank,
            "top_hit": chunks[0]["doc_key"] if chunks else None,
        })
        mark = f"rank {rank}" if rank else "MISS"
        print(f"  [{mark:>6}] {item['question'][:70]}")
    n = len(golden)
    return {
        "n_questions": n,
        "recall_at_5": round(hits / n, 3) if n else None,
        "mrr": round(rr_sum / n, 3) if n else None,
        "items": per_item,
    }


def eval_generation(golden: List[Dict[str, str]], sample_n: int) -> Dict:
    sample = golden[:sample_n]
    results = []
    with SessionLocal() as db:
        for item in sample:
            q = item["question"]
            print(f"  answering: {q[:70]}")
            try:
                res = answer_question(q, [], db)
            except AgentUnavailable as e:
                results.append({"question": q, "error": str(e)})
                continue
            context = "\n\n---\n\n".join(c["text"] for c in res["_chunks"]) or "(empty)"
            prompt = (
                f"## CONTEXT\n{context}\n\n## QUESTION\n{q}\n\n"
                f"## ANSWER\n{res['reply']}\n\nScore this answer."
            )
            try:
                verdict = structured_synthesis(
                    model=JUDGE_MODEL, system=JUDGE_SYSTEM, prompt=prompt,
                    schema=JUDGE_SCHEMA, groq_fallback=True,
                )
            except AgentUnavailable as e:
                results.append({"question": q, "error": f"judge failed: {e}"})
                continue
            print(f"    faithfulness {verdict['faithfulness']}/5 · relevance {verdict['relevance']}/5")
            results.append({"question": q, **verdict})
    scored = [r for r in results if "faithfulness" in r]
    return {
        "n_judged": len(scored),
        "n_errors": len(results) - len(scored),
        "avg_faithfulness": round(sum(r["faithfulness"] for r in scored) / len(scored), 2) if scored else None,
        "avg_relevance": round(sum(r["relevance"] for r in scored) / len(scored), 2) if scored else None,
        "items": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the RAG pipeline.")
    parser.add_argument("--no-judge", action="store_true", help="retrieval metrics only")
    parser.add_argument("--judge-sample", type=int, default=3, help="questions to judge (default 3)")
    args = parser.parse_args()

    init_db()
    golden = build_golden_set()
    if not golden:
        print("Corpus is empty — open a stock analysis or upload a file first.")
        return 1

    print(f"\n== Retrieval eval ({len(golden)} golden questions) ==")
    retrieval = eval_retrieval(golden)
    print(f"\n  recall@5: {retrieval['recall_at_5']}   MRR: {retrieval['mrr']}")

    generation = None
    if not args.no_judge:
        print(f"\n== Generation eval (judging {min(args.judge_sample, len(golden))} answers) ==")
        generation = eval_generation(golden, args.judge_sample)
        print(f"\n  avg faithfulness: {generation['avg_faithfulness']}/5"
              f"   avg relevance: {generation['avg_relevance']}/5")

    payload = {
        "ran_at": dt.datetime.now().isoformat(timespec="seconds"),
        "retrieval": {k: v for k, v in retrieval.items() if k != "items"},
        "generation": (
            {k: v for k, v in generation.items() if k != "items"} if generation else None
        ),
    }
    with SessionLocal() as db:
        db.add(EvalRun(payload_json=json.dumps({
            **payload,
            "retrieval_items": retrieval["items"],
            "generation_items": generation["items"] if generation else None,
        })))
        db.commit()
    print("\nStored in eval_runs — visible at GET /api/metrics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
