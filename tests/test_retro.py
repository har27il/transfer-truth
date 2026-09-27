"""Retrospective stories must never read as live rumours -- the Mbappé -> Liverpool bug.

LIVE BUG (2026-09-24): BBC Sport's "I was close to joining Liverpool in 2016 - Mbappe"
is Mbappé recalling a near-move from his Monaco days. It was extracted as a live
`advanced` claim (p=0.60, because the stage table maps "close" -> advanced), dated by
its RSS publish date, became deal 458 / claim 995, and LED the live feed as
"Kylian Mbappe -> Liverpool, 60% Possible".

Covers every layer of the fix, plus the guards against over-filtering:
  - ingest.retro.is_retrospective: the one shared, deterministic rule
  - pipeline: retrospective posts never reach the LLM or become claims
  - read time: the feed and the bridge ignore a retrospective claim ALREADY cached in
    ingest.db (backfill only re-reads posts that produced no claim, so a pipeline-only
    fix would leave the cached claim live)
  - the current-club sanity check: contradicted rumours are flagged, not published
  - `historical` never reaches scoring, promotion or the resolver
  - the prompt ships the rule + the Mbappé negative example; the golden regression case
"""
import csv
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))

import build_feed  # noqa: E402
from engine import golden, run as engine_run  # noqa: E402
from ground_truth import trusted, load_outcomes  # noqa: E402
from ingest import store, cluster, pipeline, meter, bridge  # noqa: E402
from outcome import promote, apply as apply_mod  # noqa: E402

TODAY = date(2026, 9, 27)
WIN = "2026-summer"
REL = {"BBC Sport": 0.87, "Sky Sports": 0.81}
POP = 0.75

# The live post, verbatim. Title: claim 995's raw_quote. Summary: the article's standfirst
# (its meta description) -- the RSS item itself had rotated out by 2026-09-27. Joined the
# way pipeline.run joins them before every filter and the extraction call.
MBAPPE_URL = ("https://www.bbc.co.uk/sport/football/articles/c68042y9l4kno"
              "?at_medium=RSS&at_campaign=rss")
MBAPPE_TITLE = "I was close to joining Liverpool in 2016 - Mbappe"
MBAPPE_SUMMARY = ('Kylian Mbappe says his mum was a "big fan" of Jurgen Klopp and wanted '
                  'her son to move to Merseyside.')
MBAPPE_TEXT = f"{MBAPPE_TITLE} {MBAPPE_SUMMARY}"
NOTE = "Historical: talks with Liverpool in 2017; joined PSG. Currently at Real Madrid."

DEALS_HEADER = ["deal_id", "player", "from_club", "to_club", "window", "outcome",
                "fee_eur_actual", "outcome_date", "outcome_source_url", "verified", "notes"]
CLAIMS_HEADER = ["claim_id", "deal_id", "source_name", "platform", "claim_date",
                 "stage", "source_url", "raw_quote", "verified"]


@pytest.fixture
def retro():
    from ingest import retro as mod     # the new module under test
    return mod


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _mbappe_post():
    return {"url": MBAPPE_URL, "source": "BBC Sport", "title": MBAPPE_TITLE,
            "summary": MBAPPE_SUMMARY, "published": "Tue, 22 Sep 2026 09:00:00 GMT"}


def _post(url, title, source="Sky Sports"):
    return {"url": url, "source": source, "title": title, "summary": "", "published": ""}


def _store_claim(conn, post, player, to_club, stage, p, date_, from_club=None):
    store.add_post(conn, post)
    store.add_claim(conn, {
        "post_url": post["url"], "deal_key": cluster.deal_key(player, WIN), "player": player,
        "from_club": from_club, "to_club": to_club, "stage": stage, "implied_p": p,
        "source_name": post["source"], "source_identifiable": 1,
        "direction_confidence": 0.9, "fee_eur": None, "claim_date": date_})


def _store_mbappe(conn, date_="2026-09-22"):
    """Claim 995 exactly as it was extracted: advanced, p=0.60, BBC Sport."""
    _store_claim(conn, _mbappe_post(), "Kylian Mbappe", "Liverpool", "advanced", 0.60, date_)


def _disposition(conn, url):
    row = conn.execute("SELECT verdict FROM dispositions WHERE url = ?", (url,)).fetchone()
    return row["verdict"] if row else None


# --- the rule ---------------------------------------------------------------------

def test_retrospective_headline_is_not_an_active_rumour(retro):
    for text in [
        "Mbappé reveals he was close to Liverpool in 2017",   # the spec's headline
        MBAPPE_TEXT,                                           # the verbatim live post
        "Kylian Mbappe reveals he was 'close' to joining Liverpool in 2016",  # the article's own title
        "Mbappé revealed talks with Liverpool in 2017",
        "Gerrard: I came close to joining Chelsea back in 2005",
        "Klopp admits Liverpool almost signed Haaland years ago",
        "Rooney nearly signed for Newcastle as a teenager",
    ]:
        assert retro.is_retrospective(text, TODAY), f"missed a retrospective story: {text!r}"


