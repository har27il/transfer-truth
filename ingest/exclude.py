#!/usr/bin/env python3
"""
Exclusion filter: keep NON-player-transfer items out of the deals pipeline.

The site ranks PLAYER transfer rumours. Two kinds of football news look like a
transfer to the extractor but are not a player moving between clubs:

  - MANAGER / coaching appointments  (Derek McInnes -> Rangers is a *manager* job)
  - WOMEN's football transfers       (out of scope for the current player feed)

Either would otherwise become a proposed deal and pollute the feed / leaderboard.
This is a PURE, exhaustively-testable predicate over the raw post text
(title + summary). It runs in TWO places (one shared rule, no drift):

  - ingest.pipeline, BEFORE the NIM extraction call  -> excluded posts cost 0 tokens
  - ingest.bridge, when turning a cluster into a deal -> catches anything already
    in the store (e.g. a manager claim cached from before this filter existed)

PRECISION OVER RECALL, deliberately. A false positive here silently drops a real
player transfer and -- because the pipeline marks the post seen -- it is never
re-examined, so the drop is unrecoverable. The keyword sets are therefore
high-signal and word-boundaried. Recall is best-effort: women's transfer
headlines frequently read identically to men's ("Earps joins PSG") with no
distinguishing token, so this catches the EXPLICIT cases (the word "Women",
"WSL", role nouns) and will miss the unmarked ones. It is a coarse guard, not a
guarantee. See tests/test_exclude.py -- the false-positive cases are the gate.
"""
import re
import unicodedata

# Category reasons, defined once -- both the keyword-regex branches and the
# _KNOWN_NON_PLAYERS name-backstop return these, so the two detection paths can never
# drift apart (they did once: "other sport" vs "...leaked from a mixed feed").
REASON_MANAGER = "manager/coaching appointment (not a player transfer)"
REASON_WOMEN = "women's football (out of scope for the player feed)"
REASON_SPORT = "not football (other sport leaked from a mixed feed)"

