"""Source-adapter tests. No network, no API key by default.

The LLM parse path and the resolve() chaining are tested with injected fakes, so a
malformed model response can never silently fabricate an outcome. A live Wikipedia
fetch test is included but skipped unless TM_NET_TESTS=1.
"""
import os
import threading
import time

import pytest

from outcome import source
from outcome.detect import classify, COMPLETED, COLLAPSED


# --- the Wikipedia politeness throttle --------------------------------------------
#
# NOTE: every other test in this file monkeypatches `_raw_fetch` away, so its
# internals (throttle, retry, backoff) had ZERO coverage until these. The lock lives
# inside that blind spot and is what stops the concurrent resolver from bursting
# straight into HTTP 429s.

def test_pace_serialises_concurrent_callers(monkeypatch):
    """N threads through _pace() must leave >= _MIN_INTERVAL between request starts.

    Asserts on RECORDED timestamps, not on wall-clock duration -- a loaded CI runner
    makes duration assertions flaky, but the ordering invariant holds regardless.
    Without the lock all threads read the same stale _last_fetch, every gap is ~0,
    and this fails.
    """
    interval = 0.05
    monkeypatch.setattr(source, "_MIN_INTERVAL", interval)
    monkeypatch.setattr(source, "_last_fetch", [0.0])
    monkeypatch.setattr(source, "_throttle_lock", threading.Lock())

    stamps, lock = [], threading.Lock()

    def call():
        source._pace()
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=call) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(stamps) == 6
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    # 20% tolerance: sleep() may wake marginally early, and the append happens just
    # after the lock is released so a gap can read slightly under the interval.
    assert all(g >= interval * 0.8 for g in gaps), f"throttle burst detected: {gaps}"


def test_retry_delay_is_jittered_not_lockstep():
    """Bare 2**attempt makes N concurrent workers retry in the SAME instant after a
    shared 429 -- they re-trip the limit together and the storm feeds itself. The
    delay must be spread, and must still sit in the exponential band."""
    for attempt in (0, 1, 2):
        lo, hi = 2 ** attempt, 2 ** attempt + 1
        vals = {source._retry_delay(attempt) for _ in range(50)}
        assert all(lo <= v <= hi for v in vals), f"attempt {attempt} out of band: {vals}"
        assert len(vals) > 40, f"attempt {attempt} not jittered, got {len(vals)} distinct"


def test_retry_delay_honours_retry_after_header():
    """A 429 usually names the exact wait. Guessing an exponential value when the
    server already answered is a slower, ruder way to get rate-limited again."""
    class _Err:
        def __init__(self, v): self.headers = {"Retry-After": v}

    assert source._retry_delay(0, _Err("7")) == 7.0
    assert source._retry_delay(2, _Err("3")) == 3.0        # header beats the exponent
    # A hostile / absurd value must not stall the batch past the step's time budget.
    assert source._retry_delay(0, _Err("99999")) == 30.0
    # Junk or absent header falls back to jittered exponential.
    assert 1.0 <= source._retry_delay(0, _Err("soon")) <= 2.0


def test_retry_delay_survives_error_without_headers():
    """Not every exception carries .headers -- URLError/TimeoutError don't. The delay
    helper must never raise inside a retry path whose whole job is not crashing."""
    class _Bare: pass
    assert 1.0 <= source._retry_delay(0, _Bare()) <= 2.0
    assert 1.0 <= source._retry_delay(0, None) <= 2.0


def test_raw_fetch_paces_every_attempt(monkeypatch):
    """_raw_fetch must call _pace() before EACH attempt, not just the first --
    otherwise a retry storm after a 429 ignores the throttle entirely."""
    calls = []
    monkeypatch.setattr(source, "_pace", lambda: calls.append(1))

    def boom(*a, **k):
        raise TimeoutError("network down")
    monkeypatch.setattr(source.urllib.request, "urlopen", boom)

    assert source._raw_fetch("Someone", retries=3) == ""   # exhausted -> '' = "unclear"
    assert len(calls) == 3, f"expected one pace per attempt, got {len(calls)}"


def test_parse_clean_json():
    r = source._parse_json_object('{"status":"moved","joined_club":"Arsenal","evidence":"signed 23 Aug"}')
    assert r["status"] == "moved" and r["joined_club"] == "Arsenal"


def test_parse_json_with_surrounding_prose():
    raw = 'Sure! Here you go:\n{"status":"stayed","joined_club":null,"evidence":"stayed"}\nHope that helps.'
    r = source._parse_json_object(raw)
    assert r["status"] == "stayed" and r["joined_club"] is None


