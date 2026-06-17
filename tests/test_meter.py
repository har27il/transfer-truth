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


def _m(stage, prob, spread):
    return {"latest_stage": stage, "probability": prob, "spread": spread}


def test_classify_tier_agreed_needs_stage_low_spread_and_high_prob():
    # here-we-go / agreed-terms where sources broadly agree -> the Agreed bucket
    assert meter.classify_tier(_m("agreement", 0.85, 0.10)) == "agreed"
    assert meter.classify_tier(_m("here_we_go", 0.99, 0.05)) == "agreed"
    assert meter.classify_tier(_m("official", 0.99, 0.19)) == "agreed"   # Isak-like spread


def test_classify_tier_contested_stays_live_despite_agreement_stage():
    """The trap: recency decay floats a fresh 'agreement' over an older denial to a high
    prob, but the deal is DISPUTED (wide spread). It must NOT get an Agreed badge."""
    assert meter.classify_tier(_m("agreement", 0.78, 0.97)) == "live"   # Guehi-like spread


def test_classify_tier_early_and_cold_deals_stay_live():
    assert meter.classify_tier(_m("talks", 0.25, 0.0)) == "live"
    assert meter.classify_tier(_m("interest", 0.15, 0.0)) == "live"
    assert meter.classify_tier(_m("agreement", 0.45, 0.05)) == "live"   # below the prob floor


def test_classify_tier_matches_real_meter_math_on_demo_archetypes():
    """Validate the spread cut against actual deal_probability output (not hand-set dicts):
    Isak (all sources commit) lands 'agreed'; Guehi (a credible denial) stays 'live'."""
    isak = meter.deal_probability(
        [_claim(0.99, "Fabrizio Romano", stage="here_we_go"),
         _claim(0.99, "Sky Sports", stage="official"),
         _claim(0.80, "BBC Sport", stage="agreement")], REL, POP, TODAY)
    guehi = meter.deal_probability(
        [_claim(0.99, "Fabrizio Romano", stage="here_we_go"),
         _claim(0.02, "BBC Sport", stage="denied"),
         _claim(0.35, "Sky Sports", stage="talks")], REL, POP, TODAY)
    assert meter.classify_tier(isak) == "agreed"
    assert meter.classify_tier(guehi) == "live"


def test_meters_max_age_drops_stale_deals():
    """A persisted DB accumulates old claims; max_age_days hides deals whose newest
    claim is past the display window, so the live feed stays current."""
    conn = store.connect(":memory:")
    # fresh deal (today) and a stale one (40 days old)
    for player, d in [("Fresh Deal", "2025-09-01"), ("Stale Deal", "2025-07-23")]:
        key = cluster.deal_key(player, "2025-summer")
        url = f"http://x/{player}"
        store.add_post(conn, {"url": url, "source": "BBC Sport", "title": "t", "summary": ""})
        store.add_claim(conn, {"post_url": url, "deal_key": key, "player": player,
                               "from_club": "A", "to_club": "B", "stage": "talks", "implied_p": 0.8,
                               "source_name": "BBC Sport", "source_identifiable": 1,
                               "direction_confidence": 0.9, "fee_eur": None, "claim_date": d})
    fresh_only = meter.meters(conn, today=TODAY, reliability=REL, pop_weight=POP, max_age_days=21)
    assert [m["player"] for m in fresh_only] == ["Fresh Deal"]
    both = meter.meters(conn, today=TODAY, reliability=REL, pop_weight=POP)  # no filter
    assert sorted(m["player"] for m in both) == ["Fresh Deal", "Stale Deal"]
