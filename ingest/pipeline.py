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
# Circuit breaker: when most extractions fail, the model/provider is broken —
# that must fail the run (red X, GitHub email) instead of committing a "quiet
# news day". June 25: a bad model swap classified 67/68 posts as noise for a
# week because parse failures were silently counted as non-transfer. The floor
# keeps a genuinely tiny run (a few flaky calls) from false-alarming.
FAIL_RATE_LIMIT = 0.5
FAIL_MIN_ATTEMPTS = 5
LAST_SUCCESS_KEY = store.LAST_INGEST_KEY
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
             "low_conf": 0, "no_player": 0, "claims": 0,
             "parse_err": 0, "extract_err": 0}

    # Phase 1 - dedup (sequential SQLite). Already-seen URLs are skipped HERE, before
    # the expensive extraction, so when ingest.db is persisted between runs a re-run
    # only pays for genuinely new headlines.
    new_posts = []
    for post in sources_fn():
        stats["fetched"] += 1
        if not store.add_post(conn, post):
            # Seen before — but a post whose extraction FAILED (below the retry
            # cap) or was queued by backfill is re-admitted instead of skipped.
            if not store.should_retry(conn, post["url"]):
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
                    stats["extract_err"] += 1
                    store.release_failed_post(conn, post["url"])
                    print(f"  ! extract failed for {post.get('url')}: {e}")
    else:
        for post in new_posts:
            try:
                results.append((post, _extract(post)))
            except Exception as e:
                stats["extract_err"] += 1
                store.release_failed_post(conn, post["url"])
                print(f"  ! extract failed for {post.get('url')}: {e}")

    # Phase 3 - filter + store (sequential SQLite).
    # A None result is a PARSE FAILURE (model broke the JSON contract), not a
    # "no transfer here" answer — conflating the two hid the June 25 outage.
    # Failed posts are released for retry (store.release_failed_post) so a bad
    # model day self-heals instead of permanently burning the headlines.
    for post, result in results:
        if result is None:
            stats["parse_err"] += 1
            store.release_failed_post(conn, post["url"])
            continue
        store.clear_failure(conn, post["url"])
        if not result.get("is_transfer_claim"):
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

    if not failure_rate_exceeded(stats):
        # Healthy run: stamp it so the feed can show "data as of ..." and flag
        # staleness to VISITORS, not just the developer's inbox.
        store.set_meta(conn, LAST_SUCCESS_KEY, datetime.now().astimezone().isoformat(timespec="seconds"))
    return stats


def failure_rate_exceeded(stats):
    """True when extraction failures (parse + API) dominate the run — the
    broken-model signature. Successes already stored are kept; the caller
    should exit non-zero so the cron goes red instead of committing silence."""
    attempts = stats["parse_err"] + stats["extract_err"] + stats["non_transfer"] \
        + stats["low_conf"] + stats["no_player"] + stats["claims"]
    failures = stats["parse_err"] + stats["extract_err"]
    return attempts >= FAIL_MIN_ATTEMPTS and failures / attempts > FAIL_RATE_LIMIT


if __name__ == "__main__":
    conn = store.connect()
    print("Ingesting live feeds (needs NVIDIA_API_KEY for extraction)...")
    s = run(conn)
    print(f"  fetched={s['fetched']} new={s['new']} dup={s['dup']} "
          f"claims={s['claims']} (excluded={s['excluded']}, "
          f"non-transfer={s['non_transfer']}, low-conf={s['low_conf']}, "
          f"parse-err={s['parse_err']}, extract-err={s['extract_err']})")
    print("  store:", store.counts(conn))
    if failure_rate_exceeded(s):
        print(f"::error::Extraction failure rate over {FAIL_RATE_LIMIT:.0%} — "
              f"model or provider is broken; failed posts were released for retry.")
        sys.exit(1)
