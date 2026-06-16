"""Source-adapter tests. No network, no API key by default.

The LLM parse path and the resolve() chaining are tested with injected fakes, so a
malformed model response can never silently fabricate an outcome. A live Wikipedia
fetch test is included but skipped unless TM_NET_TESTS=1.
"""
import os

import pytest

from outcome import source
from outcome.detect import classify, COMPLETED, COLLAPSED


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


@pytest.mark.skipif(os.environ.get("TM_NET_TESTS") != "1",
                    reason="set TM_NET_TESTS=1 to run live Wikipedia fetch")
def test_live_wikipedia_fetch():
    text = source.fetch_player_text("Nick Woltemade")
    assert "Newcastle" in text and len(text) > 500
