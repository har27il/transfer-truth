#!/usr/bin/env python3
"""
Retrospective stories are history, not rumours -- and a rumour that contradicts a
player's verified current club is held for review, not published.

THE BUG THIS FIXES (live 2026-09-24). BBC Sport ran "I was close to joining Liverpool
in 2016 - Mbappe": Mbappé, now at Real Madrid, looking back at a near-move from his
Monaco days. The extractor's stage table maps "close" -> advanced (p=0.60), nothing
asked WHEN the move nearly happened, and the claim was dated by the article's RSS
publish date -- so a decade-old near-miss LED the live feed as
"Kylian Mbappe -> Liverpool, 60% Possible" (deal 458 / claim 995).

Two guards, both PURE so every caller applies one rule (the exclude.py pattern):

  is_retrospective(text)   deterministic backstop to the prompt's PAST EVENTS rule.
      Runs pre-extraction in ingest.pipeline (the post costs 0 tokens and never becomes
      a claim) and again at READ time in the feed and the bridge -- which is the only
      thing that clears a claim already cached in ingest.db, because backfill only
      re-reads posts that produced NO claim.

  needs_review(claims, club)   the sanity check. A deal whose reporting contradicts the
      player's VERIFIED current club, with no fresh forward-looking source consistent
      with it, is moved to "under review" instead of being published as live.

PRECISION OVER RECALL, same posture as ingest.exclude: the pre-extraction drop is
permanent (the post stays marked seen), so a false positive silently loses a real
rumour. A story counts as retrospective only when:
  - a past NEAR-MISS phrase ("was close to", "almost joined", "revealed talks") is
    followed, in the SAME clause, by a time well in the past ("in 2016", "years ago",
    "as a teenager"), or a sentence opens on that time ("In 2016, ... nearly signed");
  - and nothing in the text says the story is live NOW ("could", "have reignited their
    interest", "is wanted by", "back in for").
The same clause matters: "Liverpool were close to signing X, who joined Chelsea in 2022,
last week" is a current near-miss that merely mentions an old move. Anything subtler --
"Mbappé was close to Liverpool" with no date at all -- is left to the prompt.
"""
import re
from datetime import date

from ground_truth import trusted
from ingest import cluster
from outcome.detect import same_club

HISTORICAL = "historical"   # the deal outcome AND the post disposition for a retrospective story
OLD_YEAR_GAP = 2            # a year this many years before today (or earlier) is history
REVIEW_FRESH_DAYS = 30      # a source this recent, consistent with the ledger, keeps a deal live

_Q = "['\"‘’“”]?"   # optional quote mark: he was 'close' to joining

# A move that nearly happened, told after the fact.
_NEAR_MISS = (
    rf"(?:was|were|came|come|been|got)\s+(?:so\s+|very\s+|really\s+|this\s+|that\s+|extremely\s+)?"
    rf"{_Q}(?:close|near){_Q}\s+to"
    rf"|how\s+{_Q}close{_Q}\s+(?:he|she|they|i|we)\s+came\s+to"
    r"|(?:almost|nearly)\s+(?:joined|signed|moved|went|became|swapped|completed)"
    r"|(?:could|would|should|might)\s+have\s+(?:joined|signed|moved|gone|played)"
    r"|(?:held|had|revealed|recalled|admitted|opened)\s+(?:secret\s+|private\s+|advanced\s+|early\s+)?talks"
)
_MONTHS = ("summer|winter|spring|autumn|january|february|march|april|may|june|july|"
           "august|september|october|november|december")
# A time well in the past. `year` is checked against OLD_YEAR_GAP; the rest are always past.
_ANCHOR = (
    rf"(?:(?:back\s+)?(?:in|during)\s+(?:the\s+)?(?:(?:{_MONTHS})\s+(?:of\s+)?)?(?P<year>(?:19|20)\d\d)\b"
    r"|years\s+(?:ago|earlier)\b|a\s+decade\s+(?:ago|earlier)\b|decades\s+ago\b"
    r"|as\s+an?\s+(?:teenager|teen|youngster|kid|boy|child|schoolboy)\b"
    r"|(?:early|earlier)\s+in\s+(?:his|her)\s+career\b)"
)
# near-miss, then the past time in the same clause (no , . ; ! ? between them)
_MISS_THEN_TIME = re.compile(rf"\b(?:{_NEAR_MISS})\b[^.,;!?]{{0,60}}?\b{_ANCHOR}", re.I)
# ...or a sentence that opens on the past time: "In 2016, Liverpool almost signed him"
_TIME_THEN_MISS = re.compile(rf"^\s*{_ANCHOR}\s*,?.{{0,80}}?\b(?:{_NEAR_MISS})\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\s+[-–—|]\s+|\n+")

