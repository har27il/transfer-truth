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


# --- refresh + re-open: the deal-124 class of bug --------------------------------
#
# to_club was written once at row creation and never revised, so a row could carry a
# destination nine claims out of date. The resolver then read Wikipedia correctly,
# compared it to the stale value, and collapsed a transfer that had completed.

DIOMANDE_NOTES = ("[auto] player joined Real Madrid, not Paris Saint-Germain - rumour "
                  "did not happen | On 6 August 2026, Diomande signed for Real Madrid.")


def _deal(did, player, to, outcome="unknown", verified="auto", notes="", frm="RB Leipzig"):
    return {"deal_id": did, "player": player, "from_club": frm, "to_club": to,
            "window": WIN, "outcome": outcome, "verified": verified, "notes": notes,
            "outcome_date": "2026-08-07" if outcome != "unknown" else "",
            "outcome_source_url": "http://wiki" if outcome != "unknown" else ""}


def _diomande_conn():
    """Two early PSG claims, then the Real Madrid wave the ledger never saw."""
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Yan Diomande",
                  [{"to_club": "Paris Saint-Germain", "from_club": "RB Leipzig",
                    "source": "BBC Sport", "date": "2026-06-28"},
                   {"to_club": "Paris Saint-Germain", "from_club": "RB Leipzig",
                    "source": "Sky Sports", "date": "2026-06-29"}]
                  + [{"to_club": "Real Madrid", "from_club": "RB Leipzig",
                      "source": s, "date": d}
                     for s, d in [("Sky Sports", "2026-07-24"), ("The Guardian", "2026-07-26"),
                                  ("BBC Sport", "2026-07-27"), ("Sky Germany", "2026-07-29"),
                                  ("The Guardian", "2026-08-05"), ("BBC Sport", "2026-08-06")]])
    return conn


def test_stale_to_club_is_refreshed_on_an_unresolved_row(tmp_path):
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("124", "Yan Diomande", "Paris Saint-Germain")])
    bridge.bridge(_diomande_conn(), deals_path=dp)
    assert _read_deals(dp)[0]["to_club"] == "Real Madrid"


def test_wrong_collapse_is_reopened_and_its_verdict_cleared(tmp_path):
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("124", "Yan Diomande", "Paris Saint-Germain",
                            outcome="collapsed", notes=DIOMANDE_NOTES)])
    stats = bridge.bridge(_diomande_conn(), deals_path=dp)
    row = _read_deals(dp)[0]
    assert row["outcome"] == "unknown"          # handed back to the resolver
    assert row["to_club"] == "Real Madrid"
    assert row["outcome_date"] == "" and row["outcome_source_url"] == ""
    assert row["verified"] == "auto"            # never silently promoted
    assert [d for d, _why in stats["reopened"]] == ["124"]


def test_reopen_uses_the_pre_refresh_to_club(tmp_path):
    """Gate C compares the collapse reason against the OLD destination. If the
    refresh ran first the reason would no longer match and the re-open would
    silently stop firing -- the failure mode this pins."""
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("124", "Yan Diomande", "Paris Saint-Germain",
                            outcome="collapsed", notes=DIOMANDE_NOTES)])
    stats = bridge.bridge(_diomande_conn(), deals_path=dp)
    assert stats["reopened"], "re-open did not fire: Gate B ran before Gate C"


def test_collapse_is_not_reopened_when_the_reason_names_another_club(tmp_path):
    """Precision gate: only collapses that provably rested on the stale destination.
    Here the rumour was Arsenal, so the PSG/Real Madrid revision is irrelevant."""
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("124", "Yan Diomande", "Arsenal", outcome="collapsed",
                            notes=DIOMANDE_NOTES)])
    stats = bridge.bridge(_diomande_conn(), deals_path=dp)
    assert stats["reopened"] == []
    assert _read_deals(dp)[0]["outcome"] == "collapsed"


def test_alias_miss_collapse_is_reopened(tmp_path):
    """Deal 70 shape: rumour said Leeds, resolver said Leeds United, same club."""
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Harry Wilson", [{"to_club": "Leeds", "from_club": "Fulham",
                                          "source": "BBC Sport", "date": "2026-07-01"}])
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("70", "Harry Wilson", "Leeds", outcome="collapsed", frm="Fulham",
                            notes="[auto] player joined Leeds United, not Leeds - "
                                  "rumour did not happen | joined Leeds United in 2026.")])
    stats = bridge.bridge(conn, deals_path=dp)
    assert [d for d, _ in stats["reopened"]] == ["70"]
    assert _read_deals(dp)[0]["outcome"] == "unknown"


def test_completed_rows_are_never_reopened(tmp_path):
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("124", "Yan Diomande", "Paris Saint-Germain",
                            outcome="completed", notes=DIOMANDE_NOTES)])
    bridge.bridge(_diomande_conn(), deals_path=dp)
    row = _read_deals(dp)[0]
    assert row["outcome"] == "completed" and row["to_club"] == "Paris Saint-Germain"


