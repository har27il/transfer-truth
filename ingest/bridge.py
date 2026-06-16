#!/usr/bin/env python3
"""
Bridge: turn ingested player clusters into deals.csv rows the outcome workflow can resolve.

This closes the loop. Ingestion (Phase 3) fills the SQLite store with claims clustered
by player+window. Each NEW cluster that isn't already a deal becomes a deals.csv row
with outcome=unknown / verified=auto — a PROPOSED deal. The Phase 2 outcomes workflow
then resolves it (Wikipedia -> completed/collapsed), and once promoted to verified=YES
it scores. Rumor in -> deal created -> outcome resolved -> leaderboard updates.

Posture (matches the rest of the project):
  - DRY: reuses cluster.deal_key (clustering), outcome.apply.{load_deals,write_atomic}
    (atomic CSV I/O) and the verified-gate convention (ground_truth.py).
  - Ground-truth safety: only ADDS proposed rows; never edits a curated row, never sets
    a real outcome. to_club is best-effort and explicitly marked provisional.
  - Idempotent: a cluster already represented in deals.csv (by player+window key)
    attaches to it instead of spawning a duplicate.
  - Atomic: same temp-file + os.replace write as outcome/apply.py.
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import store, cluster
from outcome.apply import DEALS, load_deals, write_atomic


def _provisional(claims, field):
    """Most common non-empty value of `field`; ties broken by the most recent claim."""
    vals = [((c.get("claim_date") or ""), c[field]) for c in claims if c.get(field)]
    if not vals:
        return ""
    counts = Counter(v for _, v in vals)
    top = max(counts.values())
    winners = {v for v, n in counts.items() if n == top}
    for _, v in sorted(vals, reverse=True):   # most recent claim first
        if v in winners:
            return v
    return next(iter(winners))


def _existing_keys(rows):
    """Map deal_key -> deal row for every existing deal that has a player+window."""
    out = {}
    for r in rows:
        k = cluster.deal_key(r.get("player", ""), r.get("window", ""))
        if k:
            out.setdefault(k, r)
    return out


def _next_id(rows):
    return max((int(r["deal_id"]) for r in rows if str(r.get("deal_id", "")).isdigit()),
               default=0)


def bridge(conn, deals_path=DEALS, dry_run=False):
    """Create deals.csv rows for ingested clusters not yet represented. Returns stats."""
    fieldnames, rows = load_deals(deals_path)
    existing = _existing_keys(rows)
    next_id = _next_id(rows)

    created, attached = [], []
    for key in store.deal_keys(conn):
        if key in existing:
            attached.append(key)            # already a deal (curated or previously bridged)
            continue
        claims = store.claims_for_deal(conn, key)
        if not claims:
            continue
        window = key.split("|", 1)[1] if "|" in key else ""
        next_id += 1
        row = {fn: "" for fn in fieldnames}
        row.update({
            "deal_id": str(next_id),
            "player": _provisional(claims, "player"),
            "from_club": _provisional(claims, "from_club"),
            "to_club": _provisional(claims, "to_club"),
            "window": window,
            "outcome": "unknown",
            "verified": "auto",
            "notes": f"[auto-ingested] {len(claims)} claim(s); to_club provisional",
        })
        rows.append(row)
        existing[key] = row                 # guard against dup keys within one run
        created.append(row)

    if created and not dry_run:
        write_atomic(deals_path, fieldnames, rows)
    return {"created": created, "attached": attached}


def _print(stats, dry_run):
    if not stats["created"]:
        print(f"No new deals. {len(stats['attached'])} cluster(s) already represented.")
        return
    print(f"{'Would create' if dry_run else 'Created'} {len(stats['created'])} proposed deal(s) "
          f"(outcome=unknown, verified=auto):")
    for r in stats["created"]:
        print(f"  deal {r['deal_id']}: {r['player']} -> {r['to_club'] or '?'} ({r['window']})")
    print(f"{len(stats['attached'])} cluster(s) attached to existing deals.")
    if dry_run:
        print("\n--dry-run: deals.csv NOT modified.")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    conn = store.connect()
    _print(bridge(conn, dry_run=dry), dry)
