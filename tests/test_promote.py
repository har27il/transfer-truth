"""Promotion CLI tests — the human trust gate over auto-resolved outcomes.

Covers: only completed/collapsed auto deals are reviewable (unknowns and
already-YES rows never surface); promoting flips the deal AND its auto claims
to YES together; unrelated rows are untouched; writes are atomic.
"""
import csv

from outcome import promote

DEALS_HEADER = ["deal_id", "player", "from_club", "to_club", "window", "outcome",
                "fee_eur_actual", "outcome_date", "outcome_source_url", "verified", "notes"]
CLAIMS_HEADER = ["claim_id", "deal_id", "source_name", "platform", "claim_date",
                 "stage", "source_url", "raw_quote", "verified"]


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


DEALS = [
    {"deal_id": "1", "player": "A", "outcome": "completed", "verified": "auto"},
    {"deal_id": "2", "player": "B", "outcome": "unknown", "verified": "auto"},
    {"deal_id": "3", "player": "C", "outcome": "collapsed", "verified": "auto"},
    {"deal_id": "4", "player": "D", "outcome": "completed", "verified": "YES"},
]
CLAIMS = [
    {"claim_id": "1", "deal_id": "1", "source_name": "Sky Sports", "stage": "agreement",
     "claim_date": "2026-06-01", "verified": "auto"},
    {"claim_id": "2", "deal_id": "1", "source_name": "BBC Sport", "stage": "official",
     "claim_date": "2026-06-02", "verified": "auto"},
    {"claim_id": "3", "deal_id": "3", "source_name": "Sky Sports", "stage": "talks",
     "claim_date": "2026-06-01", "verified": "auto"},
    {"claim_id": "4", "deal_id": "4", "source_name": "Fabrizio Romano", "stage": "here_we_go",
     "claim_date": "2026-06-01", "verified": "YES"},
]


def test_pending_shows_only_resolvable_auto_deals():
    todo = promote.pending([dict(r) for r in DEALS])
    assert [d["deal_id"] for d in todo] == ["1", "3"]   # unknown + YES rows hidden


def test_promote_flips_deal_and_its_auto_claims_together(tmp_path):
    dp, cp = tmp_path / "deals.csv", tmp_path / "claims.csv"
    _write(dp, DEALS_HEADER, DEALS)
    _write(cp, CLAIMS_HEADER, CLAIMS)

    promoted, n_claims = promote.promote(["1"], deals_path=dp, claims_path=cp,
                                         rebuild=False,
                                         fixtures_path=tmp_path / "res.json")
    assert promoted == ["1"] and n_claims == 2
    deals = {r["deal_id"]: r for r in _read(dp)}
    assert deals["1"]["verified"] == "YES"
    assert deals["2"]["verified"] == "auto"           # unknown untouched
    assert deals["3"]["verified"] == "auto"           # not approved -> untouched
    claims = {r["claim_id"]: r for r in _read(cp)}
    assert claims["1"]["verified"] == "YES" and claims["2"]["verified"] == "YES"
    assert claims["3"]["verified"] == "auto"          # other deal's claim untouched
    assert claims["4"]["verified"] == "YES"           # already-YES stays


def test_promote_ignores_ids_that_are_not_pending(tmp_path):
    dp, cp = tmp_path / "deals.csv", tmp_path / "claims.csv"
    _write(dp, DEALS_HEADER, DEALS)
    _write(cp, CLAIMS_HEADER, CLAIMS)
    before_d, before_c = _read(dp), _read(cp)

    promoted, n_claims = promote.promote(["4", "99"], deals_path=dp, claims_path=cp,
                                         rebuild=False,
                                         fixtures_path=tmp_path / "res.json")
    assert promoted == [] and n_claims == 0
    assert _read(dp) == before_d and _read(cp) == before_c   # nothing rewritten


def test_promote_writes_fixtures_for_completed_deals(tmp_path):
    """A promotion must leave the repo test-gate-clean: completed deals get a
    reproducibility fixture automatically; collapsed ones are flagged for hand
    fixtures (they need the actual destination, not the rumoured one)."""
    import json
    dp, cp, fp = tmp_path / "deals.csv", tmp_path / "claims.csv", tmp_path / "res.json"
    _write(dp, DEALS_HEADER, [
        {"deal_id": "1", "player": "A Player", "to_club": "Arsenal",
         "outcome": "completed", "verified": "auto",
         "notes": "[auto] joined the rumoured club | joined Arsenal on 1 July 2026"},
        {"deal_id": "3", "player": "C Player", "outcome": "collapsed", "verified": "auto"},
    ])
    _write(cp, CLAIMS_HEADER, [])
    fp.write_text("{}", encoding="utf-8")

    promoted, _ = promote.promote(["1", "3"], deals_path=dp, claims_path=cp,
                                  rebuild=False, fixtures_path=fp)
    assert sorted(promoted) == ["1", "3"]
    res = json.loads(fp.read_text("utf-8"))
    assert res["A Player"] == {"status": "moved", "joined_club": "Arsenal",
                               "evidence": "joined Arsenal on 1 July 2026"}
    assert "C Player" not in res          # collapsed -> flagged, never guessed


# --- stale-evidence warning in the review display (TODOS 0a; RESEARCH.md) -------------
# Display only: it changes nothing about what gets promoted. 31 of the 60 collapse
# proposals queued on 2026-09-27 cited only pre-2026 years for a 2026-summer rumour.

def _proposal(notes, window="2026-summer"):
    return {"deal_id": "418", "player": "Harvey Elliott", "from_club": "Liverpool",
            "to_club": "Valencia", "window": window, "outcome": "collapsed",
            "verified": "auto", "notes": notes}


def test_review_warns_when_evidence_predates_the_window():
    deal = _proposal("[auto] player joined Aston Villa, not Valencia - rumour did not happen"
                     " | On 1 September 2025, Elliott joined Aston Villa on loan.")
    out = promote._describe(deal, [])
    assert "WARNING" in out and "2025" in out and "2026-summer" in out


def test_review_is_quiet_when_evidence_is_in_the_window_or_undated():
    in_window = _proposal("[auto] ... | On 30 August 2026, he signed for Aston Villa.")
    undated = _proposal("[auto] window closed; player stayed at Liverpool | He remained.")
    assert "WARNING" not in promote._describe(in_window, [])
    assert "WARNING" not in promote._describe(undated, [])
