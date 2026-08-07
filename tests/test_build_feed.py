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


# --- "Done & dusted" rail: a collapse is not a stay -------------------------------

def _done_row(player, outcome, to_club="", notes=""):
    return {"player": player, "outcome": outcome, "to_club": to_club, "notes": notes}


def test_done_rail_names_the_club_the_player_actually_joined():
    """The live bug you could read off the page: deal 124 rendered
    'Yan Diomande stayed put' while its own notes said he signed for Real Madrid."""
    row = _done_row("Yan Diomande", "collapsed", "Paris Saint-Germain",
                    "[auto] player joined Real Madrid, not Paris Saint-Germain - "
                    "rumour did not happen | On 6 August 2026, Diomande returned to La Liga.")
    out = build_feed._done_rail([row])
    assert "joined Real Madrid instead" in out
    assert "stayed put" not in out


def test_done_rail_never_says_stayed_put_even_when_the_reason_is_unparseable():
    """Curated free-text notes don't parse. The fallback must still not assert a
    fact we cannot support -- 'stayed put' was wrong in every machine case."""
    row = _done_row("Marc Guehi", "collapsed", "Liverpool",
                    "Spurs walked away over ~£65-70m valuation.")
    out = build_feed._done_rail([row])
    assert "stayed put" not in out
    assert "happen" in out                      # "move didn't happen"


def test_done_rail_completed_row_is_unchanged():
    out = build_feed._done_rail([_done_row("Victor Munoz", "completed", "Liverpool")])
    assert "&rarr; Liverpool" in out
    assert "Done &check;" in out
    assert "stayed put" not in out


def test_done_rail_escapes_a_club_name_from_the_notes():
    """joined_club comes out of a notes field the LLM wrote -- escape it."""
    row = _done_row("Test Player", "collapsed", "Arsenal",
                    "[auto] player joined <b>Evil</b> & Co, not Arsenal - rumour did not happen")
    out = build_feed._done_rail([row])
    assert "<b>Evil</b>" not in out
    assert "&lt;b&gt;Evil&lt;/b&gt; &amp; Co" in out


# --- load_resolved: the deals.csv <-> ingest.db join (previously untested) ---------

import csv as _csv
from ingest import cluster

HEADER = ["deal_id", "player", "from_club", "to_club", "window", "outcome",
          "fee_eur_actual", "outcome_date", "outcome_source_url", "verified", "notes"]
WIN = "2026-summer"


def _deals_csv(tmp_path, rows):
    p = tmp_path / "deals.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEADER})
    return p


def _row(did, player, outcome="completed", to="Liverpool", verified="auto", date="2026-08-01"):
    return {"deal_id": did, "player": player, "to_club": to, "window": WIN,
            "outcome": outcome, "verified": verified, "outcome_date": date}


def test_load_resolved_canonicalizes_keys_through_the_alias_map(tmp_path):
    """A settled deal must be pulled out of the live feed under the SAME key the
    grouped meter carries, or it renders as a live rumour forever."""
    path = _deals_csv(tmp_path, [_row("66", "Victor Munoz")])
    bare = cluster.deal_key("Munoz", WIN)
    canon = cluster.deal_key("Victor Munoz", WIN)
    keys, _done = build_feed.load_resolved(path, alias={bare: canon})
    assert keys == {canon}


def test_load_resolved_dedupes_a_split_pair_and_prefers_the_curated_row(tmp_path):
    """Until the ledger merge lands, both halves are resolved rows. The rail must
    show the deal once, and believe the hand-verified side."""
    path = _deals_csv(tmp_path, [
        _row("65", "Munoz", to="Liverpool", verified="auto", date="2026-08-05"),
        _row("66", "Victor Munoz", to="Liverpool", verified="YES", date="2026-07-01"),
    ])
    bare, canon = cluster.deal_key("Munoz", WIN), cluster.deal_key("Victor Munoz", WIN)
    keys, done = build_feed.load_resolved(path, alias={bare: canon})
    assert keys == {canon}
    assert len(done) == 1
    assert done[0]["deal_id"] == "66", "curated row must win over the newer auto row"


def test_load_resolved_without_an_alias_is_unchanged(tmp_path):
    path = _deals_csv(tmp_path, [_row("1", "Isak"), _row("2", "Eze")])
    keys, done = build_feed.load_resolved(path)
    assert keys == {cluster.deal_key("Isak", WIN), cluster.deal_key("Eze", WIN)}
    assert len(done) == 2


def test_load_resolved_ignores_other_windows_and_unresolved_rows(tmp_path):
    path = _deals_csv(tmp_path, [
        _row("1", "Isak", outcome="unknown"),
        {"deal_id": "2", "player": "Eze", "window": "2025-summer", "outcome": "completed"},
        _row("3", "Wirtz", outcome="collapsed"),
    ])
    keys, done = build_feed.load_resolved(path)
    assert keys == {cluster.deal_key("Wirtz", WIN)}
    assert [r["deal_id"] for r in done] == ["3"]


def test_load_resolved_sorts_newest_first(tmp_path):
    path = _deals_csv(tmp_path, [_row("1", "Isak", date="2026-06-01"),
                                 _row("2", "Eze", date="2026-08-06")])
    _keys, done = build_feed.load_resolved(path)
    assert [r["deal_id"] for r in done] == ["2", "1"]
