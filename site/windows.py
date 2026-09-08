#!/usr/bin/env python3
"""Transfer-window calendar: which window we're in, and how long until the next one.

The site runs year-round but the market does not. Between windows the feed has
nothing live to say, so the nameplate dateline stops claiming a window is on and
counts down to the next one instead (see DESIGN.md -- micro-caps, ink only, no
colour: the meter stays the only chroma on the page).

Dates are Premier League (the league the feed's sources overwhelmingly cover),
confirmed against premierleague.com. Stored as UTC instants so the countdown is
timezone-honest; BST is UTC+1, GMT is UTC+0.

MAINTENANCE: WINDOWS must be extended as the league confirms each season's dates.
When the table runs out, state() returns next_open=None and the dateline degrades
to a bare "Window closed" rather than inventing a date. Do not guess future
windows here -- a countdown to a made-up date is worse than no countdown.
"""
from collections import namedtuple
from datetime import datetime, timezone

UTC = timezone.utc


def _utc(y, mo, d, h=0):
    return datetime(y, mo, d, h, tzinfo=UTC)


# (key, label, opens, closes) -- chronological. `closes` is the deadline instant.
WINDOWS = [
    # Opened Mon 15 Jun 2026; closed 23:00 BST Tue 1 Sep 2026 (= 22:00 UTC).
    ("2026-summer", "Summer", _utc(2026, 6, 15), _utc(2026, 9, 1, 22)),
    # Opens Fri 1 Jan 2027; closes 23:00 GMT Mon 1 Feb 2027 (= 23:00 UTC).
    ("2027-winter", "Winter", _utc(2027, 1, 1), _utc(2027, 2, 1, 23)),
]

State = namedtuple("State", "phase key label next_open next_label days_until")


def state(now=None):
    """Where the market is right now.

    phase 'open'   -> key/label describe the live window; days_until counts down
                      to ITS deadline.
    phase 'closed' -> next_open/next_label/days_until describe the next window,
                      or are None when the table has run out (see MAINTENANCE).
    """
    now = now or datetime.now(UTC)
    for key, label, opens, closes in WINDOWS:
        if opens <= now < closes:
            return State("open", key, label, None, None, _days(now, closes))
    for key, label, opens, closes in WINDOWS:
        if now < opens:
            return State("closed", None, None, opens, label, _days(now, opens))
    return State("closed", None, None, None, None, None)


def _days(now, target):
    """Whole days remaining, rounded UP so a window that opens in six hours reads
    '1 day' and never counts down to a '0 days' that is still shut."""
    secs = (target - now).total_seconds()
    return max(0, -(-int(secs) // 86400))


def _plural(n):
    return "day" if n == 1 else "days"


def dateline(now=None):
    """The nameplate's window cell: plain text, uppercased by the .np-line style.

    Server-rendered so the page is correct with JavaScript off; countdown_js()
    then re-computes it in the reader's browser, because the site is a static
    file rebuilt only twice a week and a baked number would drift by days.
    """
    st = state(now)
    if st.phase == "open":
        return f"{st.label} window open &middot; {st.days_until} {_plural(st.days_until)} to deadline"
    if st.next_open is None:
        return "Window closed"
    return f"Window closed &middot; {st.next_label} opens in {st.days_until} {_plural(st.days_until)}"


def countdown_js(now=None):
    """Client-side refresh of the dateline cell. Recomputes once on load -- no
    ticking timer (DESIGN.md motion: a credibility product doesn't bounce), and
    day granularity means a seconds clock would show nothing anyway."""
    st = state(now)
    if st.phase == "open":
        target, tmpl = _iso(st.next_open or _closes_of(st.key)), f"{st.label} window open &middot; %D% %U% to deadline"
    elif st.next_open is not None:
        target, tmpl = _iso(st.next_open), f"Window closed &middot; {st.next_label} opens in %D% %U%"
    else:
        return ""
    return (
        "<script>(function(){var e=document.getElementById('tt-window');if(!e)return;"
        f"var t=Date.parse('{target}'),d=Math.ceil((t-Date.now())/864e5);"
        "if(!(d>0))return;"
        f"e.innerHTML='{tmpl}'.replace('%D%',d).replace('%U%',d===1?'day':'days');"
        "})();</script>"
    )


def _closes_of(key):
    for k, _label, _opens, closes in WINDOWS:
        if k == key:
            return closes
    return None


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