def test_genuine_current_rumours_are_not_flagged(retro):
    """Over-filtering guard. The pipeline drop is permanent (the post stays marked seen),
    so a false positive silently loses a real rumour for good."""
    for text in [
        "Liverpool were close to signing X last week, talks could resume",   # recent near-miss, still live
        "Isak, who joined Newcastle in 2022, is wanted by Arsenal",           # old year, current rumour
        "Manchester United were close to agreeing a fee last night",
        "Tottenham nearly signed Eze in January, now Arsenal lead the race",
        "Arsenal, who came close to signing Szoboszlai in 2019, have reignited their interest",
        "Chelsea held talks over Garnacho in 2023 and could return with a fresh bid",
        "Liverpool in advanced talks to sign Marc Guehi from Crystal Palace",
        "Alexander Isak to Liverpool, here we go! (Fabrizio Romano)",
        "Newcastle close to agreeing £60m deal for Jorgen Strand Larsen",
        "",
    ]:
        assert not retro.is_retrospective(text, TODAY), f"FALSE POSITIVE: {text!r}"


def test_a_year_must_be_well_before_today_to_date_the_story(retro):
    text = "Liverpool almost signed him in 2024"
    assert retro.is_retrospective(text, date(2026, 9, 27))       # two years back: history
    assert not retro.is_retrospective(text, date(2025, 9, 27))   # last year: left to the model


# --- pipeline: never reaches the LLM, never becomes a claim --------------------------

def _claims_everything(text):
    """A model making the live mistake: every post is a live `advanced` claim."""
    return {"is_transfer_claim": True, "historical": False,
            "player": "Kylian Mbappe" if "Mbappe" in text else "Marc Guehi",
            "from_club": None, "to_club": "Liverpool", "stage": "advanced", "implied_p": 0.60,
            "source_name": None, "source_identifiable": False,
            "direction_confidence": 0.9, "fee_eur": None}


def test_pipeline_drops_a_retrospective_story_before_extraction():
    seen = []

    def analyze(text):
        seen.append(text)
        return _claims_everything(text)

    live = {**_post("http://x/guehi",
                    "Liverpool were close to signing Marc Guehi last week, talks could resume"),
            "published": "Fri, 25 Sep 2026 09:00:00 GMT"}
    conn = store.connect(":memory:")
    stats = pipeline.run(conn, sources_fn=lambda: [_mbappe_post(), live],
                         analyze_fn=analyze, window=WIN)
    assert stats["excluded"] == 1 and stats["claims"] == 1
    assert len(seen) == 1 and "Guehi" in seen[0]       # zero tokens spent on the old story
    assert store.deal_keys(conn) == [cluster.deal_key("Marc Guehi", WIN)]
    # A regex drop records NO verdict: if the rule ever misfires, a backfill after the
    # fix can still recover the post (backfill skips anything with a disposition).
    assert _disposition(conn, MBAPPE_URL) is None
    from ingest import backfill
    assert MBAPPE_URL in [p["url"] for p in backfill.claimless_posts(conn, "2000-01-01")]


def test_pipeline_trusts_the_model_when_it_flags_a_story_historical():
    """Second layer: a retrospective story with no year or 'years ago' for the regex to
    anchor on is the prompt's job. When the model says historical=true, no claim is
    stored -- even if it ALSO (wrongly) set is_transfer_claim."""
    post = _post("http://x/retro", "Mbappe on the Liverpool move that never was", "BBC Sport")
    conn = store.connect(":memory:")
    stats = pipeline.run(conn, sources_fn=lambda: [post],
                         analyze_fn=lambda t: {**_claims_everything(t), "historical": True},
                         window=WIN)
    assert stats["claims"] == 0 and stats["non_transfer"] == 1
    assert store.counts(conn)["claims"] == 0
    assert _disposition(conn, "http://x/retro") == "historical"


# --- read time: the claim already cached in ingest.db --------------------------------

def test_feed_meters_ignore_a_cached_retrospective_claim():
    """The live claim is ALREADY in the cached ingest.db, and backfill only re-reads
    posts that produced no claim -- so the feed must drop it at read time."""
    conn = store.connect(":memory:")
    _store_mbappe(conn, date_="2026-09-26")   # fresh, as it was on the day it led the feed
    _store_claim(conn, _post("http://x/guehi", "Liverpool in talks to sign Marc Guehi"),
                 "Marc Guehi", "Liverpool", "talks", 0.35, "2026-09-26", from_club="Crystal Palace")
    kw = dict(today=TODAY, reliability=REL, pop_weight=POP)
    assert "Kylian Mbappe" in [m["player"] for m in meter.meters(conn, **kw)]   # bug reproduced
    rows = meter.meters(conn, claim_filter=build_feed.live_claim_filter(conn, TODAY), **kw)
    assert [m["player"] for m in rows] == ["Marc Guehi"]


def test_bridge_never_creates_a_deal_from_a_retrospective_story(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [])
    conn = store.connect(":memory:")
    _store_mbappe(conn)
    stats = bridge.bridge(conn, deals_path=p)
    assert stats["created"] == []
    assert _read(p) == []


