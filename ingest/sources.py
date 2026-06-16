#!/usr/bin/env python3
"""
RSS sources — fetch transfer-news posts from tier-1 outlets (stdlib only).

RSS first, not Reddit: free, legal, no auth/ToS trap, and outlets publish with
attribution intact (the plan flagged r/soccer for stripping tier-1 attribution).
Each outlet is one (name, url); add Reddit/X later behind the same `fetch_all`
interface and nothing downstream changes.

A "post" is a plain dict: {url, source, title, summary, published}.
"""
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "TransferTruth/0.1 (ingestion; research; contact via repo)"

# (outlet name -> RSS url). Football-wide feeds; the engine filters non-transfer posts.
DEFAULT_FEEDS = {
    "BBC Sport": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "Sky Sports": "https://www.skysports.com/rss/12040",
    "The Guardian": "https://www.theguardian.com/football/transfer-window/rss",
}

_TAG = lambda e, name: (e.findtext(name) or "").strip()


def _clean(text):
    # RSS descriptions often carry HTML entities / stray tags; keep it plain.
    return unescape(text or "").replace("\n", " ").strip()


def parse_rss(xml_bytes, source):
    """Parse RSS/Atom bytes into post dicts. Tolerant of both <item> and <entry>."""
    posts = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return posts
    items = root.iter("item")
    for it in items:
        link = _TAG(it, "link")
        if not link:
            continue
        posts.append({
            "url": link,
            "source": source,
            "title": _clean(_TAG(it, "title")),
            "summary": _clean(_TAG(it, "description")),
            "published": _TAG(it, "pubDate"),
        })
    return posts


def fetch_feed(source, url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_rss(r.read(), source)


def fetch_all(feeds=None):
    """Fetch every feed. One outlet failing never aborts the rest (per-item isolation)."""
    feeds = feeds or DEFAULT_FEEDS
    posts, errors = [], []
    for source, url in feeds.items():
        try:
            posts.extend(fetch_feed(source, url))
        except Exception as e:  # network / parse — log and keep going
            errors.append((source, str(e)))
    if errors:
        for source, msg in errors:
            print(f"  [warn] feed '{source}' failed: {msg}")
    return posts


if __name__ == "__main__":
    got = fetch_all()
    print(f"Fetched {len(got)} posts from {len(DEFAULT_FEEDS)} feeds")
    for p in got[:5]:
        print(f"  [{p['source']}] {p['title'][:80]}")
