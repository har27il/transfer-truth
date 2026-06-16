#!/usr/bin/env python3
"""
Apply resolved outcomes back into deals.csv — the step that closes the loop.

For every deal still marked `unknown`, resolve what the player actually did
(source.resolve -> detect.classify) and, ONLY when the result is a positive
completed/collapsed, write it back. Ambiguous deals are left untouched.

Safety (from the plan's D6 + D-safety):
  - Atomic write: a temp file is fully written then os.replace()'d over deals.csv,
    so a crash mid-write can never truncate the ground truth.
  - Positive-evidence only: 'unknown' results are never written.
  - Auto rows are marked `verified=auto` (vs hand-checked `YES`) so they stay
    distinguishable and auditable.
  - Idempotent: a run that resolves nothing rewrites nothing.

Usage:
    python outcome/apply.py            # resolve unknowns, write, then rescore+rebuild
    python outcome/apply.py --dry-run  # show what WOULD change, write nothing
"""
import csv
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outcome import source
from outcome.detect import classify, COMPLETED, COLLAPSED

DEALS = ROOT / "ground-truth" / "deals.csv"
RESOLVED = (COMPLETED, COLLAPSED)


def _default_resolver(row):
    return source.resolve(row["player"], row["window"], from_club=row.get("from_club"))


def load_deals(path=DEALS):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def write_atomic(path, fieldnames, rows):
    """Write rows to a temp file in the same dir, then atomically replace path."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".deals.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)  # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def resolve_unknowns(rows, resolver=_default_resolver):
    """Mutate rows in place for any newly-resolved deal. Returns list of changes."""
    changes = []
    for r in rows:
        if (r.get("outcome") or "").strip().lower() not in ("", "unknown"):
            continue
        res = resolver(r)
        outcome, reason = classify(r, res)
        if outcome not in RESOLVED:
            continue  # D-safety: never write an unresolved outcome
        r["outcome"] = outcome
        r["verified"] = "auto"
        r["outcome_date"] = date.today().isoformat()
        r["outcome_source_url"] = "https://en.wikipedia.org/wiki/" + r["player"].replace(" ", "_")
        ev = (res.get("evidence") or "").strip()
        r["notes"] = f"[auto] {reason}" + (f" | {ev}" if ev else "")
        changes.append((r["deal_id"], r["player"], outcome, reason))
    return changes


def rescore_and_rebuild():
    """Re-run the scorer + static site so the leaderboard reflects new outcomes."""
    import subprocess
    py = sys.executable
    subprocess.run([py, str(ROOT / "scoring" / "score.py"),
                    str(ROOT / "ground-truth" / "journalist_claims.csv")], check=True)
    subprocess.run([py, str(ROOT / "site" / "build_leaderboard.py")], check=True)


def apply(deals_path=DEALS, resolver=_default_resolver, dry_run=False, rebuild=True):
    fieldnames, rows = load_deals(deals_path)
    changes = resolve_unknowns(rows, resolver)
    if not changes:
        print("No unknown deals resolved - nothing to write.")
        return changes
    print(f"Resolved {len(changes)} deal(s):")
    for did, player, outcome, reason in changes:
        print(f"  deal {did} {player}: {outcome}  ({reason})")
    if dry_run:
        print("\n--dry-run: deals.csv NOT modified.")
        return changes
    write_atomic(deals_path, fieldnames, rows)
    print(f"\nWrote {Path(deals_path).name} atomically.")
    print(f"NOTE: {len(changes)} row(s) written as verified=auto. These are PROPOSED "
          f"and do NOT affect scores yet.\nReview them, set verified=YES to trust them, "
          f"or preview with: python scoring/score.py <claims.csv> --include-auto")
    if rebuild:
        try:
            rescore_and_rebuild()
        except Exception as e:
            # deals.csv is already safely updated; only the derived artifacts are
            # stale. Don't mask the successful write — tell the user how to finish.
            print(f"\nWARNING: rescore/rebuild failed ({e}).\n"
                  f"deals.csv IS updated. Finish manually:\n"
                  f"  python scoring/score.py ground-truth/journalist_claims.csv\n"
                  f"  python site/build_leaderboard.py")
    return changes


if __name__ == "__main__":
    apply(dry_run="--dry-run" in sys.argv)
