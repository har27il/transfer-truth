"""Exclusion-filter tests — keep managers + women's football out of the player feed.

The FALSE-POSITIVE cases are the real gate: this filter runs pre-extraction and
marks the post seen, so dropping a genuine player transfer is unrecoverable.
Recall is best-effort and we assert the known gap honestly (an unmarked women's
transfer reads like a men's one and is NOT caught).
"""
from ingest.exclude import is_non_player


# --- managers / coaching staff: MUST be excluded ---------------------------------
def test_manager_appointments_excluded():
    headlines = [
        "Rangers appoint Derek McInnes as manager",          # the bug that started this
        "Tottenham name new head coach ahead of the window",
        "Club legend returns to the dugout as caretaker boss",
        "Bayern sack manager after poor run",
        "Forest appoint new sporting director",
        "Interim manager takes charge for the derby",
    ]
    for h in headlines:
        excluded, reason = is_non_player(h)
        assert excluded, f"manager headline not excluded: {h!r}"
        assert "manager" in reason or "coaching" in reason


# --- women's football: explicit cases MUST be excluded ---------------------------
def test_womens_football_explicit_excluded():
    headlines = [
        "Arsenal Women sign striker from Chelsea",
        "WSL champions complete double swoop",
        "NWSL side land USWNT defender",
        "Lyon Féminines confirm marquee arrival",
    ]
    for h in headlines:
        excluded, reason = is_non_player(h)
        assert excluded, f"women's headline not excluded: {h!r}"
        assert "women" in reason


# --- the gate: real PLAYER transfers MUST NOT be excluded ------------------------
def test_player_transfers_not_excluded():
    headlines = [
        "Isak to Liverpool, here we go!",
        "Arsenal sign Viktor Gyokeres from Sporting for £55m",
        "Ibrahima Konaté signs for Real Madrid on a four-year contract",
        "Medical booked: Mbappé completes move to Real Madrid",
        "Done deal: striker joins on a free transfer",
        "Defender agrees personal terms ahead of switch",
        # the regression that started this fix: real transfers routinely NAME the
        # manager/boss. Bare-word matching dropped all of these.
        "Arsenal manager Mikel Arteta wants Martín Zubimendi this summer",
        "Liverpool boss confirms interest in the Newcastle striker",
        "Pep Guardiola's side eye a new midfielder as the window opens",
        "Manager keen: United push to wrap up the defender",
        "Real Madrid head coach happy with the squad but wants one signing",
    ]
    for h in headlines:
        excluded, reason = is_non_player(h)
        assert not excluded, f"FALSE POSITIVE — real transfer dropped: {h!r} ({reason})"


def test_empty_text_is_not_excluded():
    # No evidence to judge on -> let the extractor decide, don't drop.
    assert is_non_player("")[0] is False
    assert is_non_player(None)[0] is False


def test_known_recall_gap_is_documented():
    """Honest limitation: an unmarked women's transfer reads exactly like a men's
    one (no 'Women'/'WSL'/role token), so a keyword filter CANNOT catch it. This
    asserts the gap on purpose — if a future change makes it catchable, revisit."""
    excluded, _ = is_non_player("Earps joins Paris Saint-Germain")
    assert excluded is False  # documents the miss, not an endorsement of it
