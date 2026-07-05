#!/usr/bin/env python3
"""
Promotion CLI — the human trust gate, made cheap enough to actually use.

Auto-resolved deals (verified=auto) are PROPOSED: they never score until a
human checks the evidence and promotes them to verified=YES. That check used
to mean hand-editing CSVs, so nobody did it and the leaderboard froze. This
tool shows each resolvable proposal WITH its evidence and its pending claims,
takes one keystroke, and writes both files atomically.

One 'y' promotes the DEAL and its verified=auto CLAIMS together — the human
saw the outcome evidence and the claim stages side by side, so both cross the
gate at once (score.py ignores auto claims until they're promoted).

Only completed/collapsed proposals are shown: outcome=unknown rows have
nothing to verify yet (they resolve later or at window close).

Usage:
    python outcome/promote.py            # interactive: y/n/a(ll)/q(uit)
    python outcome/promote.py --list     # show pending proposals, change nothing
    python outcome/promote.py --yes 12,14,20   # promote specific deal_ids
"""
import csv
import sys
from pathlib import Path

# Windows consoles default to cp1252, which explodes on players' accented
# names (deal review died on 'ć'). Force UTF-8, degrade gracefully elsewhere.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outcome.apply import DEALS, load_deals, write_atomic, rescore_and_rebuild

CLAIMS_CSV = ROOT / "ground-truth" / "journalist_claims.csv"
PROMOTABLE = ("completed", "collapsed")


def _load_claims(path=CLAIMS_CSV):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def pending(rows):
    """Deals awaiting human review: auto-verified with a resolved outcome."""
    return [r for r in rows
            if (r.get("verified") or "").strip().lower() == "auto"
            and (r.get("outcome") or "").strip().lower() in PROMOTABLE]


def claims_for(claim_rows, deal_id):
    return [c for c in claim_rows
            if (c.get("deal_id") or "").strip() == str(deal_id)
            and (c.get("verified") or "").strip().lower() == "auto"]


def _describe(deal, deal_claims):
    lines = [
        f"deal {deal['deal_id']}: {deal.get('player')} "
        f"{deal.get('from_club') or '?'} -> {deal.get('to_club') or '?'} "
        f"[{deal.get('outcome').upper()}]",
        f"  evidence: {deal.get('notes') or '(none recorded)'}",
        f"  source:   {deal.get('outcome_source_url') or '(none)'}",
    ]
    for c in deal_claims:
        lines.append(f"  claim: {c.get('source_name'):<20} {c.get('stage'):<12} "
                     f"{c.get('claim_date')}  \"{(c.get('raw_quote') or '')[:70]}\"")
    if not deal_claims:
        lines.append("  (no pending claims attached)")
    return "\n".join(lines)


FIXTURES = ROOT / "tests" / "fixtures" / "resolutions.json"


def _write_fixtures(rows, deal_ids, fixtures_path):
    """Every newly-trusted COMPLETED deal gets a reproducibility fixture so the
    test gate (tests/test_detect.py) can re-derive its outcome forever — without
    this, a promotion would correctly block the next cron at the test gate.
    Collapsed promotions are rare (positive evidence mid-window) and still need
    a hand-written fixture; we flag them instead of guessing."""
    import json
    res = json.loads(fixtures_path.read_text("utf-8"))
    added, manual = 0, []
    for r in rows:
        if r.get("deal_id") not in deal_ids:
            continue
        if (r.get("outcome") or "").lower() != "completed":
            manual.append(f"deal {r['deal_id']} {r.get('player')} ({r.get('outcome')})")
            continue
        if r.get("player") in res:
            continue
        notes = r.get("notes") or ""
        evidence = notes.split("|", 1)[1].strip() if "|" in notes else notes
        res[r["player"]] = {"status": "moved", "joined_club": r.get("to_club"),
                            "evidence": evidence}
        added += 1
    if added:
        fixtures_path.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    return added, manual


def promote(deal_ids, deals_path=DEALS, claims_path=CLAIMS_CSV, rebuild=True,
            fixtures_path=FIXTURES):
    """Flip the given deals AND their auto claims to verified=YES, and write the
    reproducibility fixtures the test gate requires. Atomic."""
    deal_ids = {str(d) for d in deal_ids}
    fieldnames, rows = load_deals(deals_path)
    cf, claim_rows = _load_claims(claims_path)
    promoted = []
    for r in rows:
        if r.get("deal_id") in deal_ids and (r.get("verified") or "").lower() == "auto":
            r["verified"] = "YES"
            promoted.append(r["deal_id"])
    n_claims = 0
    for c in claim_rows:
        if (c.get("deal_id") or "").strip() in deal_ids \
                and (c.get("verified") or "").strip().lower() == "auto":
            c["verified"] = "YES"
            n_claims += 1
    if promoted:
        write_atomic(deals_path, fieldnames, rows)
        write_atomic(claims_path, cf, claim_rows)
        n_fx, manual = _write_fixtures(rows, set(promoted), fixtures_path)
        print(f"(wrote {n_fx} test fixture(s) to {fixtures_path.name})")
        for m in manual:
            print(f"NOTE: add a fixture by hand for {m} — collapsed outcomes "
                  f"need the actual destination, see tests/fixtures/resolutions.json")
        print("REMINDER: bump the expected count in tests/test_detect.py by "
              f"{len(promoted)}, run 'python -m pytest -q', then commit "
              "ground-truth/, scoring/leaderboard.json and tests/ together.")
        if rebuild:
            rescore_and_rebuild()
    return promoted, n_claims


def main(argv):
    _, rows = load_deals(DEALS)
    _, claim_rows = _load_claims(CLAIMS_CSV)
    todo = pending(rows)
    if not todo:
        print("Nothing to review — no auto-verified completed/collapsed deals.")
        return 0

    if "--yes" in argv:
        ids = argv[argv.index("--yes") + 1].split(",")
        promoted, n_claims = promote(ids)
        print(f"Promoted {len(promoted)} deal(s) + {n_claims} claim(s) to verified=YES.")
        return 0

    print(f"{len(todo)} proposal(s) awaiting review "
          f"(y=promote, n=skip, a=promote ALL remaining, q=quit):\n")
    approved, approve_rest = [], False
    for deal in todo:
        print(_describe(deal, claims_for(claim_rows, deal["deal_id"])) + "\n")
        if "--list" in argv:
            continue
        if approve_rest:
            approved.append(deal["deal_id"])
            continue
        ans = input(f"promote deal {deal['deal_id']}? [y/n/a/q] ").strip().lower()
        if ans == "q":
            break
        if ans == "a":
            approve_rest = True
            approved.append(deal["deal_id"])
        elif ans == "y":
            approved.append(deal["deal_id"])
    if "--list" in argv:
        return 0
    if not approved:
        print("Nothing promoted.")
        return 0
    promoted, n_claims = promote(approved)
    print(f"\nPromoted {len(promoted)} deal(s) + {n_claims} claim(s) to verified=YES; "
          f"leaderboard rescored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
