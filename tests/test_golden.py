"""Golden-set tests for the ingestion engine.

Offline (always run): prove the corpus is internally consistent and that the GRADER
actually catches regressions — wrong club, wrong claim strength, hallucinated
player, broken JSON contract. This is the real guard: edit the prompt, run the live
eval, and the grader tells you if extraction quality dropped.

Live (gated on NVIDIA_API_KEY + TM_LLM_TESTS=1): run the real engine over all 15
posts via NVIDIA NIM and assert the critical pass-rate clears a floor.
"""
import copy
import os

import pytest

from stagemap import STAGE_P
from engine import golden
from engine import run as engine_run

CASES = golden.load_cases()


def test_corpus_is_well_formed():
    assert len(CASES) == 15
    ids = [c["id"] for c in CASES]
    assert len(set(ids)) == len(ids), "duplicate case ids"
    for c in CASES:
        assert c["input"].strip()
        exp = c["expected"]
        for f in golden.CRITICAL:
            assert f in exp, f"{c['id']} missing expected.{f}"
        assert exp["stage"] is None or exp["stage"] in STAGE_P, f"{c['id']} bad stage"
        # a non-transfer post must not assert a stage or clubs
        if not exp["is_transfer_claim"]:
            assert exp["stage"] is None and exp["to_club"] is None


def test_expected_labels_grade_themselves_as_correct():
    """If the engine returned exactly our labels, every case must pass — otherwise
    the corpus/grader disagree with each other."""
    summary = golden.run_eval(lambda post: next(c["expected"] for c in CASES if c["input"] == post))
    assert summary["pass_rate"] == 1.0
    assert summary["field_accuracy"] == 1.0


def test_grader_tolerates_equivalent_stage_and_club_nicknames():
    exp = next(c["expected"] for c in CASES if c["id"] == "isak-hwg")
    out = copy.deepcopy(exp)
    out["stage"] = "official"            # here_we_go == official (same implied_p)
    out["from_club"] = "Newcastle"       # nickname of "Newcastle United"
    out["to_club"] = "Liverpool FC"      # FC suffix
    assert STAGE_P["here_we_go"] == STAGE_P["official"]
    assert golden.grade(exp, out)["critical_ok"] is True


@pytest.mark.parametrize("field,bad", [
    ("is_transfer_claim", False),
    ("to_club", "Chelsea"),              # wrong destination
    ("from_club", "Everton"),            # wrong origin
    ("player", "Cole Palmer"),           # hallucinated player
    ("stage", "talks"),                  # 0.35 vs 0.99 — strength wrong
    ("source_identifiable", False),
    ("multi_claim", True),
])
def test_grader_catches_each_critical_regression(field, bad):
    exp = next(c["expected"] for c in CASES if c["id"] == "isak-hwg")
    out = copy.deepcopy(exp)
    out[field] = bad
    g = golden.grade(exp, out)
    assert g["fields"][field] is False
    assert g["critical_ok"] is False


def test_grader_fails_on_broken_json_contract():
    exp = next(c["expected"] for c in CASES if c["id"] == "isak-hwg")
    g = golden.grade(exp, None)          # engine returned non-JSON
    assert g["critical_ok"] is False
    assert all(v is False for v in g["fields"].values())


def test_fee_tolerance_band():
    assert golden._fee_match(100_000_000, 120_000_000)   # within 30%
    assert not golden._fee_match(100_000_000, 150_000_000)  # outside
    assert golden._fee_match(0, 0)                        # free transfer exact
    assert not golden._fee_match(0, 5_000_000)
    assert golden._fee_match(None, None)


def test_stage_equivalence_classes():
    assert golden._stage_match("here_we_go", "official")
    assert golden._stage_match("rumour_link", "interest")
    assert not golden._stage_match("talks", "here_we_go")
    assert golden._stage_match(None, None)
    assert not golden._stage_match(None, "talks")


@pytest.mark.skipif(
    not (os.environ.get("NVIDIA_API_KEY") and os.environ.get("TM_LLM_TESTS") == "1"),
    reason="set NVIDIA_API_KEY and TM_LLM_TESTS=1 to run the live NIM engine eval")
def test_live_engine_eval_clears_floor():
    summary = golden.run_eval(engine_run.analyze)
    for cid, g, out in summary["results"]:
        bad = [f for f, ok in g["fields"].items() if not ok]
        print(f"{'PASS' if g['critical_ok'] else 'FAIL'} {cid:24} "
              f"{'' if g['critical_ok'] else 'miss=' + ','.join(bad)}")
    print(f"\npass_rate={summary['pass_rate']:.0%} field_accuracy={summary['field_accuracy']:.0%}")
    assert summary["pass_rate"] >= 0.80
    assert summary["field_accuracy"] >= 0.90
