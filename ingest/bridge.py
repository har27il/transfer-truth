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

from ground_truth import _TRUSTED_FLAGS
from ingest import store, cluster
from ingest.exclude import is_non_player, is_known_non_player
from outcome.apply import DEALS, load_deals, write_atomic
from outcome.detect import same_club, collapse_facts
from stagemap import STAGE_P

CLAIMS_CSV = ROOT / "ground-truth" / "journalist_claims.csv"

# Re-opening a wrong collapse hands the deal back to the resolver, which costs one
# Wikipedia fetch + one LLM call each. 15 rows currently qualify; the resolve step
# already ran 29m21s against its 20m budget on 2026-08-06, so drain the backlog over
# a few runs rather than spiking one. Deterministic order, remainder deferred.
MAX_REOPENS = 10


# Moved to cluster.py so the alias layer and the ledger agree on what a cluster's
# provisional player/clubs ARE -- two implementations would be two answers. Kept
# under the old name because the bridge's tests pin majority semantics through it.
_provisional = cluster.provisional


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


def _existing_keys(rows, alias=None):
    """Map deal_key -> deal row for every existing deal that has a player+window.

    alias canonicalizes, so a bare-surname ledger row and its full-name twin resolve
    to the SAME key and the store's split clusters both find a home."""
    alias = alias or {}
    out = {}
    for r in rows:
        k = cluster.deal_key(r.get("player", ""), r.get("window", ""))
        if k:
            out.setdefault(alias.get(k, k), r)
    return out


def _is_curated(row):
    return (row.get("verified") or "").strip().lower() in _TRUSTED_FLAGS


def _group_claims(conn, alias):
    """canonical deal_key -> every claim across the raw keys that fold into it.

    The union matters: after an alias forms, querying only the canonical key would
    silently drop the bare cluster's claims -- including the lone "Real Madrid deny
    interest" that makes the Olise meter honest."""
    return {canon: [c for rk in raw for c in store.claims_for_deal(conn, rk)]
            for canon, raw in cluster.group_keys(store.deal_keys(conn), alias).items()}


def refresh_and_reopen(conn, rows, alias, groups=None):
    """Bring unresolved rows back in line with what sources are NOW saying, and undo
    collapses that rested on a destination since revised. Mutates `rows` in place.

    THE BUG THIS FIXES. to_club was written once, by _provisional, at row creation --
    and bridge() short-circuits on an existing key forever after. Deal 124 (Yan
    Diomande) was created from two 28-29 June claims saying PSG. Nine later claims,
    ending in a stage=official "Real Madrid sign Diomande from RB Leipzig", never
    touched it. The resolver then read Wikipedia CORRECTLY, compared Real Madrid to
    the stale PSG, and collapsed a transfer that had actually completed -- and the
    feed printed "stayed put" next to notes saying he'd signed for Real Madrid.

    Positive-evidence-only is preserved: nothing here writes an outcome. A re-open
    only REMOVES an unsupported assertion and hands the row back to outcome/apply.py,
    which stays the sole writer of completed/collapsed and still needs Wikipedia to
    say so. Bridge is workflow step 6 and apply is step 7, so the re-open and its
    re-resolution land in the SAME run.
    """
    if groups is None:
        groups = _group_claims(conn, alias)
    stats = {"refreshed": [], "reopened": [], "deferred": []}

    reopen_candidates = []
    for r in rows:
        # Gate A: a curated row is never machine-edited. Test the trusted SET, not
        # == "auto" -- verified also accepts y/true, which an == test would let past.
        if _is_curated(r):
            continue
        key = cluster.deal_key(r.get("player", ""), r.get("window", ""))
        claims = groups.get(alias.get(key, key)) if key else None
        if not claims:
            continue
        new = {f: cluster.provisional(claims, f) for f in ("player", "from_club", "to_club")}
        outcome = (r.get("outcome") or "").strip().lower()

        if outcome in ("", "unknown"):
            # Gate B: refresh. player included because apply.py feeds it straight to
            # Wikipedia, and a bare "Olise" lands on a disambiguation page.
            changed = [f for f, v in new.items() if v and v != r.get(f)]
            if changed:
                for f in changed:
                    r[f] = new[f]
                stats["refreshed"].append((r["deal_id"], changed))
        elif outcome == "collapsed":
            # Gate C reads the PRE-refresh to_club -- which is why Gate B must never
            # run on a collapsed row. Refresh it first and the comparison below is
            # against the new value, the reason no longer matches, and the re-open
            # silently stops firing. See test_reopen_uses_the_pre_refresh_to_club.
            joined, rumoured = collapse_facts(r.get("notes"))
            if not joined or not same_club(rumoured, r.get("to_club")):
                continue                      # not a verdict we can prove was destination-based
            stale_destination = new["to_club"] and not same_club(new["to_club"], r.get("to_club"))
            alias_miss = same_club(joined, r.get("to_club"))
            if stale_destination or alias_miss:
                reopen_candidates.append((r, new, stale_destination))

    # Deterministic order so a capped run always drains the same prefix.
    reopen_candidates.sort(key=lambda t: int(t[0]["deal_id"]) if t[0]["deal_id"].isdigit() else 0)
    for r, new, stale_destination in reopen_candidates[:MAX_REOPENS]:
        old_to = r.get("to_club")
        if stale_destination:
            r["to_club"] = new["to_club"]
            why = f"destination revised {old_to} -> {new['to_club']}"
        else:
            why = f"{old_to} matched the club actually joined once club aliases were fixed"
        for f in ("outcome_date", "outcome_source_url"):
            r[f] = ""
        r["outcome"] = "unknown"
        r["verified"] = "auto"
        r["notes"] = f"[auto] re-opened: {why}; the prior collapse rested on the old destination"
        stats["reopened"].append((r["deal_id"], why))
    stats["deferred"] = [r["deal_id"] for r, _, _ in reopen_candidates[MAX_REOPENS:]]
    return stats