def test_parse_garbage_falls_back_to_unclear():
    # A non-JSON or broken response must NEVER produce a confident outcome.
    for bad in ["totally not json", "{broken", "", "{\"status\": \"moved\""]:
        assert source._parse_json_object(bad)["status"] == "unclear"


def test_parse_normalizes_stringy_nulls():
    r = source._parse_json_object('{"status":"moved","joined_club":"none"}')
    assert r["joined_club"] is None
    r2 = source._parse_json_object('{"status":"weird","joined_club":"Arsenal"}')
    assert r2["status"] == "unclear"  # unknown status value is coerced safe


def test_resolve_chains_fetch_and_extract_offline():
    fake_text = "Eze completed a move to Arsenal on 23 August 2025."
    res = source.resolve(
        "Eberechi Eze", "2025-summer",
        fetch=lambda p: fake_text,
        extract=lambda t, p, w: {"status": "moved", "joined_club": "Arsenal", "evidence": t},
    )
    assert res["window_closed"] is True  # 2025-summer is closed
    assert classify({"to_club": "Arsenal", "from_club": "Crystal Palace"}, res)[0] == COMPLETED


def test_resolve_rejects_wrong_page_via_sanity_guard():
    # Fetched text never mentions the selling club -> likely wrong/ambiguous page.
    res = source.resolve(
        "Common Name", "2025-summer", from_club="Crystal Palace",
        fetch=lambda p: "Some other Common Name is a darts player from Essex.",
        extract=lambda *a: pytest.fail("extract must not run when the page fails sanity check"),
    )
    assert res["status"] == "unclear"
    assert "sanity check" in res["evidence"]


def test_resolve_passes_guard_when_club_present():
    res = source.resolve(
        "Marc Guehi", "2025-summer", from_club="Crystal Palace",
        fetch=lambda p: "Marc Guehi stayed at Crystal Palace after the deal collapsed.",
        extract=lambda t, p, w: {"status": "stayed", "joined_club": None},
    )
    assert res["status"] == "stayed"


def test_resolve_empty_text_is_unclear():
    res = source.resolve("Nobody", "2025-summer", fetch=lambda p: "",
                         extract=lambda *a: pytest.fail("extract should not run on empty text"))
    assert res["status"] == "unclear"


def test_window_is_closed():
    from datetime import date
    assert source.window_is_closed("2025-summer", today=date(2026, 1, 1)) is True
    assert source.window_is_closed("2025-summer", today=date(2025, 7, 1)) is False
    assert source.window_is_closed("unknown-window") is False


_DISAMBIG = ("Anthony Gordon may refer to:\n\n"
             "Tony Gordon (active 2007-2010), fictional character\n"
             "Anthony Gordon (footballer) (born 2001), English footballer with Newcastle United\n"
             "Anthony Gordon (American football) (born 1997), quarterback\n")


def test_looks_like_disambig():
    assert source._looks_like_disambig(_DISAMBIG) is True
    assert source._looks_like_disambig("Anthony Gordon is a footballer who plays for Newcastle.") is False
    assert source._looks_like_disambig("") is False


def test_footballer_titles_prefers_parsed_then_fallback():
    titles = source._footballer_titles("Anthony Gordon", _DISAMBIG)
    # the (footballer) entry is preferred over the American-football one, and a
    # generic fallback is always appended last
    assert titles[0] == "Anthony Gordon (footballer)"
    assert titles[-1] == "Anthony Gordon (footballer)" or "Anthony Gordon (footballer)" in titles


def test_fetch_follows_disambiguation_to_footballer(monkeypatch):
    # bare name -> disambig people-list; the footballer title -> the real article.
    real = "Anthony Gordon is an English footballer. On 29 May 2026 he joined Barcelona."
    pages = {"Anthony Gordon": _DISAMBIG, "Anthony Gordon (footballer)": real}
    monkeypatch.setattr(source, "_raw_fetch", lambda title, *a, **k: pages.get(title, ""))
    assert source.fetch_player_text("Anthony Gordon") == real


def test_fetch_returns_empty_when_disambiguation_unresolvable(monkeypatch):
    # No footballer article exists -> return '' (treated as unclear), never the
    # people-list, so a name collision can't fabricate an outcome.
    monkeypatch.setattr(source, "_raw_fetch",
                        lambda title, *a, **k: _DISAMBIG if title == "Ambiguous Name" else "")
    assert source.fetch_player_text("Ambiguous Name") == ""


# --- given-name disambiguation: the Éderson collision (two Brazilian Édersons) ------

