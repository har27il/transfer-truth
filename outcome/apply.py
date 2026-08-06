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
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outcome import source
from outcome.detect import classify, COMPLETED, COLLAPSED

DEALS = ROOT / "ground-truth" / "deals.csv"
RESOLVED = (COMPLETED, COLLAPSED)
# Worker cap for the resolve fan-out. Env-overridable so the workflow can dial it
# down without a code change if the free tier starts 429ing (same pattern as
# NIM_TIMEOUT / NIM_CONCURRENCY / WIKI_MIN_INTERVAL elsewhere in this repo).
RESOLVE_CONCURRENCY = int(os.environ.get("RESOLVE_CONCURRENCY", "4"))


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


def _record(row, res, changes, i, total):
    """Classify one resolution and, if positive, write it into `row`.

    Called ONLY from the sequential gather loop, never from a worker -- every
    mutation of `rows` and every append to `changes` happens on one thread.
    """
    outcome, reason = classify(row, res)
    # Flushed per-deal progress. Without this the step printed NOTHING until the whole
    # loop finished: a 27-minute run on 2026-08-06 emitted zero log lines, which is why
    # the timeout that froze the site for 20 hours was invisible while it happened.
    print(f"  [{i}/{total}] {row.get('player')} -> {outcome}", flush=True)
    if outcome not in RESOLVED:
        return  # D-safety: never write an unresolved outcome
    # A blank-destination departure that completes (player left, no club was
    # rumoured) has no to_club to display. Record the club Wikipedia confirms he
    # joined so the feed can render "-> Real Madrid" instead of "-> ?". Still
    # positive-evidence-only: we only write the destination the resolver verified.
    if outcome == COMPLETED and not (row.get("to_club") or "").strip() and res.get("joined_club"):
        row["to_club"] = res["joined_club"]
    row["outcome"] = outcome
    row["verified"] = "auto"
    row["outcome_date"] = date.today().isoformat()
    row["outcome_source_url"] = "https://en.wikipedia.org/wiki/" + row["player"].replace(" ", "_")
    ev = (res.get("evidence") or "").strip()
    row["notes"] = f"[auto] {reason}" + (f" | {ev}" if ev else "")
    changes.append((row["deal_id"], row["player"], outcome, reason))


def resolve_unknowns(rows, resolver=_default_resolver, concurrency=None):
    """Mutate rows in place for any newly-resolved deal. Returns list of changes.

    The resolver call is network+LLM bound (~12-18s per deal, dominated by the NIM
    call), and on 2026-08-06 a sequential pass over ~95 unknowns blew the workflow's
    time budget three runs straight. So the CALLS fan out:

        pending ──┬─> worker: resolver(row) ─┐
                  ├─> worker: resolver(row) ─┤   (network/LLM only)
                  ├─> worker: resolver(row) ─┤
                  └─> worker: resolver(row) ─┘
                                             │
                    gather in SUBMISSION order, main thread only
                                             ▼
                            _record(): classify -> mutate row -> append change

    Results are consumed in submission order, so `changes` ordering and every row
    mutation are byte-identical to the sequential path regardless of which resolve
    finishes first. Nothing touches `rows` off the main thread.

    Structurally this mirrors ingest/pipeline.py:104-131 (the NIM extraction fan-out).
    Deliberately duplicated rather than extracted into a shared helper: the failure
    bodies genuinely differ (pipeline calls store.release_failed_post to re-queue the
    post; here a failed deal simply stays `unknown` and is retried next run), and two
    call sites is under the rule of three. Revisit if a third fan-out appears.

    concurrency: worker cap. None -> RESOLVE_CONCURRENCY env (default 4). 4 is not a
    guess -- the backfill step already runs NIM_CONCURRENCY=4 against the same
    free-tier reasoning endpoint, and 6 workers 429-stormed it (see update-site.yml).
    """
    pending = [r for r in rows
               if (r.get("outcome") or "").strip().lower() in ("", "unknown")]
    if not pending:
        return []
    n = RESOLVE_CONCURRENCY if concurrency is None else concurrency
    workers = max(1, min(n, len(pending)))
    total = len(pending)
    print(f"Resolving {total} unknown deal(s) with {workers} worker(s)...", flush=True)

    changes = []
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(resolver, r) for r in pending]
            for i, (r, fut) in enumerate(zip(pending, futures), 1):
                try:
                    res = fut.result()
                except Exception as e:  # one deal's network/API failure != abort the batch
                    print(f"  ! resolve failed for deal {r.get('deal_id')} {r.get('player')}: {e}",
                          flush=True)
                    continue
                _record(r, res, changes, i, total)
    else:
        for i, r in enumerate(pending, 1):
            try:
                res = resolver(r)
            except Exception as e:
                print(f"  ! resolve failed for deal {r.get('deal_id')} {r.get('player')}: {e}",
                      flush=True)
                continue
            _record(r, res, changes, i, total)
    return changes


def rescore_and_rebuild():
    """Re-run the scorer + static site so the leaderboard reflects new outcomes."""
    import subprocess
    py = sys.executable
    subprocess.run([py, str(ROOT / "scoring" / "score.py"),
                    str(ROOT / "ground-truth" / "journalist_claims.csv")], check=True)
    subprocess.run([py, str(ROOT / "site" / "build_leaderboard.py")], check=True)


def apply(deals_path=DEALS, resolver=_default_resolver, dry_run=False, rebuild=True,
          concurrency=None):
    fieldnames, rows = load_deals(deals_path)
    changes = resolve_unknowns(rows, resolver, concurrency=concurrency)
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
