"""Bridge tests — ingested clusters -> proposed deals.csv rows. Fully offline.

Covers: new cluster creates a row; an ingested player already in deals.csv attaches
instead of duplicating; re-running is idempotent; a crash mid-write leaves deals.csv
intact; to_club is the most-common provisional value. Plus bridge_claims: ingested
claims flow into journalist_claims.csv as verified=auto rows (deduped, legacy rows
preserved) so the leaderboard's input file stops being a frozen hand-made seed.
"""
import csv

import outcome.apply as apply_mod
from ingest import store, cluster, bridge

HEADER = ["deal_id", "player", "from_club", "to_club", "window", "outcome",
          "fee_eur_actual", "outcome_date", "outcome_source_url", "verified", "notes"]
WIN = "2025-summer"


def _write_deals(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEADER})


def _read_deals(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _seed_cluster(conn, player, claims, window=WIN):
    """claims: list of dicts (to_club, from_club, source, date). Returns the deal_key."""
    key = cluster.deal_key(player, window)
    for i, c in enumerate(claims):
        url = f"http://post/{player}/{i}"
        store.add_post(conn, {"url": url, "source": c.get("source", "BBC Sport"),
                              "title": "t", "summary": "s", "published": ""})
        store.add_claim(conn, {
            "post_url": url, "deal_key": key, "player": player,
            "from_club": c.get("from_club", ""), "to_club": c.get("to_club", ""),
            "stage": "talks", "implied_p": 0.35, "source_name": c.get("source", "BBC Sport"),
            "source_identifiable": 1, "direction_confidence": 0.8, "fee_eur": None,
            "claim_date": c.get("date", "2025-08-01"),
        })
    return key


def test_new_cluster_creates_proposed_row(tmp_path):
    p = tmp_path / "deals.csv"
    _write_deals(p, [])
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Marc Cucurella", [{"to_club": "Chelsea", "from_club": "Brighton"}])

    stats = bridge.bridge(conn, deals_path=p)
    assert len(stats["created"]) == 1 and not stats["attached"]
    rows = _read_deals(p)
    assert len(rows) == 1
    r = rows[0]
    assert r["player"] == "Marc Cucurella" and r["to_club"] == "Chelsea"
    assert r["window"] == WIN and r["outcome"] == "unknown" and r["verified"] == "auto"
    assert r["deal_id"] == "1"


def test_existing_player_attaches_not_duplicates(tmp_path):
    p = tmp_path / "deals.csv"
    _write_deals(p, [{
        "deal_id": "5", "player": "Alexander Isak", "from_club": "Newcastle United",
        "to_club": "Liverpool", "window": WIN, "outcome": "completed", "verified": "YES",
    }])
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Alexander Isak", [{"to_club": "Liverpool"}])

    stats = bridge.bridge(conn, deals_path=p)
    assert not stats["created"] and len(stats["attached"]) == 1
    rows = _read_deals(p)
    assert len(rows) == 1                       # no duplicate row
    assert rows[0]["outcome"] == "completed"    # curated row untouched
    assert rows[0]["verified"] == "YES"


def test_idempotent_rerun(tmp_path):
    p = tmp_path / "deals.csv"
    _write_deals(p, [])
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Nico Williams", [{"to_club": "Barcelona"}])

    bridge.bridge(conn, deals_path=p)
    first = p.read_text("utf-8")
    stats2 = bridge.bridge(conn, deals_path=p)
    assert not stats2["created"] and len(stats2["attached"]) == 1
    assert p.read_text("utf-8") == first        # byte-identical, no churn


def test_provisional_to_club_is_most_common(tmp_path):
    p = tmp_path / "deals.csv"
    _write_deals(p, [])
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Some Player", [
        {"to_club": "Chelsea", "date": "2025-08-01"},
        {"to_club": "Chelsea", "date": "2025-08-02"},
        {"to_club": "Arsenal", "date": "2025-08-03"},
    ])
    bridge.bridge(conn, deals_path=p)
    assert _read_deals(p)[0]["to_club"] == "Chelsea"


