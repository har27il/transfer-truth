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
import math
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stagemap import STAGE_P
from outcome.detect import same_club

CASES = ROOT / "tests" / "golden" / "cases.jsonl"
# Fixed PRODUCTION misses, one case per bug that reached the live site. Kept apart from
# CASES so the main corpus's pinned size (tests/test_golden.py) and its self-grading test
# stay untouched; the gated live eval runs both files (see .github/workflows/golden-eval.yml).
REGRESSIONS = ROOT / "tests" / "golden" / "regressions.jsonl"
CRITICAL = ("is_transfer_claim", "player", "from_club", "to_club",
            "stage", "source_identifiable", "multi_claim")


def load_cases(path=CASES):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_regressions(path=REGRESSIONS):
    return load_cases(path)


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


def grade_regression(expected, output):
    """grade(), plus the `historical` label when the case sets one (the field postdates
    CRITICAL). A regression must pass outright -- it is a bug that already shipped once.
    A missing `historical` in the output reads as false."""
    g = grade(expected, output)
    if "historical" in expected:
        ok = output is not None and bool(output.get("historical")) == expected["historical"]
        g["fields"]["historical"] = ok
        g["critical_ok"] = g["critical_ok"] and ok
    return g


def wilson(k, n, z=1.96):
    """Wilson score interval for k successes in n trials (Brown, Cai & DasGupta 2001 --
    the recommended binomial interval at small n; see RESEARCH.md §3)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def run_repeated(analyze_fn, cases=None, repeats=3, grade_fn=grade):
    """Run every case `repeats` times. A single run at n=27 cannot separate models: a
    true-75% model clears the 80% floor 30% of the time (RESEARCH.md §3).

    Each case scores the SHARE of its runs that passed. The interval is taken over CASES,
    not runs -- repeats of one case are not independent, so pooling them would overstate
    the confidence. `flaky` lists cases that passed some runs and failed others."""
    cases = cases if cases is not None else load_cases()
    per_case = {}
    for c in cases:
        ok = sum(bool(grade_fn(c["expected"], analyze_fn(c["input"]))["critical_ok"])
                 for _ in range(repeats))
        per_case[c["id"]] = ok / repeats
    n = len(per_case)
    total = sum(per_case.values())
    return {"n_cases": n, "repeats": repeats, "per_case": per_case,
            "pass_rate": total / n if n else 0.0, "ci": wilson(total, n),
            "solid": sum(1 for v in per_case.values() if v == 1.0),
            "flaky": [cid for cid, v in per_case.items() if 0.0 < v < 1.0]}


def paired(a, b):
    """Compare two run_repeated per_case maps case by case (same cases, e.g. two models or
    two prompts). Only cases where they DISAGREE carry information; the p-value is an
    exact two-sided sign test on those. A 1-case gap is p=1.0 -- noise, not a result."""
    a_better = sorted(k for k in a if a[k] > b.get(k, 0.0))
    b_better = sorted(k for k in a if a[k] < b.get(k, 0.0))
    m, lo = len(a_better) + len(b_better), min(len(a_better), len(b_better))
    tail = sum(math.comb(m, i) for i in range(lo + 1)) / 2 ** m if m else 1.0
    return {"a_better": a_better, "b_better": b_better,
            "ties": len(a) - m, "p_value": min(1.0, 2 * tail)}


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


if __name__ == "__main__":
    # Live, repeated eval (needs NVIDIA_API_KEY):  python engine/golden.py [repeats]
    # Costs repeats x (27 + regressions) model calls. Prints per-case shares, flaky cases,
    # the pass rate with its Wilson interval, and the regression cases (which must be 1.0).
    from engine.run import analyze
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    s = run_repeated(analyze, repeats=k)
    for cid, v in s["per_case"].items():
        print(f"{v:5.0%}  {cid}")
    lo, hi = s["ci"]
    print(f"\n{s['n_cases']} cases x {k} runs: pass {s['pass_rate']:.0%} "
          f"(95% CI {lo:.0%}-{hi:.0%}); solid {s['solid']}; flaky {s['flaky'] or 'none'}")
    reg = run_repeated(analyze, load_regressions(), repeats=k, grade_fn=grade_regression)
    print("regressions:", {cid: f"{v:.0%}" for cid, v in reg["per_case"].items()})
