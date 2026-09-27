#!/usr/bin/env python3
"""
Outcome detection — decide completed / collapsed / unknown for a rumoured deal.

PURE logic. No network, no API keys, no file I/O. Given a deal row
(player, from_club, to_club, window) and a RESOLUTION of what the player actually
did in that window, classify the rumour. This is the unit you can test exhaustively
and that the whole product's integrity rests on, so it lives apart from the
network/LLM source (source.py).

D-SAFETY (critical, from the plan review): we return 'completed' or 'collapsed'
ONLY on positive evidence. Anything ambiguous stays 'unknown' and is never scored.
Wrongly auto-labelling an outcome poisons the ground truth — so when in doubt we
refuse to decide.

A RESOLUTION is a dict:
    {
      "status": "moved" | "stayed" | "unclear",   # what the player actually did
      "joined_club": "<full club name>" | None,    # required when status == moved
      "window_closed": true,                        # is the transfer window over?
      "evidence": "<short quote/fact backing it>",
    }
"""

import re
import unicodedata
from datetime import date, timedelta

COMPLETED, COLLAPSED, UNKNOWN = "completed", "collapsed", "unknown"

# Nickname / variant -> canonical club name. Keys are compared AFTER _norm(), so
# write them in normalized (lower, no punctuation) form. Only add an alias when it
# is unambiguous — a wrong alias here can flip a real outcome.
_ALIASES = {
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "man utd": "manchester united",
    "man united": "manchester united",
    "manchester utd": "manchester united",
    "man city": "manchester city",
    "newcastle": "newcastle united",
    "bayern": "bayern munich",
    "fc bayern": "bayern munich",
    "fc bayern munich": "bayern munich",
    "barca": "barcelona",
    "fc barcelona": "barcelona",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "wolves": "wolverhampton wanderers",
    "madrid": "real madrid",
    "athletic": "athletic club",
    "athletic bilbao": "athletic club",
    "juve": "juventus",
    "bvb": "borussia dortmund",
    "dortmund": "borussia dortmund",
    # Short forms the RESOLVER kept meeting in the wild while the rumour text used
    # the long form (or vice versa), each one a FALSE collapse that scored a
    # journalist wrong for a call they got right. Measured 2026-08-07 against
    # deals.csv: 7 of the 15 re-openable auto-collapses were this bug.
    "leeds": "leeds united",                    # deal 70,  Harry Wilson
    "west ham": "west ham united",              # deals 192, 231
    "hearts": "heart of midlothian",            # deal 205, Laurent Mendy
    "brighton": "brighton hove albion",         # TODOS 0b's named case, deal 89
    # NOTE: deliberately NO bare "milan" alias — it is ambiguous between AC Milan
    # and Inter Milan, and guessing would risk a false outcome.
}

# Competition qualifiers the resolver's LLM prepends to a club name when it is
# quoting Wikipedia prose: "Premier League side Bournemouth", "Women's Super League
# club Chelsea". The club is identical; only the descriptor differs, so a bare
# string compare collapses a deal that actually completed (deals 174, 251, 271).
#
# Conservative by construction: a competition token (league/liga/serie a/...) must
# appear BEFORE the club|side|team|outfit connector, so "Athletic Club" and
# "Club Brugge" — which have no competition word — are left alone.
_COMP_PREFIX = re.compile(
    r"^[a-z0-9 ]*?\b(?:league|liga|serie a|bundesliga|ligue \d|premiership"
    r"|championship|eredivisie|mls|wsl)\b[a-z0-9 ]*?\b(?:club|side|team|outfit)\s+"
)

# Same rule, applied to the ORIGINAL string instead of the normalized one, so the
# feed can print "joined Bournemouth instead" rather than leaking the LLM's
# "joined Premier League side Bournemouth instead" into the page. Kept beside
# _COMP_PREFIX so the two are edited together.
_COMP_PREFIX_RAW = re.compile(
    r"^[\w' ]*?\b(?:League|Liga|Serie A|Bundesliga|Ligue \d|Premiership"
    r"|Championship|Eredivisie|MLS|WSL)\b[\w' ]*?\b(?:club|side|team|outfit)\s+",
    re.IGNORECASE,
)


def display_club(name):
    """Club name with any competition qualifier removed, original casing preserved.

    Presentation only -- never use this for matching (that is `same_club`, which
    normalizes). Falls back to the input if stripping would leave nothing.
    """
    if not name:
        return ""
    return _COMP_PREFIX_RAW.sub("", name, count=1).strip() or name.strip()

# Generic tokens that don't distinguish a club; stripped only in the fallback match.
_GENERIC = {"fc", "afc", "cf", "sc", "bc", "ssc", "ac"}
# ...except "ac" stays meaningful for "AC Milan", so it is NOT stripped (see below).
# "rsc" added 2026-08-07: deal 253 collapsed on Anderlecht vs RSC Anderlecht, the
# same club -- another journalist scored wrong for a call they got right.
_STRIPPABLE = {"fc", "afc", "cf", "sc", "bc", "ssc", "rsc"}


