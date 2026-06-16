"""Live probability meter tests — pure functions with injected reliability + today."""
from datetime import date

from ingest import meter, store, cluster

REL = {"Fabrizio Romano": 0.80, "BBC Sport": 0.78, "Sky Sports": 0.81}
POP = 0.75
TODAY = date(2025, 9, 1)


def _claim(p, source, date_="2025-09-01", stage="talks", player="Player X", to="Club Y"):
    return {"implied_p": p, "source_name": source, "claim_date": date_,
            "stage": stage, "player": player, "to_club": to, "from_club": "Club Z"}


def test_reliable_here_we_go_is_high_and_green():
    m = meter.deal_probability([_claim(0.99, "Fabrizio Romano", stage="here_we_go")], REL, POP, TODAY)
    assert m["percent"] == 99 and m["label"] == "Likely" and m["color"] == "green"


def test_single_denied_is_cold():
    m = meter.deal_probability([_claim(0.02, "Fabrizio Romano", stage="denied")], REL, POP, TODAY)
    assert m["percent"] == 2 and m["color"] == "red"


def test_conflicting_sources_are_contested():
    m = meter.deal_probability(
        [_claim(0.99, "Fabrizio Romano", stage="here_we_go"),
         _claim(0.02, "BBC Sport", stage="denied")], REL, POP, TODAY)
    assert 40 <= m["percent"] <= 60
    assert m["label"] == "Contested" and m["color"] == "yellow"


def test_independent_corroboration_raises_probability():
    one = meter.deal_probability([_claim(0.80, "Fabrizio Romano", stage="agreement")], REL, POP, TODAY)
    three = meter.deal_probability(
        [_claim(0.80, "Fabrizio Romano", stage="agreement"),
         _claim(0.80, "BBC Sport", stage="agreement"),
         _claim(0.80, "Sky Sports", stage="agreement")], REL, POP, TODAY)
    assert three["probability"] > one["probability"]   # 3 outlets > 1 saying the same


def test_recency_decay_discounts_stale_claims():
    # fresh strong 'here we go' vs a 2-month-old weak 'interest' -> stays high
    m = meter.deal_probability(
        [_claim(0.99, "Fabrizio Romano", date_="2025-09-01", stage="here_we_go"),
         _claim(0.15, "BBC Sport", date_="2025-07-01", stage="interest")], REL, POP, TODAY)
    assert m["probability"] > 0.90    # stale low claim is decayed away


def test_unknown_source_uses_population_weight():
    m = meter.deal_probability([_claim(0.60, "Random Blog", stage="advanced")], {}, POP, TODAY)
    assert m is not None and m["percent"] == 60   # unknown -> pop weight, still computes


def test_unscorable_returns_none():
    assert meter.deal_probability([], REL, POP, TODAY) is None
    assert meter.deal_probability([{"implied_p": None, "source_name": "x"}], REL, POP, TODAY) is None


def test_meters_over_store_sorts_hottest_first():
    conn = store.connect(":memory:")
    for player, p, src in [("Hot Deal", 0.99, "Fabrizio Romano"), ("Cold Deal", 0.15, "BBC Sport")]:
        key = cluster.deal_key(player, "2025-summer")
        url = f"http://x/{player}"
        store.add_post(conn, {"url": url, "source": src, "title": "t", "summary": ""})
        store.add_claim(conn, {"post_url": url, "deal_key": key, "player": player,
                               "from_club": "A", "to_club": "B", "stage": "talks", "implied_p": p,
                               "source_name": src, "source_identifiable": 1,
                               "direction_confidence": 0.9, "fee_eur": None, "claim_date": "2025-09-01"})
    rows = meter.meters(conn, today=TODAY, reliability=REL, pop_weight=POP)
    assert [m["player"] for m in rows] == ["Hot Deal", "Cold Deal"]
    assert all("deal_key" in m for m in rows)
