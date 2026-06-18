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
    # NOTE: deliberately NO bare "milan" alias — it is ambiguous between AC Milan
    # and Inter Milan, and guessing would risk a false outcome.
}

# Generic tokens that don't distinguish a club; stripped only in the fallback match.
_GENERIC = {"fc", "afc", "cf", "sc", "bc", "ssc", "ac"}
# ...except "ac" stays meaningful for "AC Milan", so it is NOT stripped (see below).
_STRIPPABLE = {"fc", "afc", "cf", "sc", "bc", "ssc"}


def _norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canon(s):
    n = _norm(s)
    return _ALIASES.get(n, n)


def same_club(a, b):
    """True iff a and b refer to the same club. Conservative: when unsure, False."""
    if not a or not b:
        return False
    ca, cb = _canon(a), _canon(b)
    if ca == cb:
        return True
    # Fallback: ignore purely generic suffix tokens (FC/AFC/CF...) and compare.
    ta = [t for t in ca.split() if t not in _STRIPPABLE]
    tb = [t for t in cb.split() if t not in _STRIPPABLE]
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
