"""Guard the DRY refactor (T2): score.py and deal_predictor.py must share STAGE_P."""
import importlib

import stagemap


def test_stagemap_has_expected_stages():
    for s in ("rumour_link", "interest", "talks", "advanced", "agreement",
              "medical", "here_we_go", "official", "denied"):
        assert s in stagemap.STAGE_P
    assert stagemap.STAGE_P["here_we_go"] == stagemap.STAGE_P["official"] == 0.99
    assert stagemap.STAGE_P["denied"] < stagemap.STAGE_P["interest"]


def test_scorer_and_predictor_import_the_same_object():
    score = importlib.import_module("scoring.score")
    predictor = importlib.import_module("ml.deal_predictor")
    # Same dict identity -> guaranteed single source of truth, can't drift.
    assert score.STAGE_P is stagemap.STAGE_P
    assert predictor.STAGE_P is stagemap.STAGE_P
