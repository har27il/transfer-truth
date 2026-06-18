#!/usr/bin/env python3
"""
Ingestion pipeline: fetch -> dedup -> extract -> filter -> cluster -> store.

Both I/O boundaries are injectable, so the whole pipeline runs offline in tests:
    run(conn, sources_fn=fake_feed, analyze_fn=fake_engine)

Live use needs the NIM key (the extractor calls NVIDIA NIM via engine.run.analyze).
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import store, cluster, sources
from ingest.exclude import is_non_player

DEFAULT_WINDOW = "2026-summer"
MIN_CONFIDENCE = 0.5   # drop vague extractions before they pollute a deal
# How many NIM extractions to run at once. The slow part of ingest is network-bound
# LLM calls, so parallelism is the real speedup (not a smaller model). Capped to stay
# polite to the free-tier rate limit; the SDK's backoff (engine.run) absorbs the rest.
DEFAULT_CONCURRENCY = int(os.environ.get("NIM_CONCURRENCY", "6"))


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
        min_confidence=MIN_CONFIDENCE, concurrency=None):
    if analyze_fn is None:
        from engine.run import analyze as analyze_fn  # deferred: needs NIM key
    if concurrency is None:
        concurrency = DEFAULT_CONCURRENCY
    stats = {"fetched": 0, "dup": 0, "new": 0, "excluded": 0, "non_transfer": 0,
             "low_conf": 0, "no_player": 0, "claims": 0}

    # Phase 1 - dedup (sequential SQLite). Already-seen URLs are skipped HERE, before
    # the expensive extraction, so when ingest.db is persisted between runs a re-run
    # only pays for genuinely new headlines.
    new_posts = []
    for post in sources_fn():
        stats["fetched"] += 1
        if not store.add_post(conn, post):
            stats["dup"] += 1
            continue
        # Drop manager appointments + women's-football items BEFORE the NIM call, so
        # they never cost a token and never become a deal. The post stays marked seen
        # (add_post above), so a later run won't re-extract it.
        excluded, _why = is_non_player(" ".join(filter(None, [post.get("title"), post.get("summary")])))
        if excluded:
            stats["excluded"] += 1
            continue
        stats["new"] += 1
        new_posts.append(post)

    # Phase 2 - extract (network-bound). Parallelize the slow NIM calls under a hard
    # worker cap. SQLite is untouched here, so the DB stays single-threaded and safe;
    # only the I/O-bound LLM calls fan out. Results are gathered in submission order so
    # the run stays deterministic regardless of which call finishes first.
    def _extract(post):
        text = " ".join(filter(None, [post.get("title"), post.get("summary")]))
        return analyze_fn(text)

    workers = max(1, min(concurrency, len(new_posts)))
    results = []  # list of (post, result-or-None)
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_extract, p) for p in new_posts]
            for post, fut in zip(new_posts, futures):
                try:
                    results.append((post, fut.result()))
                except Exception as e:  # one bad NIM call != lose the whole run
                    stats["extract_err"] = stats.get("extract_err", 0) + 1
                    print(f"  ! extract failed for {post.get('url')}: {e}")
    else:
        for post in new_posts:
            try:
                results.append((post, _extract(post)))
            except Exception as e:
                stats["extract_err"] = stats.get("extract_err", 0) + 1
                print(f"  ! extract failed for {post.get('url')}: {e}")

    # Phase 3 - filter + store (sequential SQLite).
    for post, result in results:
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
          f"claims={s['claims']} (excluded={s['excluded']}, "
          f"non-transfer={s['non_transfer']}, low-conf={s['low_conf']})")
    print("  store:", store.counts(conn))
