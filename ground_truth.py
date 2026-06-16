#!/usr/bin/env python3
"""
Single source of truth for WHICH deal outcomes are trusted enough to score/train on.

Security boundary: automated outcome detection (outcome/apply.py) writes rows with
`verified=auto`. Those are PROPOSED facts from one LLM read of one web page — not
yet trusted. The scorer and the ML model must both refuse to count them until a
human promotes them to `verified=YES` (or `--include-auto` is explicitly passed to
preview). Keeping this decision in ONE place means the gate can't drift between
score.py and deal_predictor.py.
"""
import csv

_TRUSTED_FLAGS = {"yes", "y", "true"}


def trusted(row, include_auto=False):
    """Return the outcome ('completed'/'collapsed') if this row should count, else None."""
    outcome = (row.get("outcome") or "").strip().lower()
    if outcome not in ("completed", "collapsed"):
        return None  # unknown / blank — unresolved
    verified = (row.get("verified") or "").strip().lower()
    if verified in _TRUSTED_FLAGS:
        return outcome
    if include_auto:
        return outcome  # caller opted in to previewing unverified auto rows
    return None         # verified=auto (or blank) — proposed, not yet trusted


def load_outcomes(deals_path, include_auto=False):
    """deal_id -> 1.0 (completed) / 0.0 (collapsed) for every TRUSTED row."""
    out = {}
    with open(deals_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            o = trusted(r, include_auto)
            if o is not None:
                out[r["deal_id"].strip()] = 1.0 if o == "completed" else 0.0
    return out