# Anything that says the story is live NOW rescues it. Deliberately NOT here: recency
# words ("this week", "yesterday") -- a retrospective interview is published recently
# too ("told The Athletic this week he was close to joining Liverpool in 2016").
_LIVE = re.compile(
    r"\b(?:"
    r"could(?!\s+(?:have|never|not)\b)|will(?!\s+(?:never|always|not)\b)"
    r"|next\s+(?:week|month|summer|window|season|january|year)"
    r"|(?:are|is|remain|remains)\s+(?:still\s+|now\s+|also\s+)?(?:keen|interested|monitoring|"
    r"tracking|targeting|eyeing|chasing|pursuing|considering|plotting|preparing|weighing|"
    r"exploring|wanted|linked|in\s+(?:advanced\s+)?talks|closing\s+in|set\s+to|poised\s+to|"
    r"ready\s+to|expected\s+to|hoping\s+to|looking\s+to|planning\s+to|in\s+the\s+race|"
    r"leading\s+the\s+race|favourites|frontrunners)"
    r"|(?:have|has)\s+(?:now\s+|also\s+|already\s+)?(?:reignited|revived|renewed|rekindled|"
    r"reopened|resumed|opened|made|submitted|lodged|launched|tabled|agreed|stepped\s+up)"
    r"|(?:lead|leads|leading)\s+the\s+race|back\s+in\s+for|again"
    r"|fresh\s+(?:bid|approach|interest|talks|offer|move)|renewed\s+interest"
    r"|second\s+(?:bid|approach|attempt|offer)"
    r")\b", re.I)
# "Arsenal want him" is live; "I didn't want to leave my country" (Mbappé, looking back) is not.
_WANTS = re.compile(r"(?<!n't )(?<!not )\bwants?\b", re.I)


def _past_time(match, today):
    year = match.group("year")
    return year is None or int(year) <= today.year - OLD_YEAR_GAP


def is_retrospective(text, today=None):
    """True if `text` (a post's title + summary) looks back at a move that nearly
    happened long ago, rather than reporting one that could still happen.

    Empty/None text is NOT retrospective -- no evidence, so let the extractor decide."""
    if not text:
        return False
    today = today or date.today()
    if _LIVE.search(text) or _WANTS.search(text):
        return False
    for sentence in _SENTENCE.split(text):
        if any(_past_time(m, today) for m in _MISS_THEN_TIME.finditer(sentence)):
            return True
        m = _TIME_THEN_MISS.search(sentence)
        if m and _past_time(m, today):
            return True
    return False


# --- current-club sanity check --------------------------------------------------------

def verified_current_clubs(deal_rows):
    """{normalized player name: to_club of his newest TRUSTED completed move}.

    Positive evidence only -- the same gate as scoring (ground_truth.trusted): a
    verified=YES completed row. Never an auto proposal, never a collapse. A player with
    no such row has no entry, so nothing reported about him can be 'contradicted'."""
    best = {}
    for r in deal_rows:
        club = (r.get("to_club") or "").strip()
        name = cluster.normalize_name(r.get("player"))
        if trusted(r) != "completed" or not club or not name:
            continue
        when = ((r.get("outcome_date") or "").strip(), (r.get("window") or "").strip())
        if name not in best or when > best[name][0]:
            best[name] = (when, club)
    return {name: club for name, (_when, club) in best.items()}


# Prefix/suffix tokens that name no particular club ("VfB Stuttgart" is "Stuttgart").
# NOT "ac"/"inter": "AC Milan" and "Inter Milan" are different clubs (detect.py refuses a
# bare "milan" alias for the same reason).
_CLUB_NOISE = {"fc", "afc", "cf", "sc", "bc", "ssc", "rsc", "cd", "ud", "sv", "vfb", "vfl",
               "club", "de", "the"}


def _maybe_same_club(a, b):
    """same_club, widened: one name's distinctive tokens contained in the other's
    ("Stuttgart" / "VfB Stuttgart"). For the review check ONLY -- where "maybe the same
    club" must mean "no contradiction" -- never for outcome matching, which stays strict."""
    if same_club(a, b):
        return True
    ta, tb = ({t for t in cluster.normalize_name(x).split() if t not in _CLUB_NOISE
               and not t.isdigit()} for x in (a, b))
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def contradicts_current_club(claim, current_club):
    """A claim contradicts the verified current club when it rumours a move TO the club
    he already plays for, or places him AT a club he has since left.

    Both tests lean towards "no contradiction", so only a CLEAR one flags: the TO side
    needs a certain match (same_club), the FROM side a certain mismatch."""
    if same_club(claim.get("to_club"), current_club):
        return True
    frm = claim.get("from_club")
    return bool(frm) and not _maybe_same_club(frm, current_club)


def _claim_date(claim):
    try:
        return date.fromisoformat((claim.get("claim_date") or "").strip()[:10])
    except ValueError:
        return None


def needs_review(claims, current_club, today=None, texts=None):
    """True when a deal's reporting contradicts the player's verified current club and
    no fresh (<= REVIEW_FRESH_DAYS), forward-looking source CONSISTENT with that club
    backs it.

    A contradicting report is not rescued by its own publish date: the Mbappé post was
    dated 22 Sep 2026 and described 2016. Only a different, consistent, recent source
    clears it. texts: {post_url: post text}; when given, a retrospective story never
    counts as that forward-looking source."""
    if not current_club or not claims:
        return False
    today = today or date.today()
    contradicted = backed = False
    for c in claims:
        if contradicts_current_club(c, current_club):
            contradicted = True
            continue
        d = _claim_date(c)
        if d is None or (today - d).days > REVIEW_FRESH_DAYS:
            continue
        if texts is not None and is_retrospective(texts.get(c.get("post_url"), ""), today):
            continue
        backed = True
    return contradicted and not backed
