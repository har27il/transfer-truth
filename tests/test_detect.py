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


# --- TODOS 0a: evidence must describe a move inside THIS window -----------------------
# 2026-09-27: 31 of 60 queued auto-collapses cited only pre-2026 years. Deal 418 (Harvey
# Elliott -> Valencia, 2026-summer) was "collapsed" because Wikipedia says he joined Aston
# Villa -- on 1 September 2025, a season earlier. The guard only ever turns a verdict into
# UNKNOWN, never the reverse: positive-evidence-only gets stricter, not looser.

ELLIOTT = {"player": "Harvey Elliott", "from_club": "Liverpool", "to_club": "Valencia",
           "window": "2026-summer"}


def _moved(joined, move_date):
    return {"status": "moved", "joined_club": joined, "move_date": move_date,
            "window_closed": True, "evidence": "..."}


def test_a_move_from_last_season_cannot_settle_this_windows_rumour():
    got, why = classify(ELLIOTT, _moved("Aston Villa", "2025-09-01"))
    assert got == UNKNOWN and "outside" in why
    assert classify(ELLIOTT, _moved("Aston Villa", "2025"))[0] == UNKNOWN   # year only


def test_an_in_window_move_still_resolves():
    assert classify(ELLIOTT, _moved("Aston Villa", "2026-08-30"))[0] == COLLAPSED
    assert classify(ELLIOTT, _moved("Valencia", "2026-07-02"))[0] == COMPLETED
    assert classify(ELLIOTT, _moved("Valencia", "2026-07"))[0] == COMPLETED     # month only


def test_an_early_announced_deal_is_not_voided():
    """Summer deals are often agreed in May and registered when the window opens."""
    assert classify(ELLIOTT, _moved("Valencia", "2026-05-20"))[0] == COMPLETED


def test_no_or_unreadable_move_date_leaves_the_old_behaviour():
    for md in (None, "", "sometime", "2026-13-45"):
        assert classify(ELLIOTT, _moved("Valencia", md))[0] == COMPLETED
    assert classify({**ELLIOTT, "window": "1999-summer"},
                    _moved("Valencia", "2025-09-01"))[0] == COMPLETED        # unknown window


def test_the_resolver_parser_keeps_move_date():
    from outcome.source import _parse_json_object
    r = _parse_json_object('{"status":"moved","joined_club":"Aston Villa",'
                           '"move_date":"2025-09-01","evidence":"On 1 September 2025 ..."}')
    assert r["move_date"] == "2025-09-01"
    assert _parse_json_object('{"status":"stayed","joined_club":null,"evidence":""}')["move_date"] is None


def test_with_no_move_date_the_evidence_years_decide():
    """The resolver may omit move_date; its own evidence quote still dates the move."""
    stale = {"status": "moved", "joined_club": "Aston Villa", "window_closed": True,
             "evidence": "On 1 September 2025, Elliott joined Aston Villa on loan."}
    assert classify(ELLIOTT, stale)[0] == UNKNOWN
    mixed = {**stale, "joined_club": "Valencia",
             "evidence": "Signed from Fulham in 2019; on 1 September 2026 he joined Valencia."}
    assert classify(ELLIOTT, mixed)[0] == COMPLETED          # the LATEST year counts
    undated = {**stale, "evidence": "He joined Aston Villa on loan."}
    assert classify(ELLIOTT, undated)[0] == COLLAPSED        # no date anywhere: unchanged


# --- which window a claim belongs to (TODOS 0d) ----------------------------------------
from datetime import date as _d
from outcome.detect import window_for


def test_a_claim_belongs_to_the_window_it_was_made_in_or_before():
    assert window_for(_d(2026, 7, 10)) == "2026-summer"      # during
    assert window_for(_d(2026, 5, 20)) == "2026-summer"      # "X to Arsenal this summer"
    assert window_for(_d(2027, 1, 15)) == "2027-winter"


def test_the_weeks_after_a_deadline_still_belong_to_it():
    """Other leagues (Turkey, Saudi Arabia) buy into mid-September, and deadline-day deals
    are announced days later -- keying those to January would orphan them."""
    assert window_for(_d(2026, 9, 2)) == "2026-summer"
    assert window_for(_d(2026, 9, 22)) == "2026-summer"


def test_a_rumour_months_after_the_deadline_is_about_the_next_window():
    assert window_for(_d(2026, 10, 15)) == "2027-winter"     # was filed under the closed summer


def test_past_the_calendar_there_is_no_guess():
    assert window_for(_d(2027, 6, 1)) is None
    assert window_for(None) is None


# --- second fresh-context review (2026-09-27): resolver + window findings ------------------

def test_an_unreadable_move_date_counts_as_no_date_so_the_evidence_still_decides():
    """Review #2: "null", "August 2025", "2025/09/01"... used to skip the evidence-year
    check entirely, so a 2025 move still collapsed a 2026 rumour."""
    stale_ev = "On 1 September 2025, Elliott joined Aston Villa on loan."
    for md in ("null", "None", "unknown", "August 2025", "2025-09-01T00:00:00",
               "2025/09/01", "2025-9"):
        res = {"status": "moved", "joined_club": "Aston Villa", "move_date": md,
               "window_closed": True, "evidence": stale_ev}
        assert classify(ELLIOTT, res)[0] == UNKNOWN, md


def test_common_date_shapes_are_read_not_ignored():
    for md in ("2026-08-30T00:00:00", "2026/08/30", "2026-8"):
        res = {"status": "moved", "joined_club": "Valencia", "move_date": md,
               "window_closed": True, "evidence": ""}
        assert classify(ELLIOTT, res)[0] == COMPLETED, md


def test_october_moves_are_not_a_dead_zone():
    """Review #4: window_for files 2 Oct - 1 Nov under the winter window, so the winter
    window must accept moves from 2 October (a free-agent signing), and a summer move
    must not complete a winter deal (review #3)."""
    winter = {**ELLIOTT, "window": "2027-winter"}
    assert classify(winter, _moved("Valencia", "2026-10-10"))[0] == COMPLETED
    assert classify(winter, _moved("Valencia", "2026-08-30"))[0] == UNKNOWN


def test_the_move_date_used_is_recorded_so_the_recheck_agrees():
    """Review #1: apply classified WITH move_date but only saved reason + evidence; the
    Gate C re-check classified WITHOUT it, disagreed, re-opened, and apply collapsed it
    again -- every run. The verdict now records the move_date it rested on."""
    from outcome.apply import _record
    row = {"deal_id": "1", "player": "Harvey Elliott", "from_club": "Liverpool",
           "to_club": "Valencia", "window": "2026-summer", "outcome": "unknown"}
    res = {"status": "moved", "joined_club": "Aston Villa", "move_date": "2026-08-30",
           "window_closed": True, "evidence": "on loan at Benfica in 2024-25"}
    _record(row, res, [], 1, 1)
    assert row["outcome"] == "collapsed" and "(move_date 2026-08-30)" in row["notes"]
    joined, rumoured = collapse_facts(row["notes"])
    assert (joined, rumoured) == ("Aston Villa", "Valencia")     # reason still parses
    from ingest.bridge import recorded_resolution
    assert classify(row, recorded_resolution(row, joined))[0] == COLLAPSED   # re-check agrees
