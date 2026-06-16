"""Bridge tests — ingested clusters -> proposed deals.csv rows. Fully offline.

Covers: new cluster creates a row; an ingested player already in deals.csv attaches
instead of duplicating; re-running is idempotent; a crash mid-write leaves deals.csv
intact; to_club is the most-common provisional value.
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