def test_bridge_relabels_an_auto_deal_built_only_on_retrospective_stories(tmp_path):
    """Deal 458 as it sits in the ledger (unknown/auto), and the shape the resolver would
    have given it on the next run (collapsed/auto: 'window closed; stayed at ...'), which
    would have queued claim 995 to score AGAINST BBC Sport for a claim it never made.
    Both become `historical`, still as machine proposals (verified=auto)."""
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [
        {"deal_id": "458", "player": "Kylian Mbappe", "to_club": "Liverpool", "window": WIN,
         "outcome": "unknown", "verified": "auto",
         "notes": "[auto-ingested] 1 claim(s); to_club provisional"},
        {"deal_id": "459", "player": "Wayne Rooney", "to_club": "Newcastle United", "window": WIN,
         "outcome": "collapsed", "verified": "auto", "outcome_date": "2026-09-03",
         "notes": "[auto] window closed; player stayed at Everton - move to Newcastle United did not happen"},
    ])
    conn = store.connect(":memory:")
    _store_mbappe(conn)
    _store_claim(conn, _post("http://x/rooney", "Rooney nearly signed for Newcastle as a teenager",
                             "BBC Sport"),
                 "Wayne Rooney", "Newcastle United", "advanced", 0.60, "2026-09-22")
    stats = bridge.bridge(conn, deals_path=p)
    rows = {r["deal_id"]: r for r in _read(p)}
    assert rows["458"]["outcome"] == "historical" and rows["459"]["outcome"] == "historical"
    assert all(r["verified"] == "auto" for r in rows.values())   # a machine never writes YES
    assert "retrospective" in rows["458"]["notes"]
    assert {did for did, _ in stats["historical"]} == {"458", "459"}


def test_bridge_leaves_a_deal_with_any_live_claim_alone(tmp_path):
    """A retrospective story next to a genuine current rumour on the same player is NOT
    a historical deal -- the live claim keeps it open."""
    p = tmp_path / "deals.csv"
    before = {"deal_id": "458", "player": "Kylian Mbappe", "to_club": "Liverpool",
              "window": WIN, "outcome": "unknown", "verified": "auto",
              "notes": "[auto-ingested] 2 claim(s); to_club provisional"}
    _write(p, DEALS_HEADER, [before])
    conn = store.connect(":memory:")
    _store_mbappe(conn)
    _store_claim(conn, _post("http://x/live", "Liverpool in talks to sign Kylian Mbappe"),
                 "Kylian Mbappe", "Liverpool", "talks", 0.35, "2026-09-25")
    stats = bridge.bridge(conn, deals_path=p)
    # Never relabelled at all -- not relabelled and then quietly re-opened, which would
    # leave the outcome right but wipe the row's notes (the reopen step masks that).
    assert stats["historical"] == [] and stats["reopened"] == []
    assert _read(p) == [{k: before.get(k, "") for k in DEALS_HEADER}]


def test_bridge_never_relabels_a_curated_row(tmp_path):
    curated = {"deal_id": "7", "player": "Kylian Mbappe", "to_club": "Liverpool", "window": WIN,
               "outcome": "unknown", "verified": "YES", "notes": "hand-checked"}
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [curated])
    conn = store.connect(":memory:")
    _store_mbappe(conn)
    bridge.bridge(conn, deals_path=p)
    assert _read(p) == [{k: curated.get(k, "") for k in DEALS_HEADER}]


def _claims_for_458():
    return [
        {"claim_id": "995", "deal_id": "458", "source_name": "BBC Sport", "platform": "rss",
         "claim_date": "2026-09-22", "stage": "advanced", "source_url": MBAPPE_URL,
         "raw_quote": MBAPPE_TITLE, "verified": "auto"},
        {"claim_id": "996", "deal_id": "458", "source_name": "Sky Sports", "platform": "rss",
         "claim_date": "2026-09-25", "stage": "talks", "source_url": "http://x/live",
         "raw_quote": "Liverpool in talks to sign Kylian Mbappe", "verified": "auto"},
        {"claim_id": "1", "deal_id": "458", "source_name": "The Athletic", "platform": "rss",
         "claim_date": "2026-09-22", "stage": "advanced", "source_url": "http://x/curated",
         "raw_quote": "Mbappé reveals he was close to Liverpool in 2017", "verified": "YES"},
    ]


def test_bridge_claims_prunes_retrospective_auto_claims_and_never_appends_them(tmp_path):
    """Claim 995 is BBC's retrospective headline filed as an `advanced` claim. Promoted,
    it would have scored against BBC Sport for a claim it never made. Machine claims
    whose post is a retrospective story are pruned, the store never re-appends them,
    and hand-curated claims are never auto-deleted."""
    dp, cp = tmp_path / "deals.csv", tmp_path / "claims.csv"
    _write(dp, DEALS_HEADER, [{"deal_id": "458", "player": "Kylian Mbappe", "window": WIN,
                               "outcome": "unknown", "verified": "auto"}])
    _write(cp, CLAIMS_HEADER, _claims_for_458())
    conn = store.connect(":memory:")
    _store_mbappe(conn)                  # still in the cached store -> must not come back
    added = bridge.bridge_claims(conn, deals_path=dp, claims_path=cp)
    assert added == []
    assert [r["claim_id"] for r in _read(cp)] == ["996", "1"]


def test_bridge_claims_never_prunes_on_a_headline_alone(tmp_path):
    """Review finding: with the post evicted from the cache, judging the claim's
    raw_quote (title only) loses any live signal that was in the summary, and a claim
    kept yesterday gets pruned today. No stored post -> no verdict -> the claim stays."""
    dp, cp = tmp_path / "deals.csv", tmp_path / "claims.csv"
    _write(dp, DEALS_HEADER, [{"deal_id": "458", "player": "Kylian Mbappe", "window": WIN,
                               "outcome": "unknown", "verified": "auto"}])
    _write(cp, CLAIMS_HEADER, _claims_for_458())
    bridge.bridge_claims(store.connect(":memory:"), deals_path=dp, claims_path=cp)
    assert [r["claim_id"] for r in _read(cp)] == ["995", "996", "1"]