def test_curated_rows_are_never_refreshed_or_reopened(tmp_path):
    """Ground-truth safety: a hand-verified row is never machine-edited, and the
    check is on the trusted SET so 'y' and 'true' are protected too."""
    for flag in ("YES", "y", "true"):
        dp = tmp_path / ("deals-" + flag + ".csv")
        _write_deals(dp, [_deal("124", "Yan Diomande", "Paris Saint-Germain",
                                outcome="collapsed", verified=flag, notes=DIOMANDE_NOTES)])
        original = dp.read_bytes()
        bridge.bridge(_diomande_conn(), deals_path=dp)
        assert dp.read_bytes() == original, "curated row with verified=" + flag + " was edited"


def test_reopen_is_capped_and_defers_the_remainder(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_REOPENS", 2)
    conn = store.connect(":memory:")
    rows = []
    for i in range(5):
        player = "Player" + str(i)
        _seed_cluster(conn, player, [{"to_club": "Real Madrid", "from_club": "RB Leipzig",
                                      "source": "BBC Sport", "date": "2026-08-06"}])
        rows.append(_deal(str(10 + i), player, "Paris Saint-Germain", outcome="collapsed",
                          notes=DIOMANDE_NOTES))
    dp = tmp_path / "deals.csv"
    _write_deals(dp, rows)
    stats = bridge.bridge(conn, deals_path=dp)
    assert len(stats["reopened"]) == 2
    assert [d for d, _ in stats["reopened"]] == ["10", "11"]     # deterministic prefix
    assert stats["deferred"] == ["12", "13", "14"]


def test_refresh_upgrades_a_bare_surname_to_the_full_name(tmp_path):
    """apply.py feeds row['player'] straight to Wikipedia, and a bare 'Olise' lands
    on a disambiguation page."""
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Olise", [{"to_club": "Real Madrid", "from_club": "Bayern Munich",
                                   "source": "Sky Sports", "date": "2026-07-01"}])
    _seed_cluster(conn, "Michael Olise",
                  [{"to_club": "Real Madrid", "from_club": "Bayern Munich",
                    "source": "BBC Sport", "date": "2026-07-02"},
                   {"to_club": "Real Madrid", "from_club": "Bayern Munich",
                    "source": "The Guardian", "date": "2026-07-03"}])
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("79", "Olise", "Real Madrid", frm="Bayern Munich")])
    bridge.bridge(conn, deals_path=dp)
    assert _read_deals(dp)[0]["player"] == "Michael Olise"


def test_split_cluster_attaches_instead_of_creating_a_second_row(tmp_path):
    """The alias map means a bare-surname cluster finds the existing full-name deal."""
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Olise", [{"to_club": "Real Madrid", "from_club": "Bayern Munich",
                                   "source": "Sky Sports", "date": "2026-07-01"}])
    _seed_cluster(conn, "Michael Olise", [{"to_club": "Real Madrid", "from_club": "Bayern Munich",
                                           "source": "BBC Sport", "date": "2026-07-02"}])
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("57", "Michael Olise", "Real Madrid", frm="Bayern Munich")])
    stats = bridge.bridge(conn, deals_path=dp)
    assert stats["created"] == []
    assert len(_read_deals(dp)) == 1


def test_bridge_claims_gathers_the_whole_group_not_just_the_canonical_key(tmp_path):
    """The Olise denial lives on the BARE key. Querying only the canonical key would
    drop it, and it is the claim that stops that meter reading too high."""
    conn = store.connect(":memory:")
    _seed_cluster(conn, "Olise", [{"to_club": "Real Madrid", "from_club": "Bayern Munich",
                                   "source": "Sky Sports", "date": "2026-07-01"}])
    _seed_cluster(conn, "Michael Olise", [{"to_club": "Real Madrid", "from_club": "Bayern Munich",
                                           "source": "BBC Sport", "date": "2026-07-02"}])
    dp, cp = _paths(tmp_path,
                    [_deal("57", "Michael Olise", "Real Madrid", frm="Bayern Munich")], [])
    bridge.bridge_claims(conn, deals_path=dp, claims_path=cp)
    got = _read_claims(cp)
    assert {c["source_name"] for c in got} == {"Sky Sports", "BBC Sport"}
    assert {c["deal_id"] for c in got} == {"57"}


def test_next_id_is_not_reused_after_a_scrub(tmp_path, monkeypatch):
    """Computing the high-water mark after the scrub let a deleted max id be handed
    to the next created deal, which then inherits the dead row's orphaned claims."""
    monkeypatch.setattr(bridge, "is_known_non_player",
                        lambda name: (name or "").strip() == "Derek McInnes")
    conn = store.connect(":memory:")
    _seed_cluster(conn, "New Player", [{"to_club": "Arsenal", "source": "BBC Sport"}])
    dp = tmp_path / "deals.csv"
    _write_deals(dp, [_deal("9", "Derek McInnes", "Rangers")])   # scrubbed: holds the max id
    bridge.bridge(conn, deals_path=dp)
    ids = [r["deal_id"] for r in _read_deals(dp)]
    assert "9" not in ids, "the denylisted row should be gone"
    assert ids == ["10"], "id 9 was reused: " + str(ids)
