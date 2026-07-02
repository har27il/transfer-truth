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
from ingest.exclude import is_non_player, is_known_non_player
from outcome.apply import DEALS, load_deals, write_atomic
from stagemap import STAGE_P

CLAIMS_CSV = ROOT / "ground-truth" / "journalist_claims.csv"


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


def _cluster_excluded(conn, claims):
    """True if this cluster's source posts look like a non-player item (manager /
    women's football). Defence in depth behind the pipeline pre-filter: a claim
    already sitting in the store from before the filter existed (e.g. the Derek
    McInnes manager appointment) must not resurrect as a deal. Checks the raw post
    text behind each claim; excludes if ANY source post matches (a player+window
    cluster is single-subject, so one manager post means the whole cluster is one)."""
    for c in claims:
        post = conn.execute("SELECT title, summary FROM posts WHERE url = ?",
                            (c.get("post_url"),)).fetchone()
        if not post:
            continue
        excluded, _why = is_non_player(" ".join(filter(None, [post["title"], post["summary"]])))
        if excluded:
            return True
    return False


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

    created, attached, excluded = [], [], []
    for key in store.deal_keys(conn):
        if key in existing:
            attached.append(key)            # already a deal (curated or previously bridged)
            continue
        claims = store.claims_for_deal(conn, key)
        if not claims:
            continue
        player = _provisional(claims, "player")
        # Two gates: a curated denylist (confirmed managers the text filter can't
        # catch, e.g. McInnes) and the headline text filter (general manager/women
        # signal). Either one keeps a non-player out of the deal ledger for good.
        if is_known_non_player(player) or _cluster_excluded(conn, claims):
            excluded.append(key)            # manager / women's item -> never a player deal
            continue
        window = key.split("|", 1)[1] if "|" in key else ""
        next_id += 1
        row = {fn: "" for fn in fieldnames}
        row.update({
            "deal_id": str(next_id),
            "player": player,
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
    return {"created": created, "attached": attached, "excluded": excluded}


def _load_claims_csv(path):
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def bridge_claims(conn, deals_path=DEALS, claims_path=CLAIMS_CSV, dry_run=False):
    """Append ingested claims for KNOWN deals to journalist_claims.csv as
    verified=auto rows — the feed that keeps the leaderboard alive (the seed
    file was hand-made once and nothing ever added to it, so standings froze).

    Trust posture: appended rows are PROPOSED. score.py skips verified=auto
    claims until outcome/promote.py flips them to YES together with their deal,
    so an unreviewed LLM stage-extraction can never move a public Brier score.

    Dedup is per (deal_id, source, stage) as well as per URL: the same outlet
    re-reporting one deal daily is correlated evidence on one outcome, not many
    independent samples (the curated file was one claim per milestone)."""
    fieldnames, rows = _load_claims_csv(claims_path)
    _, deal_rows = load_deals(deals_path)
    key_to_deal = _existing_keys(deal_rows)

    seen_triple = {(r.get("deal_id", "").strip(),
                    (r.get("source_name") or "").strip().lower(),
                    (r.get("stage") or "").strip().lower()) for r in rows}
    seen_url = {(r.get("source_url") or "").strip() for r in rows if r.get("source_url")}
    next_id = max((int(r["claim_id"]) for r in rows
                   if str(r.get("claim_id", "")).isdigit()), default=0)

    added = []
    for key, deal in key_to_deal.items():
        for c in store.claims_for_deal(conn, key):
            src = (c.get("source_name") or "").strip()
            stage = (c.get("stage") or "").strip().lower()
            url = (c.get("post_url") or "").strip()
            if not src or stage not in STAGE_P or not url:
                continue                      # unattributable / unscoreable claim
            triple = (deal["deal_id"], src.lower(), stage)
            if triple in seen_triple or url in seen_url:
                continue
            post = conn.execute("SELECT title FROM posts WHERE url = ?", (url,)).fetchone()
            next_id += 1
            row = {fn: "" for fn in fieldnames}
            row.update({
                "claim_id": str(next_id),
                "deal_id": deal["deal_id"],
                "source_name": src,
                "platform": "rss",
                "claim_date": (c.get("claim_date") or "").strip(),
                "stage": stage,
                "source_url": url,
                "raw_quote": ((post["title"] if post else "") or "")[:200],
                "verified": "auto",
            })
            rows.append(row)
            seen_triple.add(triple)
            seen_url.add(url)
            added.append(row)

    if added and not dry_run:
        write_atomic(claims_path, fieldnames, rows)
    return added


def _print(stats, dry_run):
    excluded = len(stats.get("excluded", []))
    excl_note = f" {excluded} non-player cluster(s) filtered (manager/women)." if excluded else ""
    if not stats["created"]:
        print(f"No new deals. {len(stats['attached'])} cluster(s) already represented.{excl_note}")
        return
    print(f"{'Would create' if dry_run else 'Created'} {len(stats['created'])} proposed deal(s) "
          f"(outcome=unknown, verified=auto):")
    for r in stats["created"]:
        print(f"  deal {r['deal_id']}: {r['player']} -> {r['to_club'] or '?'} ({r['window']})")
    print(f"{len(stats['attached'])} cluster(s) attached to existing deals.{excl_note}")
    if dry_run:
        print("\n--dry-run: deals.csv NOT modified.")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    conn = store.connect()
    _print(bridge(conn, dry_run=dry), dry)
    added = bridge_claims(conn, dry_run=dry)
    print(f"{'Would append' if dry else 'Appended'} {len(added)} claim(s) to "
          f"journalist_claims.csv (verified=auto; score only after promotion).")
