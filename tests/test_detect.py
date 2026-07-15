"""Decision-logic tests — the part that, if wrong, corrupts ground truth.

The headline test re-derives ALL trusted (verified=YES) outcomes in deals.csv from
each player's real-world resolution, proving the classifier (and club-name
normalization) reproduces the human labels with zero disagreements — including the
four hijack players who appear in both a completed and a collapsed rumour. Every
promotion wave must add its fixtures here (outcome/promote.py review evidence →
tests/fixtures/resolutions.json), keeping ground truth reproducible forever.
"""
import csv
import json
from pathlib import Path

from outcome.detect import classify, same_club, club_token_in_text, COMPLETED, COLLAPSED, UNKNOWN

ROOT = Path(__file__).resolve().parent.parent
DEALS = ROOT / "ground-truth" / "deals.csv"
RESOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "resolutions.json").read_text("utf-8"))


def _deals():
    """Only the HAND-LABELLED ground truth (verified=YES). Auto-resolved rows
    (verified=auto) are machine proposals appended by the live loop — they have no
    hand fixture by design, so iterating them would wrongly fail this gate."""
    with open(DEALS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("verified") or "").strip().upper() != "YES":
                continue
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
    # 38 hand-labelled originals + 20 promoted 2026-07-05 (first promotion wave)
    # + 16 from the 2026-07-05 web census (6 corrected/promoted + 10 coverage gaps)
    # + 16 promoted 2026-07-15 (Touré wave + 15-deal review: Tonali, Santos,
    #   George, Steur, Duran, Meslier, Clarke, Darlow, Devlin, Lockyer,
    #   Tchaouna, Said, Tielemans, Smith, Rodriguez)
    assert n == 90, f"expected 90 resolved deals, scored {n}"


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


def test_blank_destination_departure_completes():
    """The Ibrahima Konate bug: a rumour that named NO destination (just 'leaving
    Liverpool'). He moved to Real Madrid, so the departure happened -> COMPLETED,
    NOT collapsed. Before the fix, same_club('Real Madrid', '') was False and this
    fell through to 'rumour did not happen'."""
    deal = {"to_club": "", "from_club": "Liverpool"}
    outcome, reason = classify(deal, {"status": "moved", "joined_club": "Real Madrid"})
    assert outcome == COMPLETED
    assert "Real Madrid" in reason and "Liverpool" in reason
    # also when the field is missing entirely, not just empty-string
    assert classify({"from_club": "Liverpool"},
                    {"status": "moved", "joined_club": "Real Madrid"})[0] == COMPLETED


def test_moved_to_origin_club_is_not_a_transfer():
    """'moved' but the named club IS the origin = renewal/stay. Refuse to call it a
    completed transfer (D-safety: when contradictory, stay UNKNOWN)."""
    deal = {"to_club": "", "from_club": "Liverpool"}
    assert classify(deal, {"status": "moved", "joined_club": "Liverpool"})[0] == UNKNOWN
    assert classify({"to_club": "Real Madrid", "from_club": "Liverpool"},
                    {"status": "moved", "joined_club": "Liverpool FC"})[0] == UNKNOWN


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