# --- current-club sanity check ------------------------------------------------------

CURRENT = "Real Madrid"


def _rc(frm, to, date_, url="http://x/1"):
    return {"from_club": frm, "to_club": to, "claim_date": date_, "post_url": url}


def _ledger_row(player, to, outcome="completed", verified="YES", outcome_date="2024-06-03"):
    return {"player": player, "to_club": to, "outcome": outcome, "verified": verified,
            "outcome_date": outcome_date}


def test_verified_current_club_comes_only_from_trusted_completed_moves(retro):
    rows = [
        _ledger_row("Tomás Ferreira", "Real Madrid", outcome_date="2024-06-03"),
        _ledger_row("Tomas Ferreira", "Monaco", outcome_date="2017-08-31"),        # older move
        _ledger_row("Tomas Ferreira", "Liverpool", verified="auto", outcome_date="2026-09-01"),
        _ledger_row("Tomas Ferreira", "Arsenal", outcome="collapsed", outcome_date="2026-09-02"),
    ]
    assert retro.verified_current_clubs(rows) == {"tomas ferreira": "Real Madrid"}


def test_rumour_contradicting_the_verified_current_club_with_no_fresh_source_is_flagged(retro):
    # places him at Monaco -- a club the ledger says he has left -- and nothing recent disagrees
    assert retro.needs_review([_rc("Monaco", "Liverpool", "2026-08-10")], CURRENT, TODAY)
    # a "move" to the club he already plays for
    assert retro.needs_review([_rc(None, "Real Madrid", "2026-08-10")], CURRENT, TODAY)


def test_a_recently_dated_contradicting_report_is_still_flagged(retro):
    """A publish date is not freshness: the Mbappé post was dated 22 Sep 2026 and
    described 2016. A report placing the player at a club he has left is out of date
    however recently it ran; only a consistent, fresh source clears it."""
    assert retro.needs_review([_rc("Monaco", "Liverpool", "2026-09-26")], CURRENT, TODAY)


def test_a_fresh_forward_looking_source_keeps_the_rumour_live(retro):
    claims = [_rc("Monaco", "Liverpool", "2026-08-10", "http://x/old"),
              _rc("Real Madrid", "Liverpool", "2026-09-20", "http://x/new")]
    assert not retro.needs_review(claims, CURRENT, TODAY)


def test_a_fresh_source_that_is_itself_retrospective_does_not_count(retro):
    claims = [_rc("Monaco", "Liverpool", "2026-08-10", "http://x/old"),
              _rc(None, "Liverpool", "2026-09-26", MBAPPE_URL)]
    texts = {MBAPPE_URL: MBAPPE_TEXT, "http://x/old": "Monaco star linked with Liverpool"}
    assert retro.needs_review(claims, CURRENT, TODAY, texts=texts)


def test_an_ordinary_rumour_is_not_flagged(retro):
    for claims in ([_rc("Real Madrid", "Liverpool", "2026-08-10")],      # consistent, if stale
                   [_rc("Real Madrid CF", "Liverpool", "2026-09-26")],    # club alias
                   [_rc(None, "Liverpool", "2026-09-26")]):               # origin not stated
        assert not retro.needs_review(claims, CURRENT, TODAY)
    # no verified club on record -> nothing to contradict
    assert not retro.needs_review([_rc("Monaco", "Liverpool", "2026-08-10")], None, TODAY)


def test_a_club_name_variant_is_not_a_contradiction(retro):
    """Flag only CLEAR contradictions. The live extractor writes "Stuttgart" where the
    ledger says "VfB Stuttgart" (seen in the 2026-09-27 golden eval) -- the same club, and
    no reason to hold a deal for review. Genuinely different clubs still flag."""
    assert not retro.needs_review([_rc("Stuttgart", "Newcastle United", "2026-08-10")],
                                  "VfB Stuttgart", TODAY)
    assert retro.needs_review([_rc("Manchester City", "Arsenal", "2026-08-10")],
                              "Manchester United", TODAY)
    assert retro.needs_review([_rc("AC Milan", "Arsenal", "2026-08-10")], "Inter Milan", TODAY)


def _meter_row(player, claims, days_quiet=0):
    return {"player": player, "to_club": "Liverpool", "percent": 60, "probability": 0.6,
            "days_quiet": days_quiet, "latest_stage": "advanced", "claims": claims}


def test_flagged_deal_is_downgraded_to_cooling_under_review_never_the_lede():
    rows = [_meter_row("Tomás Ferreira", [_rc("Monaco", "Liverpool", "2026-09-26")]),
            _meter_row("Marc Guehi", [_rc("Crystal Palace", "Liverpool", "2026-09-26")])]
    build_feed.flag_for_review(rows, {"tomas ferreira": "Real Madrid"}, TODAY)
    fresh, quiet = build_feed.split_fresh(rows)
    assert [m["player"] for m in fresh] == ["Marc Guehi"]
    assert [m["player"] for m in quiet] == ["Tomás Ferreira"]
    assert "under review" in build_feed._cooling_section(quiet)


