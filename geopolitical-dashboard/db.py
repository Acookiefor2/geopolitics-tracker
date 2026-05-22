# db.py
"""
Shared SQLite database layer.
All reads and writes go through this module to keep schema in one place.
"""

import sqlite3
import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from config import DB_PATH, MAX_EVENTS_DISPLAY

logger = logging.getLogger(__name__)


# ─── Schema ───────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_hash         TEXT    UNIQUE NOT NULL,   -- SHA256 of raw headline, dedup key
    source_country    TEXT    NOT NULL,
    source_city       TEXT,
    source_lat        REAL    NOT NULL,
    source_lon        REAL    NOT NULL,
    target_country    TEXT    NOT NULL,
    target_city       TEXT,
    target_lat        REAL    NOT NULL,
    target_lon        REAL    NOT NULL,
    event_type        TEXT    NOT NULL,
    urgency           INTEGER NOT NULL CHECK(urgency BETWEEN 1 AND 10),
    summary           TEXT    NOT NULL,
    raw_headline      TEXT    NOT NULL,
    source_url        TEXT,
    inserted_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_inserted ON events(inserted_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_urgency  ON events(urgency DESC);
"""


# ─── Connection helper ────────────────────────────────────────────────────────

@contextmanager
def get_connection():
    """Thread-safe SQLite connection with WAL mode for concurrent reads/writes."""
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Initialization ───────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    with get_connection() as conn:
        conn.executescript(DDL)
    logger.info("Database initialized at %s", DB_PATH)


# ─── Write ────────────────────────────────────────────────────────────────────

def compute_feed_hash(headline: str, source_url: str = "") -> str:
    """Stable deduplication key from headline text."""
    raw = f"{headline}|{source_url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def insert_event(event: dict) -> bool:
    """
    Insert a parsed event dict.  Returns True if inserted, False if duplicate.

    Expected keys:
        source_country, source_city, source_lat, source_lon,
        target_country, target_city, target_lat, target_lon,
        event_type, urgency, summary, raw_headline, source_url
    """
    feed_hash = compute_feed_hash(
        event["raw_headline"], event.get("source_url", "")
    )

    sql = """
        INSERT OR IGNORE INTO events (
            feed_hash, source_country, source_city, source_lat, source_lon,
            target_country, target_city, target_lat, target_lon,
            event_type, urgency, summary, raw_headline, source_url
        ) VALUES (
            :feed_hash, :source_country, :source_city, :source_lat, :source_lon,
            :target_country, :target_city, :target_lat, :target_lon,
            :event_type, :urgency, :summary, :raw_headline, :source_url
        )
    """
    with get_connection() as conn:
        cursor = conn.execute(sql, {**event, "feed_hash": feed_hash})
        inserted = cursor.rowcount > 0

    if inserted:
        logger.info(
            "Inserted [%s | urgency=%s]: %s → %s",
            event["event_type"],
            event["urgency"],
            event["source_country"],
            event["target_country"],
        )
    else:
        logger.debug("Duplicate skipped: %s", event["raw_headline"][:80])

    return inserted


# ─── Read ─────────────────────────────────────────────────────────────────────

def fetch_latest_events(limit: int = MAX_EVENTS_DISPLAY) -> list[dict]:
    """Return the most recent events as a list of plain dicts."""
    sql = """
        SELECT *
        FROM events
        ORDER BY inserted_at DESC
        LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(row) for row in rows]


def fetch_event_count() -> int:
    """Total number of events stored."""
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def get_db_last_modified() -> Optional[str]:
    """Return the timestamp of the most recently inserted event."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT inserted_at FROM events ORDER BY inserted_at DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else None