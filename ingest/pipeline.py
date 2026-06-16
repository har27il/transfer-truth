#!/usr/bin/env python3
"""
Ingestion pipeline: fetch -> dedup -> extract -> filter -> cluster -> store.

Both I/O boundaries are injectable, so the whole pipeline runs offline in tests:
    run(conn, sources_fn=fake_feed, analyze_fn=fake_engine)

Live use needs the NIM key (the extractor calls NVIDIA NIM via engine.run.analyze).
"""
import sys
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import store, cluster, sources

DEFAULT_WINDOW = "2026-summer"
MIN_CONFIDENCE = 0.5   # drop vague extractions before they pollute a deal


def _iso_date(published):
    """RFC-822 RSS date -> 'YYYY-MM-DD'; fall back to today if unparseable."""
    if published:
        try:
            return parsedate_to_datetime(published).date().isoformat()
        except (TypeError, ValueError):
            pass
    return date.today().isoformat()


def _to_claim(post, result, deal_key, window):
    """Build a store.claims row from a post + an engine extraction result.

    Source attribution: prefer the journalist the text actually credits; if none,
    fall back to the outlet that published it (still an identifiable source)."""
    credited = result.get("source_identifiable") and result.get("source_name")
    src_name = result["source_name"] if credited else post.get("source", "")
    return {
        "post_url": post["url"],
        "deal_key": deal_key,
        "player": result.get("player"),
        "from_club": result.get("from_club"),
        "to_club": result.get("to_club"),
        "stage": result.get("stage"),
        "implied_p": result.get("implied_p"),
        "source_name": src_name,
        "source_identifiable": 1 if (credited or post.get("source")) else 0,
        "direction_confidence": result.get("direction_confidence"),
        "fee_eur": result.get("fee_eur"),
        "claim_date": _iso_date(post.get("published")),
    }


def run(conn, sources_fn=sources.fetch_all, analyze_fn=None, window=DEFAULT_WINDOW,
        min_confidence=MIN_CONFIDENCE):
    if analyze_fn is None:
        from engine.run import analyze as analyze_fn  # deferred: needs NIM key
    stats = {"fetched": 0, "dup": 0, "new": 0, "non_transfer": 0,
             "low_conf": 0, "no_player": 0, "claims": 0}
    for post in sources_fn():
        stats["fetched"] += 1
        if not store.add_post(conn, post):
            stats["dup"] += 1
            continue
        stats["new"] += 1
        text = " ".join(filter(None, [post.get("title"), post.get("summary")]))
        try:
            result = analyze_fn(text)
        except Exception as e:  # one bad NIM call (rate limit/timeout) != lose the whole run
            stats["extract_err"] = stats.get("extract_err", 0) + 1
            print(f"  ! extract failed for {post.get('url')}: {e}")
            continue
        if not result or not result.get("is_transfer_claim"):
            stats["non_transfer"] += 1
            continue
        if (result.get("direction_confidence") or 0) < min_confidence:
            stats["low_conf"] += 1
            continue
        key = cluster.deal_key(result.get("player"), window)
        if not key:
            stats["no_player"] += 1
            continue
        if store.add_claim(conn, _to_claim(post, result, key, window)) is not None:
            stats["claims"] += 1
    return stats


if __name__ == "__main__":
    conn = store.connect()
    print("Ingesting live feeds (needs NVIDIA_API_KEY for extraction)...")
    s = run(conn)
    print(f"  fetched={s['fetched']} new={s['new']} dup={s['dup']} "
          f"claims={s['claims']} (non-transfer={s['non_transfer']}, "
          f"low-conf={s['low_conf']})")
    print("  store:", store.counts(conn))
