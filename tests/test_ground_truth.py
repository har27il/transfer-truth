"""The trusted-outcome gate: verified=auto must NOT count until promoted to YES.

This is the security boundary that stops one automated LLM read from silently
moving every journalist's score. Both score.py and deal_predictor.py route through
ground_truth.load_outcomes, so this gate guards both.
"""
import csv

from ground_truth import trusted, load_outcomes

HEADER = ["deal_id", "outcome", "verified"]


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


def test_trusted_gates_on_verified():
    assert trusted({"outcome": "completed", "verified": "YES"}) == "completed"
    assert trusted({"outcome": "collapsed", "verified": "yes"}) == "collapsed"
    # auto rows are proposed, not trusted, by default
    assert trusted({"outcome": "completed", "verified": "auto"}) is None
    assert trusted({"outcome": "completed", "verified": ""}) is None
    # ...but countable when the caller explicitly opts in
    assert trusted({"outcome": "completed", "verified": "auto"}, include_auto=True) == "completed"
    # unresolved never counts regardless of verified
    assert trusted({"outcome": "unknown", "verified": "YES"}) is None


def test_load_outcomes_excludes_auto_by_default(tmp_path):
    p = tmp_path / "deals.csv"
    _write(p, [
        {"deal_id": "1", "outcome": "completed", "verified": "YES"},
        {"deal_id": "2", "outcome": "collapsed", "verified": "auto"},   # proposed
        {"deal_id": "3", "outcome": "unknown", "verified": "YES"},      # unresolved
    ])
    default = load_outcomes(p)
    assert default == {"1": 1.0}                      # only the verified=YES resolved row
    with_auto = load_outcomes(p, include_auto=True)
    assert with_auto == {"1": 1.0, "2": 0.0}          # auto previewed in
