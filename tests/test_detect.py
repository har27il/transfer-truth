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

from outcome.detect import (classify, same_club, club_token_in_text, collapse_facts,
                            display_club,
                            COMPLETED, COLLAPSED, UNKNOWN)

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
    # Count history: 38 hand-labelled originals + 20 promoted 2026-07-05 (first
    # promotion wave) + 16 from the 2026-07-05 web census (6 corrected/promoted +
    # 10 coverage gaps) + 16 promoted 2026-07-15 (Touré wave + 15-deal review) = 90.
    #
    # This was `assert n == 90`, which made every promotion wave a two-step change:
    # promote, then hand-bump this line. Miss the bump and `pytest -q` goes red --
    # and because it is a HARD gate in update-site.yml, that kills ingest, resolve,
    # rebuild AND commit, freezing the whole site over a stale constant.
    #
    # A floor is the assertion that actually carries weight: the trusted set may only
    # grow. Shrinking means a curated row lost verified=YES or was deleted, which is
    # the real corruption this guards. Coverage is already enforced above -- every
    # scored deal must have a hand fixture (see the assert in the loop), so a
    # promotion still cannot land without its review evidence.
    assert n >= 90, (f"verified-deal count went DOWN: scored {n}, floor is 90. "
                     "A curated (verified=YES) row was deleted or demoted.")


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


def test_same_club_matches_the_short_long_pairs_that_caused_false_collapses():
    """Each pair below produced a real FALSE collapse in deals.csv (measured
    2026-08-07): the journalist named the club correctly, the resolver read the
    other form off Wikipedia, and same_club said 'did not happen'."""
    assert same_club("Leeds", "Leeds United")                    # deal 70, Harry Wilson
    assert same_club("West Ham", "West Ham United")              # deals 192, 231
    assert same_club("Hearts", "Heart of Midlothian")            # deal 205, Laurent Mendy
    assert same_club("Brighton", "Brighton & Hove Albion")       # TODOS 0b, deal 89


def test_same_club_ignores_a_competition_qualifier():
    """The resolver quotes Wikipedia prose, which prefixes the competition."""
    assert same_club("Bournemouth", "Premier League side Bournemouth")      # deal 271
    assert same_club("Chelsea", "Women's Super League club Chelsea")        # deal 251
    assert same_club("Chelsea", "English Women's Super League club Chelsea")  # deal 174
    # the qualifier is stripped, not the identity: a real hijack still collapses
    assert not same_club("Fiorentina", "Premier League club Bournemouth")   # deal 161


def test_competition_stripping_leaves_club_named_teams_alone():
    """'club'/'side' only counts as a qualifier when a COMPETITION word precedes it,
    so teams whose real name contains 'Club' survive intact."""
    assert same_club("Athletic Club", "Athletic Bilbao")
    assert same_club("Club Brugge", "Club Brugge")
    assert not same_club("Athletic Club", "Club Brugge")
    # and the ambiguity guard still refuses to guess
    assert not same_club("Milan", "Inter Milan")


def test_alias_miss_no_longer_collapses_a_move_that_happened():
    """Deal 70 (Harry Wilson -> Leeds) shape: reported correctly, resolved to
    'Leeds United', scored as a failed rumour. Positive evidence says COMPLETED."""
    deal = {"to_club": "Leeds", "from_club": "Fulham"}
    outcome, _reason = classify(deal, {"status": "moved", "joined_club": "Leeds United"})
    assert outcome == COMPLETED


def test_collapse_facts_round_trips_the_classifier_own_reason():
    """Parser and producer must not drift: feed classify()'s output straight back in."""
    deal = {"to_club": "Paris Saint-Germain", "from_club": "RB Leipzig"}
    outcome, reason = classify(deal, {"status": "moved", "joined_club": "Real Madrid"})
    assert outcome == COLLAPSED
    joined, rumoured = collapse_facts(f"[auto] {reason} | On 6 August 2026, ...")
    assert (joined, rumoured) == ("Real Madrid", "Paris Saint-Germain")


def test_display_club_strips_the_qualifier_but_keeps_the_casing():
    """Presentation only: the feed must print 'joined Bournemouth instead', not the
    LLM's 'joined Premier League side Bournemouth instead'."""
    assert display_club("Premier League side Bournemouth") == "Bournemouth"
    assert display_club("English Women's Super League club Chelsea") == "Chelsea"
    # untouched when there is no qualifier, including clubs whose NAME contains "Club"
    for name in ("Real Madrid", "Athletic Club", "Club Brugge", "Leeds United"):
        assert display_club(name) == name
    assert display_club("") == ""


def test_collapse_facts_refuses_curated_free_text():
    """A hand note is prose, not a contract — guessing at it is how you invent a fact."""
    assert collapse_facts("Spurs walked away over ~£65-70m valuation. Stayed at Bournemouth.") \
        == (None, None)
    assert collapse_facts("") == (None, None)
    assert collapse_facts(None) == (None, None)


def test_club_token_in_text_guards_wrong_page():
    palace_page = "Marc Guehi is an English footballer who plays for Crystal Palace."
    assert club_token_in_text("Crystal Palace", palace_page)
    assert club_token_in_text("VfB Stuttgart", "joined from VfB Stuttgart in 2025")
    # wrong page: a same-name player's page that never mentions the selling club
    assert not club_token_in_text("Crystal Palace", "He plays cricket for Surrey.")
    assert not club_token_in_text("Crystal Palace", "")
    # distinctive token must match the right club, not just a shared generic word
    assert not club_token_in_text("Manchester United", "He signed for Newcastle City fans.")