def _next_id(rows):
    return max((int(r["deal_id"]) for r in rows if str(r.get("deal_id", "")).isdigit()),
               default=0)


def bridge(conn, deals_path=DEALS, dry_run=False):
    """Create deals.csv rows for ingested clusters not yet represented. Returns stats."""
    fieldnames, rows = load_deals(deals_path)
    alias = cluster.alias_map_for(conn)
    groups = _group_claims(conn, alias)

    # High-water mark BEFORE any deletion. Computing it after the scrub lets a
    # deleted max id be handed to the next created deal, which then inherits the
    # dead row's orphaned claims.
    next_id = _next_id(rows)

    # Retroactive denylist enforcement (2026-07-05): rows created BEFORE a name
    # landed on the denylist survived forever — the filter gated creation and
    # the feed, never the ledger. Scrub MACHINE-created rows (verified=auto)
    # whose player is now denylisted; hand-curated rows are never auto-deleted.
    scrubbed = [r for r in rows
                if (r.get("verified") or "").strip().lower() == "auto"
                and is_known_non_player(r.get("player", ""))]
    if scrubbed:
        rows = [r for r in rows if r not in scrubbed]

    refreshed = refresh_and_reopen(conn, rows, alias, groups)

    existing = _existing_keys(rows, alias)

    created, attached, excluded = [], [], []
    for key, claims in groups.items():
        if key in existing:
            attached.append(key)            # already a deal (curated or previously bridged)
            continue
        if not claims:
            continue
        player = _provisional(claims, "player")
        # Two gates: a curated denylist (confirmed managers the text filter can't
        # catch, e.g. McInnes) and the headline text filter (general manager/women
        # signal). Either one keeps a non-player out of the deal ledger for good.
        if is_known_non_player(player) or _cluster_excluded(conn, claims):
            excluded.append(key)            # manager / women's item -> never a player deal
            continue
        window = cluster.key_window(key)
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

    dirty = created or scrubbed or refreshed["refreshed"] or refreshed["reopened"]
    if dirty and not dry_run:
        write_atomic(deals_path, fieldnames, rows)
    return {"created": created, "attached": attached, "excluded": excluded,
            "scrubbed": scrubbed, **refreshed}


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
    alias = cluster.alias_map_for(conn)
    groups = _group_claims(conn, alias)
    key_to_deal = _existing_keys(deal_rows, alias)

    # Companion to the deals scrub: machine-appended claims whose deal no longer
    # exists (e.g. a denylisted player's row was scrubbed) must not linger.
    live_ids = {r.get("deal_id", "").strip() for r in deal_rows}
    pruned = [r for r in rows
              if (r.get("verified") or "").strip().lower() == "auto"
              and (r.get("deal_id") or "").strip() not in live_ids]
    if pruned:
        rows = [r for r in rows if r not in pruned]
        if not dry_run:
            write_atomic(claims_path, fieldnames, rows)

    seen_triple = {(r.get("deal_id", "").strip(),
                    (r.get("source_name") or "").strip().lower(),
                    (r.get("stage") or "").strip().lower()) for r in rows}
    seen_url = {(r.get("source_url") or "").strip() for r in rows if r.get("source_url")}
    next_id = max((int(r["claim_id"]) for r in rows
                   if str(r.get("claim_id", "")).isdigit()), default=0)

    added = []
    for key, deal in key_to_deal.items():
        # groups[key], not claims_for_deal(key): once an alias forms, the canonical
        # key alone misses the bare cluster's claims -- which is precisely where the
        # Olise denial lives, the claim that stops that meter reading too high.
        for c in groups.get(key, []):
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
    for did, fields in stats.get("refreshed", []):
        print(f"  refreshed deal {did}: {', '.join(fields)} now match current reporting")
    for did, why in stats.get("reopened", []):
        print(f"  re-opened deal {did}: {why}")
    if stats.get("deferred"):
        print(f"  {len(stats['deferred'])} more re-open(s) deferred to the next run "
              f"(cap {MAX_REOPENS}): {', '.join(stats['deferred'])}")
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
