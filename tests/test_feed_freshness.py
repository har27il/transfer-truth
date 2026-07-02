"""Visitor-facing staleness tests — the feed must never present a dead pipeline
as live (June 25: seven days of confident, week-old numbers).

Covers data_freshness(): no stamp -> unknown; fresh stamp -> as-of label, not
stale; old stamp -> stale hours over the threshold. The stamp itself is written
only by a HEALTHY pipeline run (covered in test_ingest).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import build_feed
from ingest import store


def _conn():
    return store.connect(":memory:")


NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


def test_no_stamp_means_unknown_freshness():
    assert build_feed.data_freshness(_conn(), now=NOW) == (None, None)


def test_fresh_stamp_yields_asof_label_below_threshold():
    conn = _conn()
    store.set_meta(conn, store.LAST_INGEST_KEY, (NOW - timedelta(hours=3)).isoformat())
    label, age_h = build_feed.data_freshness(conn, now=NOW)
    assert label == "02 Jul 09:00 UTC"
    assert age_h < build_feed.STALE_AFTER_HOURS


def test_old_stamp_is_flagged_stale():
    conn = _conn()
    store.set_meta(conn, store.LAST_INGEST_KEY, (NOW - timedelta(days=3)).isoformat())
    label, age_h = build_feed.data_freshness(conn, now=NOW)
    assert label and age_h > build_feed.STALE_AFTER_HOURS


def test_garbage_stamp_fails_safe_to_unknown():
    conn = _conn()
    store.set_meta(conn, store.LAST_INGEST_KEY, "not-a-timestamp")
    assert build_feed.data_freshness(conn, now=NOW) == (None, None)