def test_a_flagged_deal_is_not_promoted_when_nothing_else_is_fresh():
    """split_fresh falls back to quiet deals when nothing is fresh, so the page never
    goes blank. That fallback must never promote a deal flagged for review to the lede."""
    rows = [_meter_row("Tomás Ferreira", [_rc("Monaco", "Liverpool", "2026-09-26")]),
            _meter_row("Marc Guehi", [_rc("Crystal Palace", "Liverpool", "2026-09-10")],
                       days_quiet=17)]
    build_feed.flag_for_review(rows, {"tomas ferreira": "Real Madrid"}, TODAY)
    fresh, quiet = build_feed.split_fresh(rows)
    assert [m["player"] for m in fresh] == ["Marc Guehi"]       # the quiet-but-clean deal
    assert [m["player"] for m in quiet] == ["Tomás Ferreira"]


def test_cooling_cap_never_hides_a_deal_under_review():
    rows = [{"player": f"Player {i}", "to_club": "Club Y", "percent": 15,
             "days_quiet": i + 4, "latest_stage": "talks"} for i in range(10)]
    rows.append({"player": "Tomás Ferreira", "to_club": "Liverpool", "percent": 60,
                 "days_quiet": 20, "latest_stage": "advanced", "review": True})
    out = build_feed._cooling_section(rows, cap=3)
    assert "Tomás Ferreira" in out and "under review" in out   # last in line, still shown
    assert out.count('class="row cold"') == 3                   # page weight still capped
    assert "3 of 11 shown" in out


def test_needs_review_only_moves_the_deal_to_cooling_under_review(tmp_path, monkeypatch):
    """The review flag is DISPLAY-ONLY. Driven through the real build (build_feed.main):
    the flagged deal is not the lede, but it is still on the page, in the cooling tail,
    marked 'under review'. Its claims stay in the store, and the ledger -- the deal's
    outcome included -- is byte-for-byte untouched."""
    fresh = date.today().isoformat()          # main() judges freshness against the real today
    deals = tmp_path / "deals.csv"
    _write(deals, DEALS_HEADER, [
        {"deal_id": "1", "player": "Tomás Ferreira", "from_club": "Monaco",
         "to_club": "Real Madrid", "window": "2024-summer", "outcome": "completed",
         "outcome_date": "2024-06-03", "verified": "YES"},
        {"deal_id": "2", "player": "Tomás Ferreira", "to_club": "Liverpool", "window": WIN,
         "outcome": "unknown", "verified": "auto", "notes": "[auto-ingested] 1 claim(s)"},
    ])
    ledger_before = deals.read_bytes()
    conn = store.connect(":memory:")
    _store_claim(conn, _post("http://x/ferreira", "Monaco's Tomás Ferreira in talks with Liverpool"),
                 "Tomás Ferreira", "Liverpool", "talks", 0.35, fresh, from_club="Monaco")
    _store_claim(conn, _post("http://x/guehi", "Liverpool in talks to sign Marc Guehi"),
                 "Marc Guehi", "Liverpool", "talks", 0.35, fresh, from_club="Crystal Palace")
    claims_before = store.counts(conn)["claims"]

    out = tmp_path / "feed.html"
    monkeypatch.setattr(build_feed, "ROOT", tmp_path)
    monkeypatch.setattr(build_feed, "DEALS", deals)
    monkeypatch.setattr(build_feed, "OUT", out)
    monkeypatch.setattr(build_feed.store, "connect", lambda *a, **k: conn)
    build_feed.main()

    page = out.read_text(encoding="utf-8")
    lede = page.split('<section class="lede">', 1)[1].split("</section>", 1)[0]
    assert "Marc Guehi" in lede and "Ferreira" not in lede      # downgraded from the lede...
    cooling = page.split("<h2>Cooling off</h2>", 1)[1]
    assert "Tomás Ferreira" in cooling and "under review" in cooling   # ...never hidden
    assert deals.read_bytes() == ledger_before                  # outcome never changed
    assert store.counts(conn)["claims"] == claims_before        # nothing deleted


# --- the rail: a historical deal shows its note, and hides nothing live --------------

def test_load_resolved_lists_a_historical_deal_without_hiding_live_claims(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [{"deal_id": "458", "player": "Kylian Mbappe", "to_club": "Liverpool",
                              "window": WIN, "outcome": "historical", "verified": "YES",
                              "outcome_date": "2026-09-27", "notes": NOTE}])
    keys, done = build_feed.load_resolved(p)
    assert keys == set()              # a genuinely new Mbappé rumour would still show live
    assert [r["deal_id"] for r in done] == ["458"]


def test_done_rail_shows_a_historical_deal_with_its_note_in_ink():
    out = build_feed._done_rail([{"player": "Kylian Mbappe", "outcome": "historical",
                                  "verified": "YES", "to_club": "Liverpool", "notes": NOTE}])
    assert NOTE in out
    assert "Not live" in out
    # DESIGN.md "colour is earned": a correction is not a denial and not a done deal
    for hue in ("--denied", "--likely", "--contested"):
        assert hue not in out
    assert 'class="stayed"' not in out and 'class="tick"' not in out


def test_done_rail_never_prints_a_machine_note_for_an_auto_historical_deal():
    out = build_feed._done_rail([{"player": "Wayne Rooney", "outcome": "historical",
                                  "verified": "auto", "to_club": "Newcastle United",
                                  "notes": "[auto] historical: every source is a retrospective story"}])
    assert "[auto]" not in out
    assert "not a live rumour" in out