def _is_year_suffix(tok):
    """German clubs carry a founding year in the name -- Paderborn 07, Hannover 96,
    Schalke 04, 1899 Hoffenheim. No two clubs are distinguished ONLY by that number,
    so it never carries identity and must not block a match."""
    return tok.isdigit()


def _norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canon(s):
    n = _COMP_PREFIX.sub("", _norm(s), count=1)
    return _ALIASES.get(n, n)


def same_club(a, b):
    """True iff a and b refer to the same club. Conservative: when unsure, False."""
    if not a or not b:
        return False
    ca, cb = _canon(a), _canon(b)
    if ca == cb:
        return True
    # Fallback: ignore purely generic suffix tokens (FC/AFC/CF...) and founding-year
    # numbers, then compare.
    ta = [t for t in ca.split() if t not in _STRIPPABLE and not _is_year_suffix(t)]
    tb = [t for t in cb.split() if t not in _STRIPPABLE and not _is_year_suffix(t)]
    return bool(ta) and ta == tb


# Tokens that don't distinguish WHICH club (many clubs share them).
_NONDISTINCT = _STRIPPABLE | {"united", "city", "club", "real", "athletic", "sporting"}


def club_token_in_text(club, text):
    """True if a distinctive token of `club` appears in `text` (both normalized).

    Used to sanity-check we fetched the RIGHT player's page before trusting an
    extraction: a player's own page virtually always names their selling club, so
    if it's absent we've likely hit a same-name collision or a disambiguation page.
    Conservative: a too-generic club name (only nondistinct tokens) returns False
    so the caller falls back to 'unclear' rather than trusting a weak match."""
    if not club or not text:
        return False
    nt = _norm(text)
    toks = [t for t in _canon(club).split() if len(t) >= 4 and t not in _NONDISTINCT]
    return any(re.search(r"\b" + re.escape(t) + r"\b", nt) for t in toks)


# Window bounds for the evidence check (TODOS 0a), with slack: summer deals are often
# agreed in April/May and registered when the window opens, and a late registration can
# trail the deadline. The slack still rejects the observed failure -- evidence from the
# PREVIOUS season. Dates mirror site/windows.py (2026-summer opened 15 Jun, closed 1 Sep).
# A window missing here gets no check (the old behaviour), never a guess.
WINDOW_BOUNDS = {
    "2025-summer": (date(2025, 6, 1), date(2025, 9, 2)),
    "2026-summer": (date(2026, 6, 15), date(2026, 9, 1)),
    "2027-winter": (date(2027, 1, 1), date(2027, 2, 1)),
}
_SLACK_BEFORE, _SLACK_AFTER = timedelta(days=60), timedelta(days=30)


_DEADLINE_GRACE = timedelta(days=30)   # = _SLACK_AFTER: other leagues buy into mid-September


def window_for(day):
    """The transfer window a claim dated `day` belongs to (TODOS 0d): the first window
    that has not closed more than _DEADLINE_GRACE before it. So a May rumour belongs to
    the coming summer, a 22 September story still to the summer just shut, and an October
    rumour to January. None past the end of the calendar -- extend WINDOW_BOUNDS (and
    site/windows.py) as each season's dates are confirmed; never guess a window."""
    if day is None:
        return None
    for key, (_opens, closes) in sorted(WINDOW_BOUNDS.items(), key=lambda kv: kv[1][0]):
        if day <= closes + _DEADLINE_GRACE:
            return key
    return None


