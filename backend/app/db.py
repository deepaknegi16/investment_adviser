"""SQLite storage: watchlist + AI result caches."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent / "adviser.db"
engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    symbol = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    added_at = Column(DateTime, default=dt.datetime.utcnow)


class AiAnalysis(Base):
    __tablename__ = "ai_analysis"
    symbol = Column(String, primary_key=True)
    date = Column(String, primary_key=True)  # YYYY-MM-DD
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class AiPicks(Base):
    __tablename__ = "ai_picks"
    date = Column(String, primary_key=True)  # YYYY-MM-DD
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class RagChunk(Base):
    """A retrievable chunk of AI research for the chat's RAG index."""

    __tablename__ = "rag_chunks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_key = Column(String, index=True, nullable=False)  # e.g. analysis:INFY.NS:2026-08-16
    symbol = Column(String, nullable=True)
    date = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)  # JSON float list; null if embedding failed
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class AiHolders(Base):
    """Cached big-shareholder lookups (quarterly data — 30-day TTL)."""

    __tablename__ = "ai_holders"
    symbol = Column(String, primary_key=True)
    date = Column(String, nullable=False)  # YYYY-MM-DD of the lookup
    payload_json = Column(Text, nullable=False)


class ChatLog(Base):
    """One row per chat turn — the observability trail for the RAG system."""

    __tablename__ = "chat_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=dt.datetime.utcnow)
    question = Column(Text, nullable=False)
    provider = Column(String)          # gemini | groq
    retrieval_mode = Column(String)    # semantic | keyword | empty
    top_score = Column(Float, nullable=True)  # cosine similarity of best chunk
    n_sources = Column(Integer)
    latency_ms = Column(Integer)
    answer_chars = Column(Integer)


class GoldEvent(Base):
    """One gold-factor news event the watcher has already seen.

    Primary key is a hash of factor+headline+source, so the same story picked
    up on three consecutive runs is stored (and emailed) exactly once.
    """

    __tablename__ = "gold_events"
    id = Column(String, primary_key=True)  # gold_watch.event_key()
    first_seen = Column(DateTime, default=dt.datetime.utcnow)
    event_date = Column(String)            # publication date as reported
    factor = Column(String, index=True)
    headline = Column(Text, nullable=False)
    source = Column(String)
    url = Column(Text)
    direction = Column(String)             # bullish | bearish | neutral
    impact = Column(String)                # high | medium | low
    horizon = Column(String)
    why_it_matters = Column(Text)
    alerted = Column(Integer, default=0)   # 1 once it has gone out by email
    trust = Column(String, default="unknown")  # ok | unverified | suspect | unknown


class GoldWatchRun(Base):
    """Audit trail of watcher sweeps — what it saw, what it decided, what it sent."""

    __tablename__ = "gold_watch_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=dt.datetime.utcnow)
    bias = Column(String)                  # bullish | neutral | bearish
    conviction = Column(String)
    etf_price = Column(Float)
    n_events = Column(Integer)
    n_new = Column(Integer)
    emailed = Column(Integer, default=0)   # 1 if an alert was sent
    email_error = Column(Text)
    suppressed_reason = Column(Text)       # why an alert was NOT sent, if it wasn't
    n_violations = Column(Integer, default=0)
    payload_json = Column(Text, nullable=False)


class GuardrailViolation(Base):
    """Every guardrail hit, kept rather than discarded.

    The point of flag-don't-drop is that suppressed content stays auditable —
    this table is where "what did the agent get wrong, and how often" lives.
    """

    __tablename__ = "guardrail_violations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=dt.datetime.utcnow)
    run_id = Column(Integer, index=True)   # GoldWatchRun.id
    kind = Column(String, index=True)      # unsourced | injection_suspected | ...
    severity = Column(String)              # low | medium | high
    detail = Column(Text)
    event_id = Column(String, nullable=True)


class GoldEvalRun(Base):
    """Results of eval_gold.py runs.

    Deliberately NOT the shared eval_runs table: /api/metrics reads the latest
    EvalRun row to render the RAG scorecard, so gold results written there would
    silently replace those numbers with ones of a different shape.
    """

    __tablename__ = "gold_eval_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=dt.datetime.utcnow)
    passed = Column(Integer, default=0)    # 1 if every adversarial fixture caught
    payload_json = Column(Text, nullable=False)


class EvalRun(Base):
    """Stored results of eval_rag.py runs, surfaced by /api/metrics."""

    __tablename__ = "eval_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=dt.datetime.utcnow)
    payload_json = Column(Text, nullable=False)


SEED_WATCHLIST = [
    ("INFY.NS", "Infosys"),
    ("WIPRO.NS", "Wipro"),
    ("GOLDBEES.NS", "Goldbees (Gold ETF)"),
    ("ADANIGREEN.NS", "Adani Green"),
    ("HDFCBANK.NS", "HDFC Bank"),
    ("ONGC.NS", "ONGC"),
    ("BEL.NS", "Bharat Electronics"),
    ("PNB.NS", "Punjab National Bank"),
    ("ATGL.NS", "Adani Total Gas"),
    ("ITC.NS", "ITC"),
    ("LICI.NS", "LIC"),
    ("SBIN.NS", "State Bank of India"),
]


# create_all() never ALTERs an existing table, and this app has no migration
# tool. Columns added after a table already exists in someone's adviser.db are
# listed here and added idempotently on startup.
_ADDED_COLUMNS = [
    ("gold_events", "trust", "TEXT DEFAULT 'unknown'"),
    ("gold_watch_runs", "suppressed_reason", "TEXT"),
    ("gold_watch_runs", "n_violations", "INTEGER DEFAULT 0"),
]


def _migrate() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in existing:
                continue  # create_all just made it with the column present
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()
    with SessionLocal() as db:
        if db.query(WatchlistItem).count() == 0:
            for symbol, name in SEED_WATCHLIST:
                db.add(WatchlistItem(symbol=symbol, name=name))
            db.commit()


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