# --- `historical` never reaches scoring, promotion or the resolver --------------------

HISTORICAL_ROW = {"deal_id": "458", "player": "Kylian Mbappe", "from_club": "",
                  "to_club": "Liverpool", "window": WIN, "outcome": "historical",
                  "verified": "YES", "notes": NOTE}


def test_historical_never_reaches_scoring(tmp_path):
    for verified in ("YES", "auto"):
        row = {**HISTORICAL_ROW, "verified": verified}
        assert trusted(row) is None and trusted(row, include_auto=True) is None
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [HISTORICAL_ROW, {**HISTORICAL_ROW, "deal_id": "459", "verified": "auto"}])
    assert load_outcomes(p) == {} and load_outcomes(p, include_auto=True) == {}


def test_historical_never_reaches_promotion():
    assert promote.pending([{**HISTORICAL_ROW, "verified": "auto"}, dict(HISTORICAL_ROW)]) == []


def test_historical_never_reaches_the_resolver():
    called = []

    def resolver(row):
        called.append(row["deal_id"])
        return {"status": "stayed", "joined_club": None, "evidence": "", "window_closed": True}

    rows = [dict(HISTORICAL_ROW), {**HISTORICAL_ROW, "deal_id": "459", "verified": "auto"}]
    assert apply_mod.resolve_unknowns(rows, resolver=resolver, concurrency=1) == []
    assert called == []


def test_historical_rows_are_never_refreshed_or_reopened():
    row = {**HISTORICAL_ROW, "verified": "auto"}
    before = dict(row)
    conn = store.connect(":memory:")
    _store_claim(conn, _post("http://x/c", "Chelsea in talks to sign Kylian Mbappe"),
                 "Kylian Mbappe", "Chelsea", "talks", 0.35, "2026-09-25")
    stats = bridge.refresh_and_reopen(conn, [row], alias={})
    assert row == before
    assert stats["refreshed"] == [] and stats["reopened"] == []


# --- the prompt + the golden regression case -----------------------------------------

def test_prompt_ships_the_historical_rule_and_the_mbappe_negative_example():
    """load_system_prompt only ships the text between the dashed lines plus the few-shot
    block -- a rule written anywhere else in the file is silently dropped."""
    p = engine_run.load_system_prompt()
    assert "PAST EVENTS ARE NOT LIVE RUMOURS" in p
    assert '"historical": boolean' in p                          # in the output contract
    assert MBAPPE_TITLE in p                                     # the negative example
    assert '"historical":true' in p


def test_regression_corpus_carries_the_mbappe_miss():
    cases = golden.load_regressions()
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids), "duplicate case ids"
    for c in cases:
        exp = c["expected"]
        for f in golden.CRITICAL + ("historical",):
            assert f in exp, f"{c['id']} missing expected.{f}"
        if exp["historical"]:
            assert exp["is_transfer_claim"] is False
            assert exp["stage"] is None and exp["to_club"] is None
        assert golden.grade_regression(exp, exp)["critical_ok"]
    mbappe = next(c for c in cases if c["id"] == "mbappe-liverpool-retro")
    assert mbappe["input"] == MBAPPE_TEXT
    assert mbappe["expected"]["historical"] is True


def test_regression_grader_fails_a_live_reading_of_a_historical_story():
    """The grader must catch the exact live miss: Mbappé read as a live advanced claim."""
    exp = next(c for c in golden.load_regressions()
               if c["id"] == "mbappe-liverpool-retro")["expected"]
    live_miss = {**exp, "is_transfer_claim": True, "historical": False,
                 "to_club": "Liverpool", "stage": "advanced"}
    assert golden.grade_regression(exp, live_miss)["critical_ok"] is False
    # right fields, but the label itself missing: still a regression
    assert golden.grade_regression(exp, {**exp, "historical": False})["critical_ok"] is False


@pytest.mark.skipif(
    not (os.environ.get("NVIDIA_API_KEY") and os.environ.get("TM_LLM_TESTS") == "1"),
    reason="set NVIDIA_API_KEY and TM_LLM_TESTS=1 to run the live NIM regression cases")
def test_live_engine_labels_the_regression_cases():
    """Runs in the gated live eval next to the main corpus (golden-eval.yml). Every
    regression must pass outright: each is a bug that already reached the live site."""
    failed = []
    for c in golden.load_regressions():
        out = engine_run.analyze(c["input"])
        g = golden.grade_regression(c["expected"], out)
        bad = [f for f, ok in g["fields"].items() if not ok]
        print(f"{'PASS' if g['critical_ok'] else 'FAIL'} {c['id']:34} "
              f"{'' if g['critical_ok'] else 'miss=' + ','.join(bad)}  -> {out}")
        if not g["critical_ok"]:
            failed.append(c["id"])
    assert not failed, f"regressions failing: {failed}"


# --- fresh-context review findings (2026-09-27) --------------------------------------
# Every input below is verbatim from the review, which CONFIRMED each one was wrongly
# flagged: a current rumour that merely mentions an old near-miss in a side clause.

