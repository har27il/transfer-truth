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

Rate-limit posture (lesson from the first recovery attempt, which 429-stormed
the free tier at ~200 posts x concurrency 6): each run re-extracts at most
--limit posts, oldest first. Dispatch repeatedly until "0 remaining". Posts are
never deleted — a failed batch just stays queued for the next dispatch.

Usage:
    python ingest/backfill.py --since 2026-06-25 [--limit 60] [--dry-run]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import store, pipeline

DEFAULT_LIMIT = 60   # posts per dispatch: bounded NIM spend, kind to the free tier


def claimless_posts(conn, since):
    """Posts fetched on/after `since` (YYYY-MM-DD) with no claim AND no recorded
    verdict, oldest first (the oldest are closest to falling out of every feed).

    The dispositions exclusion is what makes repeated dispatches ADVANCE: a
    post re-examined and judged non-transfer gets a verdict and leaves this
    set. Without it the backfill re-chewed the same oldest batch forever
    (2026-07-05: 'remaining' pinned at 325 across three green runs)."""
    rows = conn.execute(
        "SELECT url, source, title, summary, published FROM posts "
        "WHERE fetched_at >= ? AND url NOT IN (SELECT post_url FROM claims) "
        "AND url NOT IN (SELECT url FROM dispositions) "
        "ORDER BY fetched_at", (since,)).fetchall()
    return [dict(r) for r in rows]


def backfill(conn, since, dry_run=False, analyze_fn=None, limit=DEFAULT_LIMIT):
    """Queue up to `limit` claim-less posts since `since` for re-extraction and
    run the normal pipeline over them. Returns (batch, stats, remaining)."""
    posts = claimless_posts(conn, since)
    batch, remaining = posts[:limit], max(0, len(posts) - limit)
    if dry_run or not batch:
        return batch, None, remaining
    # mark_for_retry resets failure history so the retry cap can't block a
    # deliberate recovery; the pipeline's dedup then re-admits these URLs.
    store.mark_for_retry(conn, [p["url"] for p in batch])
    stats = pipeline.run(conn, sources_fn=lambda: iter(batch), analyze_fn=analyze_fn)
    return batch, stats, remaining


if __name__ == "__main__":
    args = sys.argv[1:]
    since = args[args.index("--since") + 1] if "--since" in args else None
    if not since:
        raise SystemExit("Usage: python ingest/backfill.py --since YYYY-MM-DD "
                         "[--limit N] [--dry-run]")
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else DEFAULT_LIMIT
    dry = "--dry-run" in args
    conn = store.connect()
    batch, stats, remaining = backfill(conn, since, dry_run=dry, limit=limit)
    print(f"{len(batch)} claim-less post(s) in this batch since {since}; "
          f"{remaining} remaining" +
          (" (dry-run, nothing re-extracted)." if dry else "."))
    if stats:
        print(f"  claims={stats['claims']} non-transfer={stats['non_transfer']} "
              f"excluded={stats['excluded']} parse-err={stats['parse_err']} "
              f"extract-err={stats['extract_err']}")
        if remaining:
            print(f"NOTE: dispatch the workflow again to continue ({remaining} to go).")
        if pipeline.failure_rate_exceeded(stats):
            print("::error::Backfill failure rate exceeded — rate limit or broken "
                  "model; the batch stays queued and will retry on re-dispatch.")
            sys.exit(1)