def _date_span(value):
    """'YYYY-MM-DD' -> that day; 'YYYY-MM' -> that month; 'YYYY' -> that year; else None."""
    s = (value or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            d = date.fromisoformat(s)
            return d, d
        if re.fullmatch(r"\d{4}-\d{2}", s):
            y, m = map(int, s.split("-"))
            start = date(y, m, 1)
            return start, (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        if re.fullmatch(r"\d{4}", s):
            return date(int(s), 1, 1), date(int(s), 12, 31)
    except ValueError:
        return None
    return None


def outside_window(move_date, window):
    """True only when the move is CERTAINLY outside the window (+ slack). Anything
    unknown -- no date, an unreadable date, an untracked window -- is False."""
    bounds, span = WINDOW_BOUNDS.get((window or "").strip()), _date_span(move_date)
    if not bounds or not span:
        return False
    lo, hi = bounds[0] - _SLACK_BEFORE, bounds[1] + _SLACK_AFTER
    return span[1] < lo or span[0] > hi


def _evidence_year(resolution, window):
    """The latest year the resolver's own evidence quotes, IGNORING years after the
    window: those are contract ends ("a deal until 2032"), not the move. "signed in 2019;
    joined Valencia on 1 September 2026" -> "2026". None when nothing usable is dated."""
    bounds = WINDOW_BOUNDS.get((window or "").strip())
    if not bounds:
        return None
    years = [int(y) for y in re.findall(r"\b((?:19|20)\d\d)\b", resolution.get("evidence") or "")
             if int(y) <= bounds[1].year]
    return str(max(years)) if years else None


def classify(deal, resolution):
    """Return (outcome, reason). outcome in {completed, collapsed, unknown}.

    deal: dict with at least 'to_club' and 'from_club'.
    resolution: dict as documented in the module docstring.
    """
    to_club = deal.get("to_club", "")
    from_club = deal.get("from_club", "")
    status = (resolution.get("status") or "unclear").lower()
    joined = resolution.get("joined_club")
    window_closed = resolution.get("window_closed", True)

    if status == "moved":
        if not joined:
            # claims a move but names no club -> not positive evidence
            return UNKNOWN, "status=moved but no destination club given"
        window = deal.get("window")
        md = resolution.get("move_date")
        if outside_window(md, window):
            # TODOS 0a: the move the evidence describes happened in ANOTHER window (deal
            # 418: a Sept-2025 loan "settling" a 2026 rumour). It says nothing about this
            # rumour, so refuse to decide rather than grade a journalist on it.
            return UNKNOWN, f"evidence describes a move dated {md}, outside the {window} window"
        if not md and not same_club(joined, to_club):
            # No move_date: on the COLLAPSE path only, date it by the evidence's own
            # years. That path is where the stale verdicts were (3 of 4 re-checked on
            # 2026-09-27 had in fact completed). A completion is left alone: its stale
            # quote can sit beside real proof (deal 114, verified from BBC/Sky reports).
            ey = _evidence_year(resolution, window)
            if outside_window(ey, window):
                return UNKNOWN, (f"evidence describes a move dated {ey}, "
                                 f"outside the {window} window")
        if same_club(joined, from_club):
            # 'moved' but the named club IS the origin = a renewal/stay, not a transfer.
            # Refuse to decide rather than mislabel (D-safety: when in doubt, UNKNOWN).
            return UNKNOWN, f"reported moved but joined {joined} = origin club {from_club}"
        if not (to_club or "").strip():
            # The rumour named NO destination -- a pure DEPARTURE claim ("X to leave
            # from_club"). The player demonstrably left (joined another club), so the
            # rumour came true: positive evidence of a completed exit.
            #
            #   WITHOUT this branch, same_club(joined, "") is False and a real
            #   departure fell through to COLLAPSED below -- the Ibrahima Konate bug
            #   (signed for Real Madrid, but to_club was blank, so we said "did not
            #   happen" and the feed rendered "stayed put").
            return COMPLETED, f"player left {from_club}, joined {joined} (no destination was rumoured)"
        if same_club(joined, to_club):
            return COMPLETED, f"player joined {joined} - the rumoured destination"
        return COLLAPSED, f"player joined {joined}, not {to_club} - rumour did not happen"

    if status == "stayed":
        if window_closed:
            return COLLAPSED, (f"window closed; player stayed at {from_club} - "
                               f"move to {to_club} did not happen")
        return UNKNOWN, "player still at origin but window is still open"

    return UNKNOWN, "insufficient positive evidence to resolve"


# Mirror of the reason string built in classify()'s joined-elsewhere branch above.
# Kept in THIS file, next to its producer: if that f-string is ever reworded, the
# round-trip test here fails immediately instead of the feed quietly degrading to
# its fallback label and the re-open gate quietly ceasing to fire.
_COLLAPSE_RE = re.compile(
    r"player joined (?P<joined>.+?), not (?P<rumoured>.+?) - rumour did not happen")


def collapse_facts(notes):
    """(joined_club, rumoured_club) from a MACHINE-written collapse reason, else (None, None).

    Two callers, both of which need to know what a collapse actually asserted:
      - the feed, so a deal can say "joined Real Madrid instead" rather than the
        flatly untrue "stayed put";
      - the bridge's re-open gate, to prove a collapse verdict rested on the
        destination that has since been revised.

    Curated free-text notes ("Spurs walked away over the valuation...") deliberately
    do NOT parse — a hand note is prose, not a contract, and guessing at it is how
    you invent a fact. Callers fall back when this returns (None, None).
    """
    m = _COLLAPSE_RE.search(notes or "")
    if not m:
        return None, None
    return m.group("joined").strip(), m.group("rumoured").strip()


if __name__ == "__main__":
    # tiny smoke demo
    demos = [
        ({"to_club": "Arsenal", "from_club": "Crystal Palace"},
         {"status": "moved", "joined_club": "Arsenal"}),
        ({"to_club": "Tottenham Hotspur", "from_club": "Crystal Palace"},
         {"status": "moved", "joined_club": "Arsenal"}),
        ({"to_club": "Liverpool", "from_club": "Crystal Palace"},
         {"status": "stayed", "joined_club": None}),
        ({"to_club": "Bayern Munich", "from_club": "VfB Stuttgart"},
         {"status": "unclear", "joined_club": None}),
    ]
    for d, r in demos:
        print(classify(d, r), "<-", d["to_club"], r["status"], r.get("joined_club"))
