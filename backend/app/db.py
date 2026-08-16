"""SQLite storage: watchlist + AI result caches."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import Column, DateTime, String, Text, create_engine
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


def init_db() -> None:
    Base.metadata.create_all(engine)
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