# Confirmed non-targets the TEXT filter cannot catch from a headline alone, keyed by
# normalized name -> reason. Two structurally-identical-to-a-real-transfer cases:
#
#   MANAGERS — "Derek McInnes to Rangers" extracts exactly like a player move, and not
#     every appointment headline says "manager"/"appoint" ("McInnes leaves Hearts for
#     Rangers" names no role).
#   WOMEN'S PLAYERS — "Beth Mead to Manchester City" reads identically to a men's
#     transfer: no "Women"/"WSL" token, and on the Guardian it sits under the same
#     /football/ URL as the men's game, so neither text- nor URL-section filtering
#     catches it. The only reliable signal left is the name itself.
#
# This is a precision-first BACKSTOP (an exact-name match only ever removes that one
# person, never a man), checked both by is_known_non_player(name) at bridge/feed time
# and scanned in the raw text by is_non_player() so a known name is dropped
# pre-extraction (saving a NIM token) even when no keyword token is present.
#
# The women's list is a deliberately TEMPORARY bridge. WS3 adds a model-extracted
# `competition_gender` field that makes hand-maintaining this roster obsolete --
# delete the women's entries once that ships. Keys are pre-normalized (_norm_name).
_MANAGER_NAMES = {
    "derek mcinnes",   # Rangers manager, not a player (surfaced 2026-summer)
    "craig bellamy",   # Wales/Burnley manager rumours leaked as a "deal" (2026-07)
    "jonathan morgan", # women's-football manager (Sheffield United) — appointment, not transfer
    "wouter vrancken", # Hearts head coach appointment (2026-06)
    # Manager, not a player: retired from playing in 2017. Surfaced on the LIVE feed
    # as "Xabi Alonso -> Chelsea, interest, 15%" (deals.csv id 272) off the headline
    # "Alonso heals Real Madrid scars to lead Chelsea's senior revolution" -- a
    # manager-tenure story with no appointment VERB, so _MANAGER's regex could not
    # reach it and only the name backstop catches it. The entry rests solely on him
    # being a retired player now in management (which club he manages is irrelevant
    # here and deliberately not asserted); no active player shares the name, so the
    # exact-name match is false-positive safe.
    "xabi alonso",
    # deal 226: the evidence says it outright -- "Iraola signed as HEAD COACH of
    # Liverpool on a two-year contract". Extracted as a player transfer to Liverpool.
    "andoni iraola",
}
# Top WSL / NWSL / international names whose transfers read like men's. Seeded broad
# on purpose: an exact-name match is FP-safe, so a longer list just catches more.
_WOMEN_PLAYER_NAMES = {
    # confirmed leaks observed in the live feed
    "beth mead", "mary earps", "mapi leon", "caroline weir", "katie mccabe",
    # England / Lionesses
    "leah williamson", "lucy bronze", "alessia russo", "lauren james",
    "lauren hemp", "ella toone", "georgia stanway", "chloe kelly",
    "millie bright", "fran kirby", "keira walsh", "alex greenwood",
    "hannah hampton", "jess carter", "niamh charles", "kirsty hanson",
    "nadine riesen", "manaka matsukubo", "victoria pelova", "lia walti",
    "amalie vangsgaard", "khiara keating", "aggie beever jones",
    # 2026-07-15 promotion review: WSL deals proposed by the resolver
    "selina cerci", "geraldine reuteler", "ona batlle", "lisa baum",
    "millie turner", "julia bartel", "ebony salmon",
    # 2026-08-07: these four sat in deals.csv as FALSE collapses -- the resolver had
    # quoted Wikipedia's "Women's Super League club Chelsea" against a rumoured
    # "Chelsea" and called it a different club. Fixing that alias would otherwise have
    # re-opened them, resolved them completed, and made four women's deals promotable
    # onto a leaderboard the project deliberately keeps to men's football. Denylisting
    # is the mechanism that makes the row deletion stick (bridge scrubs auto rows).
    "melvine malard", "estelle cascarino", "laia codina", "giulia dragoni",
    # 2026-08-07 promotion review: seven more sitting in the promotion queue, each
    # identified by the resolver's OWN evidence ("Women's Super League club X",
    # "WSL side X", Gotham FC/NWSL). Promoting them would have put women's deals on
    # a men's-football leaderboard. Eleven leaks in one day means the name denylist
    # is a bridge, not the fix -- the durable answer is the `competition_gender`
    # field in TODOS' WS3, which retires this list entirely.
    "denise o sullivan", "jade rastocle", "lexi lloyd smith", "mathilde harviken",
    "mao itamura", "alice sombath", "ashleigh neville",
    # deal 170: no WSL token in the evidence, only the pronoun ("She signed for
    # the club"). England international, Chelsea/Man Utd/Everton women.
    "hannah blundell",
    # 2026-08-07, second sweep of the promotion queue. Each provable from the
    # resolver's OWN evidence, not from outside knowledge: "her contract with
    # Leicester expired" (188), "Liga F side Barcelona" (221), "her former academy
    # team" (235).
    "hannah cain", "kerolin", "sophie baggaley",
    # Europe
    "aitana bonmati", "alexia putellas", "caroline graham hansen",
    "vivianne miedema", "pernille harder", "fridolina rolfo", "ada hegerberg",
    "lena oberdorf", "lea schuller", "klara buhl", "giulia gwinn", "guro reiten",
    # NWSL / rest of world
    "sam kerr", "sophia smith", "trinity rodman", "naomi girma", "mallory swanson",
    "lindsey horan", "khadija shaw", "marta", "debinha", "mary fowler",
}
# Non-football athletes already cached in the store from the old mixed Sky feed (they
# extract with no sport token in the bare name, so the _NON_FOOTBALL text regex can't
# reach them at feed-render time -- only an exact-name backstop drops the stored row).
# The feed swap (sources.py: 12040 -> 12691) stops NEW ones; these clear the residue.
_OTHER_SPORT_NAMES = {
    "feldt",   # rugby league (St Helens) -- surfaced at 99% on the live football feed
    "ellie kildunne",   # rugby union (Harlequins/PWR) -- leaked via BBC/Sky mixed sport copy
}
_KNOWN_NON_PLAYERS = {
    **{n: REASON_MANAGER for n in _MANAGER_NAMES},
    **{n: REASON_WOMEN for n in _WOMEN_PLAYER_NAMES},
    **{n: REASON_SPORT for n in _OTHER_SPORT_NAMES},
}

# Names SAFE to substring-scan in raw post text (is_non_player). A headline naming a
# women's player or a non-football athlete is, in practice, ABOUT that person.
#
# MANAGERS ARE DELIBERATELY EXCLUDED FROM THE TEXT SCAN. A current manager's name is
# everywhere in genuine transfer copy about OTHER players -- "Chelsea interested in
# signing Xhaka to reunite midfielder with Xabi Alonso", "Gittens keen to impress
# 'world-class' Alonso" are both real claims in ground-truth/journalist_claims.csv
# about Xhaka and Gittens. Substring-scanning "xabi alonso" would drop those genuine
# deals PERMANENTLY (the pipeline marks posts seen, so a dropped post is never
# re-examined). This is the same trap the _MANAGER regex documents for the bare word
# "manager" -- role words and manager names alike appear in real player transfer copy.
#
# Managers are therefore matched ONLY by is_known_non_player(extracted_player_name),
# an exact match on the player the extractor actually pulled out -- which fires on
# "Xabi Alonso -> Chelsea" and leaves "Xhaka -> Chelsea" untouched.
_TEXT_SCAN_NAMES = {
    **{n: REASON_WOMEN for n in _WOMEN_PLAYER_NAMES},
    **{n: REASON_SPORT for n in _OTHER_SPORT_NAMES},
}


