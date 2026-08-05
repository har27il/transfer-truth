"""Render-layer tests for the two live-audit findings fixed in PR1:

  - the Cooling-off section's own count label must never contradict the
    nameplate's "N cooling" stat by silently truncating without saying so
    (found live: header said "48 cooling", the section rendered only 8 rows
    with no indication anything was hidden).
  - a source with no resolved leaderboard score must never render as a bare
    name with nothing after it (found live: "Keith Downie" with no percent,
    next to four other sources that all had one).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import build_feed


def _cooling_row(player, pct, days_quiet, stage="talks"):
    return {"player": player, "to_club": "Club Y", "percent": pct,
            "days_quiet": days_quiet, "latest_stage": stage}


def test_cooling_section_count_label_matches_when_under_cap():
    rows = [_cooling_row(f"Player {i}", 15, i + 4) for i in range(5)]
    out = build_feed._cooling_section(rows)
    assert "5 of" not in out            # nothing hidden, no need to call it out
    assert "no movement in" in out


def test_cooling_section_count_label_says_so_when_truncated():
    rows = [_cooling_row(f"Player {i}", 15, i + 4) for i in range(48)]
    out = build_feed._cooling_section(rows)
    assert "8 of 48 shown" in out        # the exact live-site mismatch, now stated not hidden
    assert out.count('class="row cold"') == 8   # still only renders the capped set


def test_cooling_section_respects_custom_cap():
    rows = [_cooling_row(f"Player {i}", 15, i + 4) for i in range(20)]
    out = build_feed._cooling_section(rows, cap=3)
    assert "3 of 20 shown" in out
    assert out.count('class="row cold"') == 3


def _meter_row(sources, player="Bruno Guimaraes", to_club="Arsenal"):
    return {"player": player, "to_club": to_club, "percent": 48, "label": "Contested",
            "color": "yellow", "spread": 0.5, "n_sources": len(sources), "sources": sources}


def test_lead_shows_percent_for_scored_sources():
    m = _meter_row(["Sky Sports"])
    out = build_feed._lead(m, {"Sky Sports": 0.91})
    assert "Sky Sports</b> 91%" in out


def test_lead_falls_back_gracefully_for_unscored_source():
    """The exact live bug: a source absent from the leaderboard rendered as a bare
    name. Must now say plainly that it isn't scored yet, not go silent."""
    m = _meter_row(["Keith Downie"])
    out = build_feed._lead(m, {})   # empty reliability dict == not in leaderboard.json yet
    assert "Keith Downie</b> &middot; not yet scored" in out


def test_lead_mixed_scored_and_unscored_sources():
    m = _meter_row(["Sky Sports", "Keith Downie"])
    out = build_feed._lead(m, {"Sky Sports": 0.91})
    assert "Sky Sports</b> 91%" in out
    assert "Keith Downie</b> &middot; not yet scored" in out
