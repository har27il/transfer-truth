#!/usr/bin/env python3
"""
Journalist Truth Score — Phase 1 scoring engine.

Reads ground-truth deal outcomes + journalist stage-claims, computes a
Brier-based accuracy with sample-size shrinkage and an earliness bonus, and
prints a leaderboard + writes leaderboard.json.

Stdlib only (no pandas). Usage:
    python scoring/score.py [path/to/journalist_claims.csv]
Default claims file: ground-truth/journalist_claims.sample.csv
"""

import csv, json, math, sys, statistics
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
DEALS = ROOT / "ground-truth" / "deals.csv"
_POS = [a for a in sys.argv[1:] if not a.startswith("-")]
INCLUDE_AUTO = "--include-auto" in sys.argv   # count verified=auto rows (preview only)
CLAIMS = Path(_POS[0]) if _POS else ROOT / "ground-truth" / "journalist_claims.sample.csv"

sys.path.insert(0, str(ROOT))
from stagemap import STAGE_P    # shared stage -> implied-probability map (single source of truth)
from ground_truth import load_outcomes  # single trusted-outcome gate (verified=YES only by default)

K = 20          # shrinkage strength: with <K claims you sit near the population mean
EARLINESS_CAP = 3.0   # max bonus points for breaking news early
EARLINESS_K = 0.6


def parse_date(s):
    try:
        return date.fromisoformat(s.strip())
    except Exception:
        return None


def load_deals():
    # Only verified=YES outcomes score by default; verified=auto rows are proposed
    # (see ground_truth.py). Pass --include-auto to preview them.
    return load_outcomes(DEALS, include_auto=INCLUDE_AUTO)


def load_claims(deals):
    claims = []
    with open(CLAIMS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            did = (r.get("deal_id") or "").strip()
            src = (r.get("source_name") or "").strip()
            stage = (r.get("stage") or "").strip().lower()
            if not did or not src or stage not in STAGE_P:
                continue                # skip blanks / example rows / bad stages
            if (r.get("verified") or "").strip().lower() == "auto" and not INCLUDE_AUTO:
                # Claim-level gate, mirroring the deal-level one: bridge-appended
                # claims are unreviewed LLM stage-extractions and must not move a
                # journalist's public Brier score until a human promotes them
                # (outcome/promote.py flips deal AND claims to YES together).
                continue
            if did not in deals:
                continue                # claim about an unresolved/unknown deal
            claims.append({
                "deal_id": did, "source": src, "stage": stage,
                "p": STAGE_P[stage], "outcome": deals[did],
                "date": parse_date(r.get("claim_date") or ""),
            })
    return claims


def earliness_bonus(claims):
    """Reward breaking a deal early ONLY when you committed to it (p>=0.5) and it
    actually happened. A vague early 'link' earns nothing; the bonus is weighted by
    how right the call was, so you can't farm it by spamming everyone early."""
    bonus = {}
    by_deal = {}
    for c in claims:
        by_deal.setdefault(c["deal_id"], []).append(c)
    for did, cs in by_deal.items():
        if cs[0]["outcome"] != 1.0:
            continue  # only reward scoops on deals that actually completed
        dated = [c for c in cs if c["date"]]
        if len(dated) < 2:
            continue
        median_day = statistics.median([c["date"].toordinal() for c in dated])
        for c in dated:
            if c["p"] < 0.5:
                continue  # uncommitted "link"/"interest" earns no scoop credit
            hours_before = (median_day - c["date"].toordinal()) * 24
            if hours_before > 0:
                correctness = 1 - (c["p"] - c["outcome"]) ** 2   # weight by how right
                b = EARLINESS_K * math.log(1 + hours_before) * correctness
                bonus[c["source"]] = bonus.get(c["source"], 0.0) + b
    return {s: min(v, EARLINESS_CAP) for s, v in bonus.items()}


def score():
    deals = load_deals()
    claims = load_claims(deals)
    if not claims:
        print("No scoreable claims found. Fill journalist_claims.csv (stage + a "
              "deal_id whose outcome is completed/collapsed).")
        return

    all_briers = [(c["p"] - c["outcome"]) ** 2 for c in claims]
    pop_mean_acc = (1 - statistics.mean(all_briers)) * 100

    by_src = {}
    for c in claims:
        by_src.setdefault(c["source"], []).append((c["p"] - c["outcome"]) ** 2)

    bonus = earliness_bonus(claims)
    rows = []
    for src, briers in by_src.items():
        n = len(briers)
        mean_brier = statistics.mean(briers)
        raw_acc = (1 - mean_brier) * 100
        shrunk = (n / (n + K)) * raw_acc + (K / (n + K)) * pop_mean_acc
        eb = bonus.get(src, 0.0)
        final = max(0.0, min(100.0, shrunk + eb))
        rows.append({
            "source": src, "n_claims": n,
            "raw_accuracy": round(raw_acc, 1),
            "shrunk": round(shrunk, 1),
            "earliness_bonus": round(eb, 2),
            "score": round(final, 1),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)

    print(f"\nJournalist Truth Score  (population mean = {pop_mean_acc:.1f}%, "
          f"K={K}, {len(claims)} claims over {len(by_src)} sources)\n")
    print(f"{'#':>2}  {'source':<20} {'n':>3}  {'raw%':>6}  {'shrunk%':>8}  "
          f"{'early':>6}  {'SCORE':>6}")
    print("-" * 62)
    for i, r in enumerate(rows, 1):
        print(f"{i:>2}  {r['source']:<20} {r['n_claims']:>3}  "
              f"{r['raw_accuracy']:>6}  {r['shrunk']:>8}  "
              f"{r['earliness_bonus']:>6}  {r['score']:>6}")

    out = ROOT / "scoring" / "leaderboard.json"
    out.write_text(json.dumps({
        "population_mean_accuracy": round(pop_mean_acc, 1),
        "k": K, "n_claims": len(claims), "leaderboard": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    score()