def test_manager_cluster_is_filtered_not_bridged(tmp_path):
    """A manager appointment sitting in the store (e.g. Derek McInnes, cached before
    the exclusion filter existed) must NOT become a player deal. Bridge checks the
    raw post text behind the cluster and skips it."""
    p = tmp_path / "deals.csv"
    _write_deals(p, [])
    conn = store.connect(":memory:")
    key = cluster.deal_key("Derek McInnes", WIN)
    store.add_post(conn, {"url": "http://post/mcinnes", "source": "BBC Sport",
                          "title": "Rangers appoint Derek McInnes as manager",
                          "summary": "The former Aberdeen boss takes the dugout.", "published": ""})
    store.add_claim(conn, {
        "post_url": "http://post/mcinnes", "deal_key": key, "player": "Derek McInnes",
        "from_club": "Hearts", "to_club": "Rangers", "stage": "talks", "implied_p": 0.5,
        "source_name": "BBC Sport", "source_identifiable": 1, "direction_confidence": 0.9,
        "fee_eur": None, "claim_date": "2025-08-01",
    })

    stats = bridge.bridge(conn, deals_path=p)
    assert not stats["created"]
    assert key in stats["excluded"]
    assert _read_deals(p) == []                 # nothing written


def test_known_non_player_denylist_blocks_bridge(tmp_path):
    """Backstop: a confirmed manager on the denylist is excluded even when the cached
    post text carries NO appointment keyword ('McInnes leaves Hearts for Rangers').
    This is the case that resurrected McInnes from the store after the text filter
    alone missed his headline."""
    p = tmp_path / "deals.csv"
    _write_deals(p, [])
    conn = store.connect(":memory:")
    key = cluster.deal_key("Derek McInnes", WIN)
    store.add_post(conn, {"url": "http://post/mci2", "source": "BBC Sport",
                          "title": "McInnes leaves Hearts for Rangers",   # no role word
                          "summary": "The Scot is on his way to Ibrox.", "published": ""})
    store.add_claim(conn, {
        "post_url": "http://post/mci2", "deal_key": key, "player": "Derek McInnes",
        "from_club": "Heart of Midlothian", "to_club": "Rangers", "stage": "talks",
        "implied_p": 0.6, "source_name": "BBC Sport", "source_identifiable": 1,
        "direction_confidence": 0.9, "fee_eur": None, "claim_date": "2025-08-01",
    })

    stats = bridge.bridge(conn, deals_path=p)
    assert not stats["created"] and key in stats["excluded"]
    assert _read_deals(p) == []


def test_atomic_write_leaves_deals_intact_on_crash(tmp_path, monkeypatch):
    p = tmp_path / "deals.csv"
    _write_deals(p, [])
    before = p.read_text("utf-8")
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Crash Test", [{"to_club": "Spurs"}])

    monkeypatch.setattr(apply_mod.os, "replace",
                        lambda *a: (_ for _ in ()).throw(OSError("disk gone mid-write")))
    try:
        bridge.bridge(conn, deals_path=p)
    except OSError:
        pass
    assert p.read_text("utf-8") == before                       # ground truth intact
    leftovers = [f for f in p.parent.iterdir() if f.name.startswith(".deals.")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


# ---- bridge_claims: ingested claims -> journalist_claims.csv ------------------

CLAIMS_HEADER = ["claim_id", "deal_id", "source_name", "platform", "claim_date",
                 "stage", "source_url", "raw_quote", "verified"]


def _write_claims(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CLAIMS_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CLAIMS_HEADER})


def _read_claims(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


LEGACY = {"claim_id": "1", "deal_id": "5", "source_name": "Fabrizio Romano",
          "platform": "twitter", "claim_date": "2025-08-01", "stage": "here_we_go",
          "source_url": "http://x/legacy", "raw_quote": "here we go", "verified": "YES"}


def _paths(tmp_path, deals, claims):
    dp, cp = tmp_path / "deals.csv", tmp_path / "claims.csv"
    _write_deals(dp, deals)
    _write_claims(cp, claims)
    return dp, cp


def test_claims_append_as_auto_for_known_deals(tmp_path):
    dp, cp = _paths(tmp_path, [{"deal_id": "5", "player": "Alexander Isak",
                                "window": WIN, "outcome": "unknown", "verified": "auto"}],
                    [LEGACY])
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Alexander Isak", [{"to_club": "Liverpool", "source": "Sky Sports"}])

    added = bridge.bridge_claims(conn, deals_path=dp, claims_path=cp)
    assert len(added) == 1
    rows = _read_claims(cp)
    assert rows[0] == LEGACY                       # hand-seeded row byte-preserved
    new = rows[1]
    assert new["deal_id"] == "5" and new["source_name"] == "Sky Sports"
    assert new["verified"] == "auto"               # PROPOSED: does not score yet
    assert new["claim_id"] == "2"                  # ids continue after the legacy max


def test_claims_append_is_idempotent_and_dedupes_source_stage(tmp_path):
    """The same outlet re-reporting one deal daily is correlated evidence on one
    outcome — dedup per (deal, source, stage) keeps claim spam from stacking
    Brier samples. Re-running adds nothing."""
    dp, cp = _paths(tmp_path, [{"deal_id": "5", "player": "Alexander Isak",
                                "window": WIN, "outcome": "unknown", "verified": "auto"}], [])
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Alexander Isak", [
        {"to_club": "Liverpool", "source": "Sky Sports", "date": "2025-08-01"},
        {"to_club": "Liverpool", "source": "Sky Sports", "date": "2025-08-02"},  # same source+stage
        {"to_club": "Liverpool", "source": "BBC Sport", "date": "2025-08-02"},
    ])
    added = bridge.bridge_claims(conn, deals_path=dp, claims_path=cp)
    assert {a["source_name"] for a in added} == {"Sky Sports", "BBC Sport"}
    assert len(added) == 2
    again = bridge.bridge_claims(conn, deals_path=dp, claims_path=cp)
    assert again == []                             # idempotent rerun
    assert len(_read_claims(cp)) == 2


