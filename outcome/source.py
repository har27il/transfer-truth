#!/usr/bin/env python3
"""
Outcome data source — resolve what a player ACTUALLY did in a transfer window.

Two stages, deliberately separated so the risky decision logic (detect.py) stays
testable without network or API keys:

  1. fetch_player_text(player)            -> plain Wikipedia career prose
                                             (stdlib urllib, no API key)
  2. extract_resolution(text, player, w)  -> {status, joined_club, evidence}
                                             via an LLM (default: NVIDIA NIM)

resolve(player, window) chains them. Both stages are injectable, so tests run the
whole pipeline against fixtures with zero network/keys:
    resolve(p, w, fetch=fake_fetch, extract=fake_extract)

PROVIDER: extraction defaults to NVIDIA NIM, which is OpenAI-compatible and has a
free tier (get a key at https://build.nvidia.com, set NVIDIA_API_KEY). The model
and endpoint are env-overridable, so swapping providers never touches detect.py.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WIKI_API = "https://en.wikipedia.org/w/api.php"
NIM_BASE = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
# Model default lives in nim_models.py (shared with the engine so the two can
# never silently drift apart again — June 25 post-mortem). Resolver-only
# override: NIM_RESOLVER_MODEL. The model lists previously kept here as
# fallback suggestions were STALE (a listed model 404'd on this account) —
# verify availability at build.nvidia.com before relying on any candidate.
from nim_models import RESOLVER_MODEL as NIM_MODEL
USER_AGENT = "TransferMarket/0.1 (outcome-detection; research; contact via repo)"

# When a window's deadline has passed, "player stayed" becomes positive evidence
# of a collapse. Add windows here as they are tracked.
WINDOW_CLOSE = {
    "2025-summer": date(2025, 9, 2),
    "2026-summer": date(2026, 9, 1),
}


def window_is_closed(window, today=None):
    today = today or date.today()
    close = WINDOW_CLOSE.get((window or "").strip())
    return close is not None and today > close


# Politeness throttle: a batch resolve hits Wikipedia from a shared CI IP, which
# gets rate-limited (HTTP 429) fast. Space requests out so we stay a good citizen.
_MIN_INTERVAL = float(os.environ.get("WIKI_MIN_INTERVAL", "0.6"))
_last_fetch = [0.0]


def _raw_fetch(title, timeout=20, retries=3):
    """One Wikipedia extract fetch for an exact title, or '' on miss/error.

    Resilient by design: a network blip or rate-limit (HTTP 429) must never crash a
    batch resolve. We throttle, retry transient errors with backoff, and on persistent
    failure return '' -- which resolve() treats as 'unclear', so a fetch failure can
    never fabricate an outcome."""
    q = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": 1,
        "redirects": 1, "format": "json", "titles": title,
    })
    req = urllib.request.Request(WIKI_API + "?" + q, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        wait = _MIN_INTERVAL - (time.monotonic() - _last_fetch[0])
        if wait > 0:
            time.sleep(wait)
        _last_fetch[0] = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)  # back off 1s, 2s, ... on transient errors
                continue
            return ""
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return ""
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return ""
        page = next(iter(pages.values()))
        return page.get("extract", "") or ""
    return ""  # all retries exhausted


# Opening phrases of a Wikipedia disambiguation / name-list page. We must NOT feed
# these people-lists to the LLM: they name several players' clubs, so the LLM can't
# tell which one moved AND the selling-club sanity guard passes (every club, including
# the right one, appears on the list). "may refer to" is the classic disambig opener;
# the "given name"/"surname"/"notable people" variants are name-list pages that DON'T
# say "may refer to" -- the Éderson case (two Brazilian Édersons) slipped through on
# exactly this gap and never resolved. Keep this list broad: a false "disambig" just
# triggers the (safe) footballer-candidate search below.
_DISAMBIG_MARKERS = (
    "may refer to", "can refer to",
    "is a given name", "is a masculine given name", "is a feminine given name",
    "is a unisex given name", "is a surname", "is a brazilian given name",
    "notable people with the name", "notable people with this name",
)


def _looks_like_disambig(text):
    """True if the extract is a disambiguation / name-list page rather than one
    person's article (see _DISAMBIG_MARKERS). Checked on the opening, where the
    signature phrase always appears."""
    head = (text or "")[:400].lower()
    return bool(text) and any(m in head for m in _DISAMBIG_MARKERS)


def _footballer_titles(player, disambig_text):
    """Candidate article titles for the FOOTBALLER(s) on a disambiguation / name-list
    extract. A name-list reads e.g. 'Ederson (footballer, born January 1986), Brazilian
    midfielder ... Ederson (footballer, born August 1993), goalkeeper ...' -- often
    several on ONE line (the API collapses the bullet list), so we scan the WHOLE text,
    not line-by-line, and return EVERY '<name> (footballer...)' title. Order preserved;
    a generic '<player> (footballer)' fallback is appended last."""
    titles = []
    for m in re.finditer(r"([A-ZÀ-Þ][\w.'\- ]+?\s*\(footballer[^)]*\))", disambig_text):
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        if t not in titles:
            titles.append(t)
    fallback = f"{player} (footballer)"
    if fallback not in titles:
        titles.append(fallback)
    return titles


def fetch_player_text(player, timeout=20, retries=3, prefer_club=None):
    """Plain-text Wikipedia extract for a player, or '' on miss/error/disambiguation.

    If the bare name lands on a disambiguation / name-list page, follow it to the
    footballer's article. When `prefer_club` is given (the player's selling club),
    PREFER the candidate article that mentions it -- this is what tells two same-name
    players apart (e.g. Éderson at Atalanta vs Éderson the Man City goalkeeper). A
    direct hit that does NOT mention prefer_club also triggers the candidate search,
    since the bare name can redirect to the wrong same-name player. Falls back to the
    first valid footballer article when no candidate names the club, and to '' (treated
    as 'unclear') when none is found -- never let a name collision fabricate an outcome."""
    from outcome.detect import club_token_in_text
    text = _raw_fetch(player, timeout, retries)
    if not _looks_like_disambig(text):
        if not prefer_club or club_token_in_text(prefer_club, text):
            return text  # unambiguous, or the one article already names the club
    fallback = ""
    for title in _footballer_titles(player, text):
        alt = _raw_fetch(title, timeout, retries)
        if not alt or _looks_like_disambig(alt):
            continue
        if not prefer_club or club_token_in_text(prefer_club, alt):
            return alt                 # the right same-name player
        fallback = fallback or alt     # a valid footballer page, but not the club we want
    return fallback  # best-effort; resolve()'s own from_club guard still gates the write


_SYS = ("You verify football (soccer) transfers from an encyclopedia extract. "
        "Report only what the text states; never guess. Output strict JSON only.")


def _build_prompt(text, player, window):
    return (
        f"Player: {player}\n"
        f"Transfer window: {window} (treat 'summer 2025' as ~1 June to 2 September 2025).\n\n"
        "Using ONLY the text below, determine what this player ACTUALLY did in THIS "
        "window. Ignore moves in any other window (e.g. January 2026).\n\n"
        "Return JSON with exactly these keys:\n"
        '  "status": "moved" | "stayed" | "unclear"\n'
        '  "joined_club": "<full club name if status=moved, else null>"\n'
        '  "evidence": "<one short quote or fact from the text, with a date if present>"\n\n'
        "Definitions:\n"
        "- moved: the text clearly says he transferred to and signed for a club DURING this window.\n"
        "- stayed: the text clearly says he remained / a move fell through / he signed a new "
        "contract with his existing club during this window.\n"
        "- unclear: the text does not clearly establish either. When unsure, choose unclear.\n\n"
        f"--- TEXT ---\n{text[:12000]}"
    )


def _parse_json_object(raw):
    """Lenient: reuse the engine's hardened parser (strips <think> blocks,
    balanced-brace fallback) so a reasoning model's transcript parses instead of
    degrading every resolution to 'unclear'. On any failure still return an
    'unclear' resolution so a bad LLM response can never fabricate an outcome."""
    from engine.run import parse_engine_json
    obj = parse_engine_json(raw or "")
    if not isinstance(obj, dict):
        return {"status": "unclear", "joined_club": None,
                "evidence": "could not parse model output"}
    status = str(obj.get("status", "unclear")).strip().lower()
    if status not in ("moved", "stayed", "unclear"):
        status = "unclear"
    joined = obj.get("joined_club")
    if isinstance(joined, str):
        joined = joined.strip() or None
        if joined and joined.lower() in ("null", "none", "n/a"):
            joined = None
    else:
        joined = None
    return {"status": status, "joined_club": joined,
            "evidence": str(obj.get("evidence", ""))[:300]}


def extract_resolution(text, player, window, model=None):
    """LLM extraction via NVIDIA NIM (OpenAI-compatible). Requires NVIDIA_API_KEY."""
    from openai import OpenAI  # local import: not needed for offline/fixture tests
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(
            "NVIDIA_API_KEY not set. Get a free key at https://build.nvidia.com and "
            "export NVIDIA_API_KEY=... (or inject a fake `extract` in tests).")
    client = OpenAI(base_url=NIM_BASE, api_key=key)
    resp = client.chat.completions.create(
        model=model or NIM_MODEL,
        messages=[{"role": "system", "content": _SYS},
                  {"role": "user", "content": _build_prompt(text, player, window)}],
        temperature=0,
        # Same reasoning-headroom rule as engine/run.py: a <think> preamble must
        # not eat the budget before the JSON is emitted.
        max_tokens=int(os.environ.get("NIM_MAX_TOKENS", "2048")),
    )
    return _parse_json_object(resp.choices[0].message.content or "")


def resolve(player, window, from_club=None, fetch=None, extract=extract_resolution):
    """Resolve a player's real outcome for a window. Returns a detect.py resolution.

    `from_club` does double duty: (1) it steers disambiguation -- the default fetch
    prefers the same-name article that mentions the selling club (Éderson@Atalanta, not
    the Man City keeper); (2) the fetched page must still mention it before we trust any
    extraction, catching collisions and name-list pages that would otherwise auto-write
    a confident but wrong outcome. An injected `fetch` (tests) is called as fetch(player)
    and bypasses the prefer_club steering -- fixtures already return the right page."""
    from outcome.detect import club_token_in_text
    closed = window_is_closed(window)
    text = fetch(player) if fetch is not None else fetch_player_text(player, prefer_club=from_club)
    if not text:
        return {"status": "unclear", "joined_club": None,
                "evidence": "no Wikipedia text found", "window_closed": closed}
    if from_club and not club_token_in_text(from_club, text):
        return {"status": "unclear", "joined_club": None,
                "evidence": f"page sanity check failed: '{from_club}' not found on the "
                            f"fetched page for {player} (possible wrong/ambiguous page)",
                "window_closed": closed}
    res = extract(text, player, window)
    res.setdefault("window_closed", closed)
    return res


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "Nick Woltemade"
    win = sys.argv[2] if len(sys.argv) > 2 else "2025-summer"
    print(json.dumps(resolve(name, win), indent=2))
