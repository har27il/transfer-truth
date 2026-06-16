"""Ingestion pipeline tests — fully offline (injected feed + injected extractor).

Covers: URL dedup, player-based clustering (incl. accent-folding), and every filter
branch (non-transfer, low-confidence, no-player) plus the happy path where two
outlets reporting the same player land in ONE deal with two claims.
"""
from ingest import store, cluster, pipeline, sources


def _conn():
    return store.connect(":memory:")


# ---- store + cluster units ------------------------------------------------

def test_add_post_dedups_on_url():
    conn = _conn()
    p = {"url": "http://x/1", "source": "BBC Sport", "title": "t", "summary": "s"}
    assert store.add_post(conn, p) is True       # new
    assert store.add_post(conn, p) is False      # seen -> dedup


def test_deal_key_clusters_by_player_accent_insensitive():
    assert cluster.deal_key("João Pedro", "2025-summer") == cluster.deal_key("Joao Pedro", "2025-summer")
    assert cluster.deal_key("Isak", "2025-summer") != cluster.deal_key("Eze", "2025-summer")
    assert cluster.deal_key("", "2025-summer") == ""     # no player -> empty key


def test_rss_parser_extracts_items():
    xml = b"""<rss><channel>
      <item><title>Isak to Liverpool</title><link>http://x/1</link>
            <description>Here we go</description><pubDate>Mon, 01 Sep 2025 10:00:00 GMT</pubDate></item>
      <item><title>No link item</title></item>
    </channel></rss>"""
    posts = sources.parse_rss(xml, "BBC Sport")
    assert len(posts) == 1                        # item with no <link> is skipped
    assert posts[0]["url"] == "http://x/1" and posts[0]["source"] == "BBC Sport"


# ---- pipeline end-to-end --------------------------------------------------

def _fake_feed():
    s = "BBC Sport"
    return [
        {"url": "a", "source": s, "title": "Isak to Liverpool here we go", "summary": "", "published": "Mon, 01 Sep 2025 10:00:00 GMT"},
        {"url": "b", "source": s, "title": "Match report: Arsenal 2-0 Spurs", "summary": "", "published": ""},
        {"url": "c", "source": s, "title": "Club vaguely linked with a player", "summary": "", "published": ""},
        {"url": "d", "source": s, "title": "Talks ongoing for an unnamed target", "summary": "", "published": ""},
        {"url": "e", "source": "Sky Sports", "title": "Isak deal advancing", "summary": "", "published": ""},
        {"url": "a", "source": s, "title": "Isak to Liverpool here we go", "summary": "", "published": ""},  # dup url
    ]


def _fake_analyze(text):
    base = {"is_transfer_claim": True, "from_club": "Newcastle United", "to_club": "Liverpool",
            "stage": "here_we_go", "implied_p": 0.99, "source_name": None,
            "source_identifiable": False, "direction_confidence": 0.95, "fee_eur": None}
    if "Match report" in text:
        return {**base, "is_transfer_claim": False, "player": None, "stage": None}
    if "vaguely linked" in text:
        return {**base, "player": "Someone", "direction_confidence": 0.2}   # low conf
    if "unnamed target" in text:
        return {**base, "player": None, "direction_confidence": 0.8}        # no player
    if "Isak deal advancing" in text:
        return {**base, "player": "Alexander Isak", "stage": "advanced", "implied_p": 0.60,
                "source_name": "Sky Sports", "source_identifiable": True, "direction_confidence": 0.8}
    return {**base, "player": "Alexander Isak"}                             # the "here we go"


def test_pipeline_dedups_filters_and_clusters():
    conn = _conn()
    stats = pipeline.run(conn, sources_fn=_fake_feed, analyze_fn=_fake_analyze,
                         window="2025-summer")
    assert stats == {"fetched": 6, "dup": 1, "new": 5, "non_transfer": 1,
                     "low_conf": 1, "no_player": 1, "claims": 2}

    c = store.counts(conn)
    assert c == {"posts": 5, "claims": 2, "deals": 1}   # both Isak posts -> ONE deal

    key = cluster.deal_key("Alexander Isak", "2025-summer")
    claims = store.claims_for_deal(conn, key)
    assert len(claims) == 2
    # source attribution: credited journalist wins, else the outlet that published it
    srcs = sorted(cl["source_name"] for cl in claims)
    assert srcs == ["BBC Sport", "Sky Sports"]
    assert all(cl["source_identifiable"] == 1 for cl in claims)


def test_pipeline_is_idempotent_on_rerun():
    conn = _conn()
    pipeline.run(conn, sources_fn=_fake_feed, analyze_fn=_fake_analyze)
    before = store.counts(conn)
    stats2 = pipeline.run(conn, sources_fn=_fake_feed, analyze_fn=_fake_analyze)
    assert stats2["new"] == 0 and stats2["claims"] == 0   # everything already seen
    assert store.counts(conn) == before                   # no growth
