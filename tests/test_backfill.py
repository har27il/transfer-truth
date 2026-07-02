"""Backfill tests — recovering posts a broken model burned as 'seen, no claim'.

Covers: claim-less posts since the cutoff are found; posts WITH claims are left
alone; dry-run changes nothing; a real run releases the posts (clearing any
retry-cap history) and re-runs them through the normal pipeline so the claims
finally land.
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
    posts, stats = backfill.backfill(conn, "2020-01-01", dry_run=True)
    assert {p["url"] for p in posts} == {"http://x/t1", "http://x/t2"}
    assert stats is None
    assert store.counts(conn)["posts"] == 3          # nothing deleted


def test_backfill_reextracts_and_lands_the_lost_claims():
    conn = _conn()
    _seed_burned_week(conn)
    # simulate the retry cap having given up on one of the burned posts
    for _ in range(store.MAX_EXTRACT_ATTEMPTS + 1):
        store.release_failed_post(conn, "http://x/t1")
        store.add_post(conn, _post("http://x/t1", "Tonali to Spurs here we go"))

    posts, stats = backfill.backfill(conn, "2020-01-01",
                                     analyze_fn=lambda t: dict(GOOD))
    assert len(posts) == 2
    assert stats["claims"] == 2                       # the lost week is recovered
    assert store.counts(conn)["posts"] == 3           # released posts re-admitted
    assert store.counts(conn)["deals"] == 2           # Tonali deal now exists


def test_backfill_respects_the_since_cutoff():
    conn = _conn()
    _seed_burned_week(conn)
    posts, stats = backfill.backfill(conn, "2999-01-01", dry_run=False)
    assert posts == [] and stats is None              # nothing that recent
