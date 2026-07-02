#!/usr/bin/env python3
"""
SQLite store for ingested posts + extracted claims (stdlib sqlite3, no install).

Two tables:
  posts  — every raw post we've seen. `url` is the PRIMARY KEY, so re-ingesting the
           same feed item is a no-op: dedup is a UNIQUE-constraint, not custom logic.
  claims — one structured transfer claim per (post, deal). `deal_key` clusters claims
           by player (see cluster.py) so the same deal from many sources lines up.

Machine state only — this DB is gitignored. The curated ground-truth CSVs stay the
hand-verified source of truth; ingested claims are proposals until promoted.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "ingest" / "ingest.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    url        TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    title      TEXT,
    summary    TEXT,
    published  TEXT,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    post_url             TEXT NOT NULL REFERENCES posts(url),
    deal_key             TEXT NOT NULL,
    player               TEXT,
    from_club            TEXT,
    to_club              TEXT,
    stage                TEXT,
    implied_p            REAL,
    source_name          TEXT,
    source_identifiable  INTEGER,
    direction_confidence REAL,
    fee_eur              INTEGER,
    claim_date           TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE(post_url, deal_key)
);
CREATE INDEX IF NOT EXISTS idx_claims_deal ON claims(deal_key);
CREATE TABLE IF NOT EXISTS extract_failures (
    url      TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# A post whose extraction failed (parse error / API exception) is RELEASED for
# retry up to this many times: its posts row is deleted so the next run's URL
# dedup re-admits it. After the cap it stays "seen" (a poison post can't loop
# forever). Separate table so the cached DB migrates via CREATE IF NOT EXISTS —
# no ALTER on the posts schema.
MAX_EXTRACT_ATTEMPTS = 3

# meta key: ISO timestamp of the last HEALTHY ingest run (set by pipeline.run,
# read by site/build_feed.py to show visitors "data as of ..." + a stale badge).
LAST_INGEST_KEY = "last_ingest_success"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=DEFAULT_DB):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def add_post(conn, post):
    """Insert a post. Returns True if NEW, False if already seen (dedup on url)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO posts(url, source, title, summary, published, fetched_at) "
        "VALUES (:url, :source, :title, :summary, :published, :fetched_at)",
        {"url": post["url"], "source": post.get("source", ""),
         "title": post.get("title", ""), "summary": post.get("summary", ""),
         "published": post.get("published", ""), "fetched_at": _now()},
    )
    conn.commit()
    return cur.rowcount == 1


def add_claim(conn, claim):
    """Insert a claim (one per post+deal). Returns the row id, or None if duplicate."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO claims(post_url, deal_key, player, from_club, to_club, "
        "stage, implied_p, source_name, source_identifiable, direction_confidence, "
        "fee_eur, claim_date, created_at) VALUES (:post_url, :deal_key, :player, "
        ":from_club, :to_club, :stage, :implied_p, :source_name, :source_identifiable, "
        ":direction_confidence, :fee_eur, :claim_date, :created_at)",
        {**{"created_at": _now()}, **claim},
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount == 1 else None


def release_failed_post(conn, url):
    """Extraction failed for this post: count the attempt and, below the cap,
    delete its posts row so the next run retries it (self-healing instead of
    alert-and-lose — the June 25 outage burned a week of posts as 'seen').
    Returns True if the post was released for retry, False if the cap is hit."""
    row = conn.execute("SELECT attempts FROM extract_failures WHERE url = ?",
                       (url,)).fetchone()
    attempts = (row["attempts"] if row else 0) + 1
    conn.execute(
        "INSERT INTO extract_failures(url, attempts, last_at) VALUES (?, ?, ?) "
        "ON CONFLICT(url) DO UPDATE SET attempts = ?, last_at = ?",
        (url, attempts, _now(), attempts, _now()))
    released = attempts < MAX_EXTRACT_ATTEMPTS
    if released:
        conn.execute("DELETE FROM posts WHERE url = ?", (url,))
    conn.commit()
    return released


def clear_failure(conn, url):
    """Extraction finally succeeded: forget the failure history."""
    conn.execute("DELETE FROM extract_failures WHERE url = ?", (url,))
    conn.commit()


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = ?", (key, value, value))
    conn.commit()


def get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def claims_for_deal(conn, deal_key):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM claims WHERE deal_key = ? ORDER BY claim_date", (deal_key,))]


def deal_keys(conn):
    return [r["deal_key"] for r in conn.execute(
        "SELECT DISTINCT deal_key FROM claims ORDER BY deal_key")]


def counts(conn):
    return {
        "posts": conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "claims": conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0],
        "deals": conn.execute("SELECT COUNT(DISTINCT deal_key) FROM claims").fetchone()[0],
    }
