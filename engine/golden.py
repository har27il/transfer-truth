#!/usr/bin/env python3
"""
Golden-set grader for the ingestion engine.

Compares an engine output object against an expected label with TOLERANT matching,
so the test guards real regressions (wrong club, wrong claim strength, hallucinated
player) without failing on harmless surface variation:

  - stage: two stages match if they share the same implied_p (here_we_go == official,
    rumour_link == interest). Drives the Brier score, so the *strength* must be right,
    not the exact word.
  - clubs: matched via detect.same_club (so "Man Utd" == "Manchester United"); null
    must match null.
  - player: accent/case-insensitive.
  - fee_eur: soft — within +/-30% (or exact 0 for free); reported, not critical.

CRITICAL fields (must all pass) are the ones that feed scoring + clustering:
is_transfer_claim, player, from_club, to_club, stage, source_identifiable, multi_claim.
"""
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stagemap import STAGE_P
from outcome.detect import same_club

CASES = ROOT / "tests" / "golden" / "cases.jsonl"
CRITICAL = ("is_transfer_claim", "player", "from_club", "to_club",
            "stage", "source_identifiable", "multi_claim")


def load_cases(path=CASES):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _norm_name(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _stage_match(exp, got):
    if exp is None or got is None:
        return exp == got
    if exp == got:
        return True
    # same implied probability => same claim strength => equivalent for scoring
    return STAGE_P.get(exp) is not None and STAGE_P.get(exp) == STAGE_P.get(got)


def _club_match(exp, got):
    if exp is None or got is None:
        return (exp is None) == (got is None)
    return same_club(exp, got)


def _fee_match(exp, got):
    if exp is None:
        return got is None
    if exp == 0:
        return got == 0
    if not isinstance(got, (int, float)):
        return False
    return abs(got - exp) <= 0.30 * exp


def grade_field(name, exp, got):
    if name == "stage":
        return _stage_match(exp, got)
    if name in ("from_club", "to_club"):
        return _club_match(exp, got)
    if name == "player":
        return _norm_name(exp) == _norm_name(got)
    if name == "fee_eur":
        return _fee_match(exp, got)
    return exp == got  # booleans: is_transfer_claim, source_identifiable, multi_claim


def grade(expected, output):
    """Return {'fields': {name: bool}, 'critical_ok': bool, 'fee_ok': bool}.
    A None output (engine broke the JSON contract) fails every critical field."""
    if output is None:
        return {"fields": {f: False for f in CRITICAL}, "critical_ok": False, "fee_ok": False}
    fields = {f: grade_field(f, expected.get(f), output.get(f)) for f in CRITICAL}
    return {
        "fields": fields,
        "critical_ok": all(fields.values()),
        "fee_ok": _fee_match(expected.get("fee_eur"), output.get("fee_eur")),
    }


def run_eval(analyze_fn, cases=None):
    """Run the engine over every case and summarize. analyze_fn(post) -> dict|None."""
    cases = cases if cases is not None else load_cases()
    results = []
    for c in cases:
        out = analyze_fn(c["input"])
        g = grade(c["expected"], out)
        results.append((c["id"], g, out))
    n = len(results)
    passed = sum(1 for _, g, _ in results if g["critical_ok"])
    field_acc = (sum(v for _, g, _ in results for v in g["fields"].values())
                 / (n * len(CRITICAL))) if n else 0.0
    return {"n": n, "passed": passed, "pass_rate": passed / n if n else 0.0,
            "field_accuracy": field_acc, "results": results}
