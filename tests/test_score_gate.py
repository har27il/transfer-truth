"""Claim-level verified gate in the scorer (outside-voice finding, July 2026).

score.py used to gate ONLY on the deal's verified flag: once a deal was
promoted, every claim on it scored — including bridge-appended verified=auto
rows whose stage is an unreviewed LLM extraction ("agreement" vs "talks" is a
0.80-vs-0.35 Brier swing). The gate makes auto claims invisible to scoring
until outcome/promote.py flips them to YES with their deal.
"""
import importlib

import scoring.score as score_mod


CSV = """claim_id,deal_id,source_name,platform,claim_date,stage,source_url,raw_quote,verified
1,10,Fabrizio Romano,twitter,2026-06-01,here_we_go,http://x/1,hwg,YES
2,10,Sky Sports,rss,2026-06-02,agreement,http://x/2,agreed,auto
3,10,BBC Sport,news_site,2026-06-03,talks,http://x/3,talks,
"""

DEALS = {"10": 1.0}   # deal 10 completed and is trusted


def _claims_with(tmp_path, monkeypatch, include_auto):
    p = tmp_path / "claims.csv"
    p.write_text(CSV, encoding="utf-8")
    monkeypatch.setattr(score_mod, "CLAIMS", p)
    monkeypatch.setattr(score_mod, "INCLUDE_AUTO", include_auto)
    return score_mod.load_claims(DEALS)


def test_auto_claims_do_not_score_by_default(tmp_path, monkeypatch):
    claims = _claims_with(tmp_path, monkeypatch, include_auto=False)
    srcs = {c["source"] for c in claims}
    assert srcs == {"Fabrizio Romano", "BBC Sport"}   # YES + legacy blank score
    assert "Sky Sports" not in srcs                    # auto row gated out


def test_include_auto_previews_auto_claims(tmp_path, monkeypatch):
    claims = _claims_with(tmp_path, monkeypatch, include_auto=True)
    assert {c["source"] for c in claims} == {"Fabrizio Romano", "Sky Sports", "BBC Sport"}


def teardown_module():
    importlib.reload(score_mod)   # restore module-level CLAIMS/INCLUDE_AUTO

# --- the earliness bonus no longer moves the ranked score (RESEARCH.md §4, rec. 5) -----
# The bonus paid only when a HIGH stage was called early on a deal that completed, so
# overstating a claim's stage could only gain: it rewarded exactly the overclaiming the
# Brier score exists to punish. It is still computed and shown; it no longer counts.

def test_the_ranked_score_is_the_shrunk_brier_accuracy_alone():
    from datetime import date
    claims = [
        {"deal_id": "1", "source": "Early Bird", "stage": "here_we_go", "p": 0.99,
         "outcome": 1.0, "date": date(2026, 6, 1)},
        {"deal_id": "1", "source": "Late Owl", "stage": "here_we_go", "p": 0.99,
         "outcome": 1.0, "date": date(2026, 6, 20)},
    ]
    rows, _pop = score_mod.rank_rows(claims)
    early = next(r for r in rows if r["source"] == "Early Bird")
    assert early["earliness_bonus"] > 0                    # still computed and published
    assert early["score"] == early["shrunk"]               # ...but not added to the score
    assert {r["score"] for r in rows} == {rows[0]["shrunk"]}   # same calls, same score