REVIEW_FALSE_POSITIVES = [
    # (a) the dated near-miss sits in a relative clause; the headline is live
    "Chelsea, who nearly signed Michael Olise in 2023, have returned for the winger",
    "Chelsea, who nearly signed Michael Olise in 2023, make £60m bid for Bayern winger",
    "Chelsea, who nearly signed Michael Olise in 2023, open talks with Bayern",
    "Liverpool reignite interest in Adam Wharton, who they almost signed in 2023",
    "Arsenal return for Martin Zubimendi after he nearly joined them in 2024",
    "Man Utd, who were close to signing Jobe Bellingham as a teenager, lodge bid for Dortmund midfielder",
    "Tottenham step up pursuit of Eze, who they were close to signing in 2023",
    "Juventus in talks for Jadon Sancho, who was close to joining them in 2021",
    "Aston Villa target Joao Felix, who almost joined them in January 2024",
    "Barcelona eye move for Nico Williams, who nearly joined in summer 2024",
    "Newcastle held talks over James Trafford in 2023 and have returned with a £30m bid",
    "Sesko, who Arsenal held talks over in 2024, is a January target for the Gunners",
    "Everton make approach for Dwight McNeil two years after he almost signed in 2024",
    "Man City, who could have signed Jude Bellingham in 2020, make Wharton top target",
    "PSG hijack: Paris Saint-Germain move for Kvaratskhelia who nearly joined Newcastle in 2022",
    "Bayern Munich, who were close to signing Florian Wirtz in 2023-24, submit €120m offer",
    "Napoli target Rasmus Hojlund, who was close to joining them years ago",
    "Fulham agree £27m fee for Emile Smith Rowe, who was close to joining Newcastle in 2023",
    # (b) "have held talks" is the live verb
    "Newcastle United have held talks over a move for Marc Guehi, who they came close to signing in 2022. (Telegraph)",
    "Arsenal, who came close to signing Dominik Szoboszlai in 2019, have held talks with Liverpool over a January move",
    "Liverpool have held talks with Marc Guehi's representatives. The Reds came close to signing him in 2022.",
    # (c) completed signings -- the highest-value claims of all
    "Here we go: Liverpool sign Ekitike, who was close to joining PSG in 2022",
    "Sunderland sign Granit Xhaka, who was close to joining Bayer Leverkusen in 2023",
    "Everton confirm signing of Jack Grealish, who nearly joined Man Utd in 2019",
    "Aston Villa, who were close to signing Harvey Elliott in 2019, sign him on loan from Liverpool",
    "Man Utd, who held talks with Leverkusen over Frimpong in 2023, complete his signing",
    # (d) the old near-miss is a second segment after a dash / pipe
    "Chelsea make Olise bid - they nearly signed him in 2023",
    '"We were close to signing him in 2024" - Arteta on Arsenal\'s renewed pursuit of Zubimendi',
    "Toney admits he was close to joining Chelsea in 2023 | Brentford striker open to Saudi move",
    # (e) a time-first sentence running across a comma
    "In 2024 Chelsea signed Pedro Neto from Wolves, now they've opened talks for Rodrygo",
]


@pytest.mark.parametrize("text", REVIEW_FALSE_POSITIVES)
def test_review_false_positives_stay_live(retro, text):
    assert not retro.is_retrospective(text, TODAY), f"FALSE POSITIVE: {text!r}"


def test_the_year_is_judged_against_the_claims_own_date(retro):
    """Review finding: read-time callers passed the BUILD date, so a live 2026 story
    about a 2025 near-miss flipped to 'history' on 2027-01-01 and got pruned from the
    committed ledger. The story is judged as of when it ran."""
    text = "Liverpool almost signed him in 2025"
    assert not retro.is_retrospective(text, date(2026, 9, 20))
    assert retro.is_retrospective(text, date(2027, 1, 1))      # the flip the review found
    conn = store.connect(":memory:")
    _store_claim(conn, _post("http://x/g", text), "Marc Guehi", "Liverpool", "talks", 0.35,
                 "2026-09-20")
    claims = store.claims_for_deal(conn, cluster.deal_key("Marc Guehi", WIN))
    assert build_feed.live_claim_filter(conn, today=date(2027, 1, 1))(claims[0])
    live, historical = bridge._split_retro(conn, {"k": claims})
    assert "k" in live and not historical


def test_pipeline_logs_a_retrospective_drop_by_title(capsys):
    conn = store.connect(":memory:")
    pipeline.run(conn, sources_fn=lambda: [_mbappe_post()], analyze_fn=_claims_everything,
                 window=WIN)
    assert MBAPPE_TITLE in capsys.readouterr().out     # the audit trail is the CI log


def test_pipeline_does_not_read_the_string_false_as_historical():
    post = _post("http://x/live", "Liverpool in talks to sign Marc Guehi")
    conn = store.connect(":memory:")
    stats = pipeline.run(conn, sources_fn=lambda: [post],
                         analyze_fn=lambda t: {**_claims_everything(t), "historical": "false"},
                         window=WIN)
    assert stats["claims"] == 1


