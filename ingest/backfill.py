#!/usr/bin/env python3
"""
Backfill: re-extract posts that were ingested but produced no claim.

Why this exists (June 25 post-mortem): posts are deduped on URL before
extraction, so a run with a broken model burns its headlines as "seen, no
claim" — RSS won't re-serve them, but their title/summary are still in the
posts table. This script releases those claim-less posts back through the
NORMAL pipeline (same filters, same extraction, same failure circuit breaker),
so a recovery is just a re-run, not a parallel code path.

The ingest DB lives in the Actions cache, so a real backfill must run inside a
workflow: dispatch update-site.yml with the `backfill_since` input.

Usage:
    python ingest/backfill.py --since 2026-06-25 [--dry-run]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import store, pipeline


def claimless_posts(conn, since):
    """Posts fetched on/after `since` (YYYY-MM-DD) that never yielded a claim."""
    rows = conn.execute(
        "SELECT url, source, title, summary, published FROM posts "
        "WHERE fetched_at >= ? AND url NOT IN (SELECT post_url FROM claims) "
        "ORDER BY fetched_at", (since,)).fetchall()
    return [dict(r) for r in rows]


def backfill(conn, since, dry_run=False, analyze_fn=None):
    """Release claim-less posts since `since` and re-run the pipeline on them."""
    posts = claimless_posts(conn, since)
    if dry_run or not posts:
        return posts, None
    for p in posts:
        # Clear both the seen-marker and any failure history: a backfill is an
        # explicit "try these again", so the retry cap must not block it.
        conn.execute("DELETE FROM posts WHERE url = ?", (p["url"],))
        conn.execute("DELETE FROM extract_failures WHERE url = ?", (p["url"],))
    conn.commit()
    stats = pipeline.run(conn, sources_fn=lambda: iter(posts), analyze_fn=analyze_fn)
    return posts, stats


if __name__ == "__main__":
    args = sys.argv[1:]
    since = args[args.index("--since") + 1] if "--since" in args else None
    if not since:
        raise SystemExit("Usage: python ingest/backfill.py --since YYYY-MM-DD [--dry-run]")
    dry = "--dry-run" in args
    conn = store.connect()
    posts, stats = backfill(conn, since, dry_run=dry)
    print(f"{len(posts)} claim-less post(s) since {since}" +
          (" (dry-run, nothing re-extracted)." if dry else " released and re-run:"))
    if stats:
        print(f"  claims={stats['claims']} non-transfer={stats['non_transfer']} "
              f"excluded={stats['excluded']} parse-err={stats['parse_err']} "
              f"extract-err={stats['extract_err']}")
        if pipeline.failure_rate_exceeded(stats):
            print("::error::Backfill failure rate exceeded — model still broken?")
            sys.exit(1)
