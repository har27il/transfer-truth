"""Exclusion-filter tests — keep managers + women's football out of the player feed.

The FALSE-POSITIVE cases are the real gate: this filter runs pre-extraction and
marks the post seen, so dropping a genuine player transfer is unrecoverable.
Recall is best-effort and we assert the known gap honestly (an unmarked women's
transfer reads like a men's one and is NOT caught).
"""
from ingest.exclude import is_non_player, is_known_non_player


def test_known_non_player_denylist():
    """Backstop for confirmed non-targets the text filter can't catch from a headline:
    managers ('McInnes leaves Hearts for Rangers' names no role) AND women's players
    ('Beth Mead to Man City' carries no 'Women'/'WSL' token). Matched by name,
    accent/case-insensitive."""
    assert is_known_non_player("Derek McInnes")
    assert is_known_non_player("derek mcinnes")     # case-insensitive
    assert is_known_non_player("Beth Mead")         # women's player, no token in name
    assert is_known_non_player("Mapi León")         # accent-insensitive
    assert is_known_non_player("Feldt")             # rugby-league residue from old feed
    assert not is_known_non_player("Alexander Isak")
    assert not is_known_non_player("Éderson")        # legit men's deal must survive
    assert not is_known_non_player("")


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
        "London City Lionesses poised to sign Mary Earps",     # 'Lionesses' token
        "OL Lyonnes confirm Caroline Weir extension",          # 'Lyonnes' token
    ]
    for h in headlines:
        excluded, reason = is_non_player(h)
        assert excluded, f"women's headline not excluded: {h!r}"
        assert "women" in reason


# --- women's players with NO token (the Beth Mead leak): caught by name backstop ---
def test_womens_player_name_excluded_pre_extraction():
    """The headline reads exactly like a men's transfer (no 'Women'/'WSL' token), so
    only the name catches it. This must fire in is_non_player (pre-extraction), not
    just at bridge time, so it never costs a NIM token."""
    headlines = [
        "Beth Mead describes moving to Manchester City as a no-brainer",
        "Mary Earps joins Paris Saint-Germain",
        "Mapi León set for Chelsea switch",          # accented name in raw text
    ]
    for h in headlines:
        excluded, reason = is_non_player(h)
        assert excluded, f"women's-player headline not excluded: {h!r}"
        assert "women" in reason


# --- other sports leaking from a mixed feed: MUST be excluded ---------------------
def test_non_football_sports_excluded():
    headlines = [
        "Hull KR claim dominant win over Leigh Leopards in rugby league",
        "England predictably beaten at the Oval in the cricket",
        "Littler eases through at the darts",
        "Mercedes top the timesheets at the grand prix",
    ]
    for h in headlines:
        excluded, reason = is_non_player(h)
        assert excluded, f"non-football headline not excluded: {h!r}"
        assert "football" in reason


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
        # sport-word backstop must not eat real men's transfers that mention an
        # FP-prone token in a football context (the collisions we deliberately excluded).
        "Rugby Town's striker linked with a Premier League move",   # 'Rugby' the place
        "Everton eye a defender ahead of their Boxing Day clash",   # 'Boxing' Day
        "AFC Wimbledon complete the signing of a free-agent keeper",
    ]
    for h in headlines:
        excluded, reason = is_non_player(h)
        assert not excluded, f"FALSE POSITIVE — real transfer dropped: {h!r} ({reason})"


def test_empty_text_is_not_excluded():
    # No evidence to judge on -> let the extractor decide, don't drop.
    assert is_non_player("")[0] is False
    assert is_non_player(None)[0] is False


def test_known_recall_gap_is_documented():
    """Honest limitation: an unmarked women's transfer for a player NOT on the denylist
    reads exactly like a men's one (no 'Women'/'WSL'/role token), so the keyword+name
    filter still CANNOT catch it. This asserts the residual gap on purpose — WS3's
    model `competition_gender` field is what finally closes it. (A famous name like
    Earps IS now caught — see test_womens_player_name_excluded_pre_extraction.)"""
    excluded, _ = is_non_player("Bompastor's uncapped teenager joins on a free")
    assert excluded is False  # documents the miss, not an endorsement of it
