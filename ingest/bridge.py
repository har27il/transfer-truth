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
from outcome.detect import same_club, collapse_facts, classify
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
        canon = alias.get(key, key) if key else None
        claims = groups.get(canon) if canon else None
        if not claims:
            continue
        new = {f: cluster.provisional(claims, f) for f in ("from_club", "to_club")}
        # player comes from the CANONICAL cluster only, never the union. Over the
        # union the bare-surname half can outvote the full name, renaming the row to
        # "Olise" -- whose deal_key no longer equals the canonical key, so the next
        # run would stop matching and create a DUPLICATE row. Restricting to the
        # canonical cluster keeps deal_key(new_player, window) == canon by
        # construction, because a cluster's key is minted from its own player name.
        new["player"] = cluster.provisional(
            [c for c in claims if c.get("deal_key") == canon], "player")
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
            # Would the classifier STILL call this collapsed on the very facts recorded
            # in its own notes? If not, the verdict is stale relative to today's rules
            # and must not stand. This one check subsumes every club-alias correction:
            # "joined Leeds United, not Leeds" (now the same club -> COMPLETED) and
            # "joined Hannover 96, not Rangers" where Hannover is the ORIGIN club
            # (-> UNKNOWN, refusing to decide) both stop being collapses without
            # needing a rule per shape.
            outdated = classify(r, {"status": "moved", "joined_club": joined})[0] != "collapsed"
            if stale_destination or outdated:
                reopen_candidates.append((r, new, stale_destination))

    # Deterministic order so a capped run always drains the same prefix.
    reopen_candidates.sort(key=lambda t: int(t[0]["deal_id"]) if t[0]["deal_id"].isdigit() else 0)
    for r, new, stale_destination in reopen_candidates[:MAX_REOPENS]:
        old_to = r.get("to_club")
        if stale_destination:
            r["to_club"] = new["to_club"]
            why = f"destination revised {old_to} -> {new['to_club']}"
        else:
            why = (f"the classifier no longer calls this a collapse on its own recorded "
                   f"evidence (joined {collapse_facts(r.get('notes'))[0]})")
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


def _row_rank(row):
    """Which row of a split pair survives: curated > resolved > full name."""
    return (_is_curated(row),
            (row.get("outcome") or "").strip().lower() in ("completed", "collapsed"),
            len(cluster.normalize_name(row.get("player", "")).split()))


