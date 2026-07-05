"""Backfill tests — recovering posts a broken model burned as 'seen, no claim'.

Covers: claim-less posts since the cutoff are found; posts WITH claims are left
alone; dry-run changes nothing; batches are capped by --limit (rate-limit
posture: the first live recovery 429-stormed the free tier at full volume);
posts rows are NEVER deleted (they can be the only copy of an old headline);
a real run queues the batch via mark_for_retry — even past the retry cap —
and the claims finally land.
"""
from ingest import store, backfill


def _conn():
    return store.connect(":memory:")


def _post(url, title):
    return {"url": url, "source": "BBC Sport", "title": title, "summary": "", "published": ""}


GOOD = {"is_transfer_claim": True, "player": "Sandro Tonali", "from_club": "Newcastle United",
        "to_club": "Tottenham Hotspur", "stage": "here_we_go", "implied_p": 0.99,
        "source_name": None, "source_identifiable": False,
        "direction_confidence": 0.95, "fee_eur": None}


def _seed_burned_week(conn):
    """Two posts burned with no claim (the broken-model week) + one healthy post
    that DID produce a claim and must not be re-extracted."""
    for url, title in [("http://x/t1", "Tonali to Spurs here we go"),
                       ("http://x/t2", "Tonali medical booked")]:
        store.add_post(conn, _post(url, title))
    store.add_post(conn, _post("http://x/ok", "Isak stays"))
    store.add_claim(conn, {"post_url": "http://x/ok", "deal_key": "isak|2026-summer",
                           "player": "Alexander Isak", "from_club": "", "to_club": "",
                           "stage": "denied", "implied_p": 0.02, "source_name": "BBC Sport",
                           "source_identifiable": 1, "direction_confidence": 0.9,
                           "fee_eur": None, "claim_date": "2026-06-20"})


def test_dry_run_lists_claimless_posts_and_changes_nothing():
    conn = _conn()
    _seed_burned_week(conn)
    batch, stats, remaining = backfill.backfill(conn, "2020-01-01", dry_run=True)
    assert {p["url"] for p in batch} == {"http://x/t1", "http://x/t2"}
    assert stats is None and remaining == 0
    assert store.counts(conn)["posts"] == 3          # nothing touched


def test_backfill_reextracts_and_lands_the_lost_claims_even_past_retry_cap():
    conn = _conn()
    _seed_burned_week(conn)
    # simulate the retry cap having given up on one of the burned posts
    for _ in range(store.MAX_EXTRACT_ATTEMPTS + 1):
        store.release_failed_post(conn, "http://x/t1")
    assert store.should_retry(conn, "http://x/t1") is False

    batch, stats, remaining = backfill.backfill(conn, "2020-01-01",
                                                analyze_fn=lambda t: dict(GOOD))
    assert len(batch) == 2 and remaining == 0
    assert stats["claims"] == 2                       # the lost week is recovered
    assert store.counts(conn)["posts"] == 3           # rows preserved throughout
    assert store.counts(conn)["deals"] == 2           # Tonali deal now exists


def test_failed_batch_is_never_deleted_and_stays_queued():
    """A rate-limit storm must be a retriable non-event: rows intact, batch
    still claim-less, so the next dispatch picks it up again."""
    conn = _conn()
    _seed_burned_week(conn)

    def _always_429(text):
        raise RuntimeError("429 Too Many Requests")

    batch, stats, remaining = backfill.backfill(conn, "2020-01-01",
                                                analyze_fn=_always_429)
    assert stats["extract_err"] == 2
    assert store.counts(conn)["posts"] == 3           # NOTHING deleted
    again, _, _ = backfill.backfill(conn, "2020-01-01", dry_run=True)
    assert {p["url"] for p in again} == {"http://x/t1", "http://x/t2"}  # still queued


def test_limit_caps_the_batch_and_reports_remaining():
    conn = _conn()
    _seed_burned_week(conn)
    batch, stats, remaining = backfill.backfill(conn, "2020-01-01", limit=1,
                                                analyze_fn=lambda t: dict(GOOD))
    assert len(batch) == 1 and remaining == 1
    assert batch[0]["url"] == "http://x/t1"           # oldest first
    batch2, _, remaining2 = backfill.backfill(conn, "2020-01-01", limit=1,
                                              analyze_fn=lambda t: dict(GOOD))
    assert len(batch2) == 1 and remaining2 == 0       # second dispatch finishes


def test_backfill_respects_the_since_cutoff():
    conn = _conn()
    _seed_burned_week(conn)
    batch, stats, remaining = backfill.backfill(conn, "2999-01-01")
    assert batch == [] and stats is None and remaining == 0


def test_examined_non_transfer_posts_leave_the_backfill_queue():
    """REGRESSION (2026-07-05): posts judged non-transfer stayed 'claim-less'
    and the backfill re-chewed the same batch forever. A verdict disposition
    must remove them from the queue; parse failures must stay queued."""
    conn = _conn()
    _seed_burned_week(conn)

    def _non_transfer(text):
        return {"is_transfer_claim": False, "player": None, "from_club": None,
                "to_club": None, "stage": None, "implied_p": None, "source_name": None,
                "source_identifiable": False, "direction_confidence": 0.0, "fee_eur": None}

    batch, stats, remaining = backfill.backfill(conn, "2020-01-01",
                                                analyze_fn=_non_transfer)
    assert len(batch) == 2 and stats["non_transfer"] == 2
    again, _, remaining2 = backfill.backfill(conn, "2020-01-01", dry_run=True)
    assert again == [] and remaining2 == 0            # queue actually advanced
