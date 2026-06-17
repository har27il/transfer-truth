#!/usr/bin/env python3
"""
Live deal-probability meter: per-deal P(move happens) from clustered claims.

Each claim already carries the stage's implied probability (implied_p). The meter
combines a deal's claims into one number by weighting each claim by:

  reliability(source)  -- how often that journalist/outlet is right, from the
                          leaderboard (scoring/leaderboard.json). Unknown sources
                          get the population mean (same shrinkage prior the scorer uses).
  recency(claim_date)  -- a half-life decay so a 'here we go' from yesterday counts
                          for more than a 'linked with' from three weeks ago.

    P = sum(w_i * implied_p_i) / sum(w_i),   w_i = reliability_i * recency_i

then a small corroboration boost for INDEPENDENT sources that commit to the deal
(two outlets saying 'agreed' is stronger than one saying it twice). A denied claim
from a reliable source naturally drags P down because its implied_p is 0.02.

Pure functions + injected `today` -> fully testable offline.
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import store

LEADERBOARD = ROOT / "scoring" / "leaderboard.json"
HALFLIFE_DAYS = 14          # a claim's weight halves every two weeks
MAX_CORRO_BOOST = 0.15      # cap on the independent-corroboration nudge
DEFAULT_POP_WEIGHT = 0.75   # fallback prior if no leaderboard exists yet


def load_reliability(path=LEADERBOARD):
    """Return (source -> weight in 0..1, population_weight). Empty if no leaderboard."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, DEFAULT_POP_WEIGHT
    rel = {r["source"]: r["score"] / 100.0 for r in d.get("leaderboard", [])}
    pop = d.get("population_mean_accuracy", DEFAULT_POP_WEIGHT * 100) / 100.0
    return rel, pop


def _parse_date(s):
    try:
        return date.fromisoformat((s or "").strip()[:10])
    except (ValueError, AttributeError):
        return None


def _recency(claim_date, today):
    if not claim_date:
        return 0.5                      # undated claim: weak but non-zero
    age = max(0, (today - claim_date).days)
    return 0.5 ** (age / HALFLIFE_DAYS)


def _label(p, spread):
    """(text, color). 'Contested' when sources strongly disagree (wide implied_p spread)."""
    if p >= 0.70:
        return ("Likely", "green")
    if p >= 0.40:
        return ("Contested" if spread > 0.6 else "Possible", "yellow")
    return ("Denied" if spread > 0.6 else "Cold", "red")


def deal_probability(claims, reliability, pop_weight, today=None):
    """Combine a deal's claims into a probability + display detail. None if unscorable."""
    today = today or date.today()
    num = den = 0.0
    committed = set()           # distinct sources asserting the deal (implied_p >= .5)
    ps = []
    for c in claims:
        p = c.get("implied_p")
        if p is None:
            continue
        ps.append(p)
        w = reliability.get(c.get("source_name"), pop_weight) * _recency(_parse_date(c.get("claim_date")), today)
        num += w * p
        den += w
        if p >= 0.5:
            committed.add(c.get("source_name"))
    if den == 0:
        return None
    prob = num / den
    n_indep = len(committed)
    if n_indep > 1:             # independent corroboration nudges toward certainty
        boost = min(MAX_CORRO_BOOST, 0.05 * (n_indep - 1))
        prob = prob + (1 - prob) * boost
    prob = max(0.0, min(1.0, prob))
    spread = (max(ps) - min(ps)) if ps else 0.0
    text, color = _label(prob, spread)
    latest = max(claims, key=lambda c: (c.get("claim_date") or ""))
    return {
        "probability": round(prob, 3),
        "percent": round(prob * 100),
        "label": text,
        "color": color,
        # Contestedness signals for the feed's "most debated" hero: how far apart the
        # sources are (spread), and how close the verdict sits to a coin-flip (uncertainty).
        "spread": round(spread, 3),
        "uncertainty": round(1 - 2 * abs(prob - 0.5), 3),
        "n_claims": len(claims),
        "n_sources": len({c.get("source_name") for c in claims}),
        "latest_stage": latest.get("stage"),
        "player": latest.get("player"),
        "to_club": latest.get("to_club"),
        "from_club": latest.get("from_club"),
        "sources": sorted({c.get("source_name") for c in claims if c.get("source_name")}),
    }


def meters(conn, today=None, reliability=None, pop_weight=None, max_age_days=None):
    """Compute a meter for every deal in the ingest store, hottest first.

    max_age_days: when set, drop deals whose NEWEST claim is older than this many days.
    Once ingest.db is persisted between runs it keeps accumulating claims, so without a
    display window the feed would slowly fill with dead weeks-old rumours. None = no
    filter (keeps offline tests and the empty-store demo untouched)."""
    if reliability is None or pop_weight is None:
        reliability, pop_weight = load_reliability()
    today = today or date.today()
    out = []
    for key in store.deal_keys(conn):
        claims = store.claims_for_deal(conn, key)
        if max_age_days is not None:
            dates = [d for d in (_parse_date(c.get("claim_date")) for c in claims) if d]
            if dates and (today - max(dates)).days > max_age_days:
                continue  # stale deal: newest claim is past the display window
        m = deal_probability(claims, reliability, pop_weight, today)
        if m:
            m["deal_key"] = key
            out.append(m)
    out.sort(key=lambda m: m["probability"], reverse=True)
    return out


if __name__ == "__main__":
    conn = store.connect()
    rows = meters(conn)
    if not rows:
        print("No live deals in the ingest store yet (run the ingestion pipeline first).")
    for m in rows:
        print(f"  {m['percent']:>3}% [{m['label']:<9}] {m['player']} -> {m['to_club'] or '?'} "
              f"({m['n_sources']} src, {m['latest_stage']})")
