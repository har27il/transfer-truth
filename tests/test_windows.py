"""Transfer-window calendar + countdown (site/windows.py).

The nameplate now states, in the reader's face, whether the market is open and
how long until it isn't. Wrong here = the site confidently lies about the one
fact every visitor can check against the news, so the boundaries get tested
explicitly: the instant a window opens, the instant it shuts, and the gap.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))

import windows


def at(y, mo, d, h=0):
    return datetime(y, mo, d, h, tzinfo=timezone.utc)


def test_mid_summer_window_is_open():
    st = windows.state(at(2026, 7, 1))
    assert st.phase == "open" and st.label == "Summer"


def test_open_at_the_opening_instant_and_shut_at_the_deadline():
    # 23:00 BST on 1 Sep 2026 == 22:00 UTC. Half-open interval: open at the bell,
    # shut on the deadline instant itself.
    assert windows.state(at(2026, 6, 15)).phase == "open"
    assert windows.state(at(2026, 9, 1, 21)).phase == "open"
    assert windows.state(at(2026, 9, 1, 22)).phase == "closed"


def test_between_windows_counts_down_to_the_next_open():
    st = windows.state(at(2026, 9, 8))
    assert st.phase == "closed"
    assert st.next_label == "Winter"
    assert st.next_open == at(2027, 1, 1)
    assert st.days_until == 115


def test_countdown_rounds_up_so_it_never_reads_zero_while_shut():
    # Six hours before the winter window opens: still shut, must not say "0 days".
    st = windows.state(at(2026, 12, 31, 18))
    assert st.phase == "closed" and st.days_until == 1
    assert windows.dateline(at(2026, 12, 31, 18)).endswith("opens in 1 day")


def test_dateline_wording_by_phase():
    assert windows.dateline(at(2026, 9, 8)) == "Window closed &middot; Winter opens in 115 days"
    assert windows.dateline(at(2026, 7, 1)).startswith("Summer window open &middot;")


def test_exhausted_table_degrades_instead_of_inventing_a_date():
    """Past the last known window we do NOT guess. A countdown to a made-up date
    is worse than none — see the MAINTENANCE note in site/windows.py."""
    st = windows.state(at(2027, 6, 1))
    assert st.phase == "closed" and st.next_open is None and st.days_until is None
    assert windows.dateline(at(2027, 6, 1)) == "Window closed"
    assert windows.countdown_js(at(2027, 6, 1)) == ""


def test_countdown_js_targets_the_same_instant_as_the_server_render():
    """The script overwrites the server-rendered cell; if the two disagreed the
    number would jump on load."""
    js = windows.countdown_js(at(2026, 9, 8))
    assert "2027-01-01T00:00:00Z" in js
    assert "tt-window" in js