def merge_split_deals(deals_path=DEALS, claims_path=CLAIMS_CSV, dry_run=False):
    """Fold each already-split bare-surname row into its full-name twin. Idempotent.

    The alias layer stops NEW splits forming, but bridge has no deletion path, so the
    11 rows already in the ledger would persist forever. Profiles come from deals.csv
    itself -- player/from_club/to_club/window are all there -- so this needs no store
    and can be dry-run against real data offline.

    ORDER IS THE ATOMICITY MECHANISM. There is no cross-file transaction, and
    bridge_claims deletes auto claims whose deal_id is not live. So: remap the claims
    FIRST (deleting nothing), then drop the loser rows. A crash in between leaves
    claims on a live survivor and an empty loser row -- degraded but non-destructive,
    and self-correcting next run. The reverse order is the data-loss order: it would
    destroy the Sky "Real Madrid deny interest" claim rather than reattach it.
    """
    fieldnames, rows = load_deals(deals_path)
    profiles, by_key = {}, {}
    for r in rows:
        k = cluster.deal_key(r.get("player", ""), r.get("window", ""))
        if k:
            profiles.setdefault(k, {"player": r.get("player", ""),
                                    "from_club": r.get("from_club", ""),
                                    "to_club": r.get("to_club", "")})
            by_key.setdefault(k, []).append(r)

    cfields, crows = _load_claims_csv(claims_path)
    yes_deal_ids = {(c.get("deal_id") or "").strip() for c in crows
                    if (c.get("verified") or "").strip().lower() in _TRUSTED_FLAGS}

    merged, refused, remap = [], [], {}
    for bare_key, canon_key in sorted(cluster.alias_map(profiles).items()):
        group = by_key.get(bare_key, []) + by_key.get(canon_key, [])
        if len(group) < 2:
            continue
        survivor = max(group, key=_row_rank)
        losers = [r for r in group if r is not survivor]
        why = None
        if sum(1 for r in group if _is_curated(r)) > 1:
            why = "two curated rows -- a human must decide"
        elif any(_is_curated(r) for r in losers):
            why = "the losing row is curated and is never auto-deleted"
        elif any((r.get("outcome") or "").strip().lower() in ("completed", "collapsed")
                 for r in losers):
            why = "the losing row carries a recorded verdict; deleting it loses history"
        elif any(r["deal_id"].strip() in yes_deal_ids for r in losers):
            why = "the losing row holds a promoted (verified=YES) claim"
        if why:
            refused.append((survivor.get("player"), why))
            continue
        for r in losers:
            remap[r["deal_id"].strip()] = survivor["deal_id"].strip()
        merged.append((survivor, losers))

    if not merged:
        return {"merged": [], "refused": refused, "remapped": 0, "deduped": []}

    # 1) claims first: rewrite deal_id, delete nothing.
    remapped = 0
    for c in crows:
        did = (c.get("deal_id") or "").strip()
        if did in remap:
            c["deal_id"] = remap[did]
            remapped += 1

    # 2) a remapped claim can now collide with one already on the survivor. That is the
    # correlated-evidence duplicate bridge_claims guards against, and it would
    # double-count a Brier sample. Drop the LATER-dated copy, never a curated one.
    def _keeps(a, b):
        """True if claim a should be kept over b: curated wins, then the EARLIER
        claim (calling it first is what the scorer's earliness bonus rewards)."""
        a_cur = (a.get("verified") or "").strip().lower() in _TRUSTED_FLAGS
        b_cur = (b.get("verified") or "").strip().lower() in _TRUSTED_FLAGS
        if a_cur != b_cur:
            return a_cur
        # Strict: on an equal date the incumbent stays, so the result does not depend
        # on row order in the file.
        return (a.get("claim_date") or "") < (b.get("claim_date") or "")

    seen, keep, deduped = {}, [], []
    for c in crows:
        triple = ((c.get("deal_id") or "").strip(),
                  (c.get("source_name") or "").strip().lower(),
                  (c.get("stage") or "").strip().lower())
        prev = seen.get(triple)
        if prev is None:
            seen[triple] = c
            keep.append(c)
        elif _keeps(c, prev):
            keep[keep.index(prev)] = c
            seen[triple] = c
            deduped.append(prev.get("claim_id"))
        else:
            deduped.append(c.get("claim_id"))
    if not dry_run:
        write_atomic(claims_path, cfields, keep)

    # 3) only now drop the loser rows.
    dead = {r["deal_id"] for _s, ls in merged for r in ls}
    if not dry_run:
        write_atomic(deals_path, fieldnames, [r for r in rows if r["deal_id"] not in dead])
    return {"merged": merged, "refused": refused, "remapped": remapped, "deduped": deduped}


def _print_merge(stats, dry_run):
    for survivor, losers in stats["merged"]:
        for l in losers:
            print(f"  merge deal {l['deal_id']} ({l['player']}) -> {survivor['deal_id']} "
                  f"({survivor['player']}) [{l.get('from_club') or '-'} / "
                  f"{l.get('to_club') or '-'}] into [{survivor.get('from_club') or '-'} / "
                  f"{survivor.get('to_club') or '-'}]")
    for player, why in stats["refused"]:
        print(f"  REFUSED {player}: {why}")
    if stats["merged"]:
        print(f"{'Would merge' if dry_run else 'Merged'} {len(stats['merged'])} split "
              f"player(s); {stats['remapped']} claim(s) reattached, "
              f"{len(stats['deduped'])} duplicate(s) dropped.")


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
    # Before bridge(): claims must be reattached before anything can prune them, and
    # the merge must not run inside bridge_claims, whose prune runs first.
    _print_merge(merge_split_deals(dry_run=dry), dry)
    _print(bridge(conn, dry_run=dry), dry)
    added = bridge_claims(conn, dry_run=dry)
    print(f"{'Would append' if dry else 'Appended'} {len(added)} claim(s) to "
          f"journalist_claims.csv (verified=auto; score only after promotion).")
