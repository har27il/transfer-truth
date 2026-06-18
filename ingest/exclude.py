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

# Women's football: the explicit word (also covers "women's") + major competitions.
# Precision-first, same as above: a men's transfer headline doesn't carry these.
_WOMEN = re.compile(
    r"\b(?:"
    r"women|wsl|nwsl|women's super league|"
    r"f[ée]minines?|femenin[oa]|frauen|liga f"
    r")\b", re.I)


def is_non_player(text):
    """Return (excluded: bool, reason: str).

    Empty/None text is NOT excluded -- we can't judge it, so let the normal
    extractor decide rather than drop on no evidence."""
    if not text:
        return False, ""
    if _MANAGER.search(text):
        return True, "manager/coaching appointment (not a player transfer)"
    if _WOMEN.search(text):
        return True, "women's football (out of scope for the player feed)"
    return False, ""
