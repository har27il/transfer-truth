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
import urllib.parse
import urllib.request
from datetime import date

WIKI_API = "https://en.wikipedia.org/w/api.php"
NIM_BASE = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL = os.environ.get("NIM_MODEL", "meta/llama-3.3-70b-instruct")
USER_AGENT = "TransferMarket/0.1 (outcome-detection; research; contact via repo)"

# When a window's deadline has passed, "player stayed" becomes positive evidence
# of a collapse. Add windows here as they are tracked.
WINDOW_CLOSE = {
    "2025-summer": date(2025, 9, 2),
}


def window_is_closed(window, today=None):
    today = today or date.today()
    close = WINDOW_CLOSE.get((window or "").strip())
    return close is not None and today > close


def fetch_player_text(player, timeout=20):
    """Return the plain-text Wikipedia extract for a player, or '' if not found."""
    q = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": 1,
        "redirects": 1, "format": "json", "titles": player,
    })
    req = urllib.request.Request(WIKI_API + "?" + q, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return ""
    page = next(iter(pages.values()))
    return page.get("extract", "") or ""


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
    """Lenient: pull the first {...} block and parse it. On any failure return an
    'unclear' resolution so a bad LLM response can never fabricate an outcome."""
    try:
        i, j = raw.index("{"), raw.rindex("}")
        obj = json.loads(raw[i:j + 1])
    except (ValueError, json.JSONDecodeError):
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
        max_tokens=400,
    )
    return _parse_json_object(resp.choices[0].message.content or "")


def resolve(player, window, from_club=None, fetch=fetch_player_text, extract=extract_resolution):
    """Resolve a player's real outcome for a window. Returns a detect.py resolution.

    If `from_club` is given, the fetched page must mention it (the player's own page
    names their selling club) before we trust any extraction — this catches same-name
    collisions and disambiguation pages, which would otherwise auto-write a confident
    but wrong outcome into the ground truth."""
    from outcome.detect import club_token_in_text
    closed = window_is_closed(window)
    text = fetch(player)
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