def _norm_name(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def is_known_non_player(name):
    """True if `name` is a curated, confirmed non-target (manager or women's player)
    the text filter can't reliably catch. Normalized, accent-insensitive match."""
    return _norm_name(name) in _KNOWN_NON_PLAYERS


# Manager / coaching staff. The signal is an APPOINTMENT, not the bare word
# "manager" -- that word is everywhere in real transfer copy ("Arsenal manager
# Arteta wants X", "Liverpool boss confirms interest"), and matching it would drop
# genuine player deals for good. So we require an appointment/dismissal VERB next to
# a role noun, or an unambiguous appointment phrase ("as manager", "caretaker",
# "manager job", "sporting director"). Recall is sacrificed for precision on purpose.
_ROLE = r"(?:manager|head coach|boss|gaffer)"
_MANAGER = re.compile(
    r"\b(?:"
    # appoint/name/hire/unveil/sack/axe ... <up to 4 words> ... <role>
    r"(?:appoint|appoints|appointed|appointment|name|names|named|hire|hires|hired|"
    r"unveil|unveils|unveiled|sack|sacks|sacked|axe|axed) (?:[a-z.'\-]+ ){0,4}" + _ROLE + r"|"
    r"as " + _ROLE + r"|"
    r"(?:interim|caretaker) " + _ROLE + r"|caretaker|"
    + _ROLE + r" (?:job|role|vacancy|hunt|search|hot[- ]?seat)|"
    r"manager(?:ial|less)|"
    r"sporting director|director of football|technical director|"
    r"(?:steps? down|stepped down|resign|resigns|resigned|dismissed|sacked) as|"
    r"in the dugout|takes charge of"
    r")\b", re.I)

# Women's football: the explicit word (also covers "women's") + competitions + the
# women's-only club tokens that carry no "Women" word ("OL Lyonnes", "London City
# Lionesses", "Barcelona Femení"). Precision-first: a men's transfer headline doesn't
# carry these. Unmarked names (e.g. "Beth Mead to Man City") are caught by the
# _KNOWN_NON_PLAYERS name-scan below instead.
_WOMEN = re.compile(
    r"\b(?:"
    r"women|wsl|nwsl|women's super league|lionesses|lyonnes|"
    r"f[ée]minines?|f[ée]minin|femen[ií]n?[oa]?|frauen|liga f"
    r")\b", re.I)

# Other sports leaking from a mixed multi-sport feed (the real fix is the football-only
# Sky feed in sources.py; this is cheap defense-in-depth against any future mixed feed).
# ZERO-COLLISION word list only -- every token here is impossible in genuine football
# transfer copy. Deliberately EXCLUDED as FP-prone: bare "rugby" ("Rugby Town" FC),
# "boxing" ("Boxing Day" fixtures), "wimbledon" (AFC Wimbledon), "golf"/"tennis"
# (used adjectivally). Precision over recall, same rule as the manager regex.
_NON_FOOTBALL = re.compile(
    r"\b(?:"
    r"cricket|rugby league|rugby union|darts|snooker|nascar|moto\s?gp|"
    r"baseball|the ashes|grand prix|ice hockey|nfl|nba|wnba"
    r")\b", re.I)


def is_non_player(text):
    """Return (excluded: bool, reason: str).

    Empty/None text is NOT excluded -- we can't judge it, so let the normal
    extractor decide rather than drop on no evidence."""
    if not text:
        return False, ""
    if _MANAGER.search(text):
        return True, REASON_MANAGER
    if _WOMEN.search(text):
        return True, REASON_WOMEN
    if _NON_FOOTBALL.search(text):
        return True, REASON_SPORT
    # Name backstop: a denylisted women's player / non-football athlete named in the text
    # but carrying no keyword token (the Beth Mead case). Word-boundary match on normalized
    # text so a multi-word name can't partial-match a longer token. Managers are NOT scanned
    # here on purpose -- see _TEXT_SCAN_NAMES for why (their names appear in genuine copy
    # about other players).
    norm = f" {_norm_name(text)} "
    for name, reason in _TEXT_SCAN_NAMES.items():
        if f" {name} " in norm:
            return True, reason
    return False, ""