# A real Wikipedia "given name" extract: a name-list that does NOT say "may refer to",
# packs several footballers on one blob, and names every one's club (so the selling-club
# guard passes on the WRONG page). This is what left Éderson @ Atalanta unresolved.
_GIVEN_NAME = (
    "Ederson is a given name. Notable people with the name include the following "
    "footballers: Ederson (footballer, born January 1986), Brazilian midfielder "
    "(Lyon, Lazio). Ederson (footballer, born August 1993), Brazilian goalkeeper "
    "(Manchester City). Ederson (footballer, born July 1999), Brazilian midfielder "
    "(Atalanta, Manchester United)."
)
_EDERSON_GK = "Ederson is a Brazilian goalkeeper who plays for Manchester City."
_EDERSON_MID = ("Ederson is a Brazilian midfielder. In June 2026 he joined Manchester "
                "United from Atalanta for a reported 35 million euros.")


def test_given_name_page_detected_as_disambig():
    # the bug: this format slipped the old '"may refer to"' check and was fed to the LLM.
    assert source._looks_like_disambig(_GIVEN_NAME) is True
    assert source._looks_like_disambig("Ederson is a Brazilian goalkeeper.") is False


def test_footballer_titles_extracts_all_candidates_from_one_blob():
    titles = source._footballer_titles("Ederson", _GIVEN_NAME)
    assert "Ederson (footballer, born August 1993)" in titles
    assert "Ederson (footballer, born July 1999)" in titles
    assert len([t for t in titles if "footballer" in t]) >= 3   # all three + fallback


def test_fetch_prefers_candidate_that_names_the_selling_club(monkeypatch):
    # prefer_club='Atalanta' must pick the midfielder, NOT the Man City keeper, even
    # though the keeper's page is a valid footballer article that lists first.
    pages = {
        "Ederson": _GIVEN_NAME,
        "Ederson (footballer, born August 1993)": _EDERSON_GK,
        "Ederson (footballer, born July 1999)": _EDERSON_MID,
    }
    monkeypatch.setattr(source, "_raw_fetch", lambda title, *a, **k: pages.get(title, ""))
    got = source.fetch_player_text("Ederson", prefer_club="Atalanta")
    assert "Atalanta" in got and "midfielder" in got
    assert got == _EDERSON_MID


def test_resolve_disambiguates_via_from_club_end_to_end(monkeypatch):
    # Full path: bare-name -> given-name list -> from_club steers to the right Éderson
    # -> COMPLETED. This is the regression for the 'stuck unknown' deal.
    pages = {
        "Ederson": _GIVEN_NAME,
        "Ederson (footballer, born August 1993)": _EDERSON_GK,
        "Ederson (footballer, born July 1999)": _EDERSON_MID,
    }
    monkeypatch.setattr(source, "_raw_fetch", lambda title, *a, **k: pages.get(title, ""))
    res = source.resolve("Ederson", "2026-summer", from_club="Atalanta",
                         extract=lambda t, p, w: {"status": "moved",
                                                  "joined_club": "Manchester United", "evidence": t})
    assert res["status"] == "moved" and res["joined_club"] == "Manchester United"
    assert classify({"to_club": "Manchester United", "from_club": "Atalanta"}, res)[0] == COMPLETED


@pytest.mark.skipif(os.environ.get("TM_NET_TESTS") != "1",
                    reason="set TM_NET_TESTS=1 to run live Wikipedia fetch")
def test_live_wikipedia_fetch():
    text = source.fetch_player_text("Nick Woltemade")
    assert "Newcastle" in text and len(text) > 500


@pytest.mark.skipif(os.environ.get("TM_NET_TESTS") != "1",
                    reason="set TM_NET_TESTS=1 to run live Wikipedia fetch")
def test_live_ederson_disambiguation():
    # the real collision, end to end against live Wikipedia. Assert the IDENTITY of the
    # returned article, not just that 'Atalanta' appears somewhere -- the original weak
    # assertion ('Atalanta' in text) also passed for the WRONG Éderson (a retired winger
    # whose page happens to mention Atalanta), giving false confidence.
    head = source.fetch_player_text("Éderson", prefer_club="Atalanta")[:400].lower()
    assert "atalanta" in head and "midfielder" in head   # the born-1999 Atalanta player
    assert "goalkeeper" not in head                       # NOT the Manchester City keeper


def test_parse_json_object_survives_reasoning_transcript():
    """REGRESSION (2026-07-05 model switch): the resolver's parser must survive a
    reasoning model's <think> preamble (stray braces included) instead of
    degrading every resolution to 'unclear' — it now reuses the engine parser."""
    from outcome.source import _parse_json_object
    raw = ('<think>Career section says {joined: Spurs} in 2026...</think>\n'
           '{"status": "moved", "joined_club": "Tottenham Hotspur", "evidence": "signed 2 July 2026"}')
    res = _parse_json_object(raw)
    assert res["status"] == "moved" and res["joined_club"] == "Tottenham Hotspur"
    assert _parse_json_object("no json here")["status"] == "unclear"