def test_claims_for_clusters_without_a_deal_are_skipped(tmp_path):
    dp, cp = _paths(tmp_path, [], [])              # no deals at all
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Marc Cucurella", [{"to_club": "Chelsea"}])
    assert bridge.bridge_claims(conn, deals_path=dp, claims_path=cp) == []
    assert _read_claims(cp) == []


def test_claims_without_a_source_never_reach_ground_truth(tmp_path):
    dp, cp = _paths(tmp_path, [{"deal_id": "9", "player": "Marc Cucurella",
                                "window": WIN, "outcome": "unknown", "verified": "auto"}], [])
    conn = store.connect(":memory:")
    key = cluster.deal_key("Marc Cucurella", WIN)
    store.add_post(conn, {"url": "http://p/nosrc", "source": "", "title": "t", "summary": ""})
    store.add_claim(conn, {"post_url": "http://p/nosrc", "deal_key": key,
                           "player": "Marc Cucurella", "from_club": "", "to_club": "Chelsea",
                           "stage": "talks", "implied_p": 0.35, "source_name": "",
                           "source_identifiable": 0, "direction_confidence": 0.8,
                           "fee_eur": None, "claim_date": "2025-08-01"})
    assert bridge.bridge_claims(conn, deals_path=dp, claims_path=cp) == []
    assert _read_claims(cp) == []


def test_denylisted_auto_rows_are_scrubbed_from_the_ledger(tmp_path):
    """REGRESSION (2026-07-05): women's deals created BEFORE their names hit the
    denylist survived in deals.csv forever and got resolved/promotable. The
    bridge now scrubs machine-created (verified=auto) denylisted rows on every
    run; hand-curated rows are never auto-deleted."""
    p = tmp_path / "deals.csv"
    _write_deals(p, [
        {"deal_id": "1", "player": "Mary Earps", "window": WIN,
         "outcome": "completed", "verified": "auto"},
        {"deal_id": "2", "player": "Alexander Isak", "window": WIN,
         "outcome": "completed", "verified": "YES"},
        {"deal_id": "3", "player": "Mary Earps", "window": "2024-summer",
         "outcome": "completed", "verified": "YES"},   # curated -> untouchable
    ])
    conn = store.connect(":memory:")
    stats = bridge.bridge(conn, deals_path=p)
    assert [r["player"] for r in stats["scrubbed"]] == ["Mary Earps"]
    remaining = _read_deals(p)
    assert [r["deal_id"] for r in remaining] == ["2", "3"]


def test_orphaned_auto_claims_are_pruned_with_their_deal(tmp_path):
    dp, cp = _paths(tmp_path, [{"deal_id": "2", "player": "Alexander Isak",
                                "window": WIN, "outcome": "unknown", "verified": "auto"}],
                    [{"claim_id": "1", "deal_id": "99", "source_name": "BBC Sport",
                      "stage": "official", "claim_date": "2026-06-01",
                      "source_url": "http://x/orphan", "verified": "auto"},
                     LEGACY])  # LEGACY is deal 5 (gone) but verified=YES -> kept
    conn = store.connect(":memory:")
    bridge.bridge_claims(conn, deals_path=dp, claims_path=cp)
    rows = _read_claims(cp)
    assert [r["claim_id"] for r in rows] == ["1"] or [r["claim_id"] for r in rows] == [LEGACY["claim_id"]]
    # precise: the auto orphan (deal 99) is gone; the YES legacy row survives
    assert all(not (r["deal_id"] == "99" and r["verified"] == "auto") for r in rows)
    assert any(r["verified"] == "YES" for r in rows)