def test_an_auto_historical_deal_reopens_when_a_live_claim_arrives(tmp_path):
    """Review finding: a machine-relabelled historical row was terminal -- a later live
    claim attached to it and could never resolve or score. A live claim now hands the
    row back to the resolver (unknown), the way refresh_and_reopen undoes a stale
    collapse: it only removes an assertion, never writes an outcome."""
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [{"deal_id": "458", "player": "Kylian Mbappe", "to_club": "Liverpool",
                              "window": WIN, "outcome": "historical", "verified": "auto",
                              "outcome_date": "2026-09-27", "notes": "[auto] historical: ..."}])
    conn = store.connect(":memory:")
    _store_mbappe(conn)
    _store_claim(conn, _post("http://x/ars", "Arsenal in advanced talks to sign Kylian Mbappe"),
                 "Kylian Mbappe", "Arsenal", "advanced", 0.60, "2026-09-26", from_club="Real Madrid")
    stats = bridge.bridge(conn, deals_path=p)
    row = _read(p)[0]
    assert row["outcome"] == "unknown" and row["verified"] == "auto" and not row["outcome_date"]
    assert [did for did, _ in stats["reopened"]] == ["458"]


def test_a_curated_historical_deal_is_never_reopened(tmp_path):
    curated = {"deal_id": "458", "player": "Kylian Mbappe", "to_club": "Liverpool", "window": WIN,
               "outcome": "historical", "verified": "YES", "notes": NOTE}
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [curated])
    conn = store.connect(":memory:")
    _store_claim(conn, _post("http://x/ars", "Arsenal in advanced talks to sign Kylian Mbappe"),
                 "Kylian Mbappe", "Arsenal", "advanced", 0.60, "2026-09-26")
    bridge.bridge(conn, deals_path=p)
    assert _read(p) == [{k: curated.get(k, "") for k in DEALS_HEADER}]


def test_relabel_keeps_the_resolvers_collapse_evidence(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [{"deal_id": "459", "player": "Wayne Rooney", "to_club": "Newcastle United",
                              "window": WIN, "outcome": "collapsed", "verified": "auto",
                              "outcome_date": "2026-09-03",
                              "outcome_source_url": "https://en.wikipedia.org/wiki/Wayne_Rooney",
                              "notes": "[auto] window closed; player stayed at Everton"}])
    conn = store.connect(":memory:")
    _store_claim(conn, _post("http://x/rooney", "Rooney nearly signed for Newcastle as a teenager",
                             "BBC Sport"), "Wayne Rooney", "Newcastle United", "advanced", 0.60,
                 "2026-09-22")
    bridge.bridge(conn, deals_path=p)
    row = _read(p)[0]
    assert row["outcome"] == "historical"
    assert "window closed; player stayed at Everton" in row["notes"]
    assert "2026-09-03" in row["notes"] and "wikipedia.org/wiki/Wayne_Rooney" in row["notes"]


def test_merge_refuses_a_pair_with_a_historical_row(tmp_path):
    """Review finding: merge_split_deals ignored `historical` -- it deleted a live bare
    twin into a historical row, or silently deleted a historical row."""
    dp, cp = tmp_path / "deals.csv", tmp_path / "claims.csv"
    _write(dp, DEALS_HEADER, [
        {"deal_id": "910", "player": "Michael Olise", "to_club": "Chelsea", "window": WIN,
         "outcome": "historical", "verified": "auto"},
        {"deal_id": "911", "player": "Olise", "to_club": "Chelsea", "window": WIN,
         "outcome": "unknown", "verified": "auto"},
    ])
    _write(cp, CLAIMS_HEADER, [{"claim_id": "7000", "deal_id": "911", "source_name": "Sky Sports",
                                "stage": "talks", "claim_date": "2026-09-20", "verified": "auto"}])
    stats = bridge.merge_split_deals(deals_path=dp, claims_path=cp)
    assert stats["merged"] == [] and len(stats["refused"]) == 1
    assert [r["deal_id"] for r in _read(dp)] == ["910", "911"]
    assert _read(cp)[0]["deal_id"] == "911"


def test_promote_only_accepts_deals_in_the_review_queue(tmp_path):
    """Review finding (predates the branch): promote(ids) flipped ANY auto deal to YES,
    including a historical one -- its claims then became unprunable, one outcome edit
    away from scoring."""
    dp, cp = tmp_path / "deals.csv", tmp_path / "claims.csv"
    _write(dp, DEALS_HEADER, [{**HISTORICAL_ROW, "verified": "auto"}])
    _write(cp, CLAIMS_HEADER, [{"claim_id": "995", "deal_id": "458", "source_name": "BBC Sport",
                                "stage": "advanced", "claim_date": "2026-09-22", "verified": "auto"}])
    promoted, n_claims = promote.promote(["458"], deals_path=dp, claims_path=cp, rebuild=False,
                                         fixtures_path=tmp_path / "res.json")
    assert promoted == [] and n_claims == 0
    assert _read(dp)[0]["verified"] == "auto" and _read(cp)[0]["verified"] == "auto"


def test_rail_lists_only_hand_verified_historical_deals(tmp_path):
    """Review finding: machine relabels sorted to the top of the 6-slot rail and could
    push real results off it. Only a human-verified correction earns a rail slot."""
    p = tmp_path / "deals.csv"
    _write(p, DEALS_HEADER, [
        {"deal_id": "458", "player": "Kylian Mbappe", "window": WIN, "outcome": "historical",
         "verified": "YES", "outcome_date": "2026-09-27", "notes": NOTE},
        {"deal_id": "459", "player": "Wayne Rooney", "window": WIN, "outcome": "historical",
         "verified": "auto", "outcome_date": "2026-09-27"},
    ])
    _keys, done = build_feed.load_resolved(p)
    assert [r["deal_id"] for r in done] == ["458"]
