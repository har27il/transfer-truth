"""Decision-logic tests — the part that, if wrong, corrupts ground truth.

The headline test re-derives ALL 38 hand-labelled outcomes in deals.csv from each
player's real-world resolution, proving the classifier (and club-name
normalization) reproduces the human labels with zero disagreements — including the
four hijack players who appear in both a completed and a collapsed rumour.
"""
import csv
import json
from pathlib import Path

from outcome.detect import classify, same_club, club_token_in_text, COMPLETED, COLLAPSED, UNKNOWN

ROOT = Path(__file__).resolve().parent.parent
DEALS = ROOT / "ground-truth" / "deals.csv"
RESOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "resolutions.json").read_text("utf-8"))


def _deals():
    with open(DEALS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["outcome"].strip().lower() in ("completed", "collapsed"):
                yield r


def test_reproduces_all_ground_truth_outcomes():
    mismatches = []
    n = 0
    for d in _deals():
        res = RESOLUTIONS.get(d["player"])
        assert res is not None, f"no fixture resolution for {d['player']}"
        got, reason = classify(d, res)
        want = d["outcome"].strip().lower()
        n += 1
        if got != want:
            mismatches.append(f"deal {d['deal_id']} {d['player']}->{d['to_club']}: "
                              f"want {want}, got {got} ({reason})")
    assert not mismatches, "Classifier disagreed with ground truth:\n" + "\n".join(mismatches)
    assert n == 38, f"expected 38 resolved deals, scored {n}"


def test_classifier_never_invents_an_outcome_without_evidence():
    """D-safety: every 'unclear' resolution must stay unknown, never completed/collapsed."""
    deal = {"to_club": "Liverpool", "from_club": "Crystal Palace"}
    assert classify(deal, {"status": "unclear", "joined_club": None})[0] == UNKNOWN
    # 'moved' with no named club is not positive evidence
    assert classify(deal, {"status": "moved", "joined_club": None})[0] == UNKNOWN


def test_hijack_one_fact_two_outcomes():
    """Eze's single real move (to Arsenal) must complete the Arsenal rumour AND
    collapse the Spurs rumour."""
    eze = RESOLUTIONS["Eberechi Eze"]
    arsenal = {"to_club": "Arsenal", "from_club": "Crystal Palace"}
    spurs = {"to_club": "Tottenham Hotspur", "from_club": "Crystal Palace"}
    assert classify(arsenal, eze)[0] == COMPLETED
    assert classify(spurs, eze)[0] == COLLAPSED


def test_stayed_collapses_only_after_window_closes():
    deal = {"to_club": "Liverpool", "from_club": "Crystal Palace"}
    assert classify(deal, {"status": "stayed", "window_closed": True})[0] == COLLAPSED
    # window still open -> we don't know yet
    assert classify(deal, {"status": "stayed", "window_closed": False})[0] == UNKNOWN


def test_moved_elsewhere_is_collapse():
    deal = {"to_club": "Newcastle United", "from_club": "Eintracht Frankfurt"}
    assert classify(deal, {"status": "moved", "joined_club": "Liverpool"})[0] == COLLAPSED


def test_same_club_normalization():
    assert same_club("Newcastle", "Newcastle United")
    assert same_club("Spurs", "Tottenham Hotspur")
    assert same_club("Man Utd", "Manchester United")
    assert same_club("Liverpool FC", "Liverpool")
    assert same_club("Bayern", "Bayern Munich")
    assert same_club("Al-Nassr", "Al-Nassr")


def test_same_club_rejects_distinct_clubs():
    assert not same_club("Manchester United", "Manchester City")
    assert not same_club("AC Milan", "Inter Milan")
    assert not same_club("Newcastle United", "Bayern Munich")
    assert not same_club("", "Arsenal")


def test_club_token_in_text_guards_wrong_page():
    palace_page = "Marc Guehi is an English footballer who plays for Crystal Palace."
    assert club_token_in_text("Crystal Palace", palace_page)
    assert club_token_in_text("VfB Stuttgart", "joined from VfB Stuttgart in 2025")
    # wrong page: a same-name player's page that never mentions the selling club
    assert not club_token_in_text("Crystal Palace", "He plays cricket for Surrey.")
    assert not club_token_in_text("Crystal Palace", "")
    # distinctive token must match the right club, not just a shared generic word
    assert not club_token_in_text("Manchester United", "He signed for Newcastle City fans.")
