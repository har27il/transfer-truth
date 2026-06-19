#!/usr/bin/env python3
"""
Deal-outcome predictor — the LEARNED version of the Deal Probability Engine (§3).

Instead of hand-tuned weights, this trains a logistic-regression model on your
ground-truth outcomes to predict P(deal completes) from rumor features.

Logistic regression is implemented from scratch in numpy (sigmoid + gradient
descent + L2), so there are no new installs and you can see exactly what the
model does. Evaluation is leave-one-out cross-validation (LOOCV) because the
dataset is tiny.

HONEST LIMITS:
  - With ~10-40 labeled deals this is a SCAFFOLD, not a trustworthy predictor.
    It will overfit. LOOCV + L2 keep it honest, but treat the numbers as a
    learning exercise until you have hundreds of deals.
  - `fee_eur` is intentionally NOT a feature: collapsed deals have blank fees, so
    using it would be target leakage (the model would cheat). Don't add it back.

PROMOTION GATE (before this ever touches the live site / scores):
  1. >= ~150 featurizable verified deals (claim-bearing, verified=YES).
  2. BEATS the live meter.py heuristic on out-of-sample Brier (printed below) --
     beating coin-flip is not enough when a hand-tuned heuristic already ships.
  3. Calibrated (predicted P matches realized frequency).
  Until all three hold, meter.py (probability) + score.py (Brier reliability) stay
  the single source of truth. This script is a diagnostic, never wired into the cron.

Usage:
    python ml/deal_predictor.py [path/to/journalist_claims.csv]
Default claims file: ground-truth/journalist_claims.sample.csv
"""

import csv, json, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEALS = ROOT / "ground-truth" / "deals.csv"
_POS = [a for a in sys.argv[1:] if not a.startswith("-")]
INCLUDE_AUTO = "--include-auto" in sys.argv   # train on verified=auto rows (preview only)
CLAIMS = Path(_POS[0]) if _POS else ROOT / "ground-truth" / "journalist_claims.sample.csv"

sys.path.insert(0, str(ROOT))
from stagemap import STAGE_P    # shared stage -> implied-probability map (single source of truth)
from ground_truth import load_outcomes as _load_trusted  # don't train on unverified auto labels
from ingest import meter        # the LIVE hand-tuned heuristic -- the baseline the model must beat

FEATURES = ["n_claims", "n_sources", "max_p", "mean_p", "denied_flag"]


def load_outcomes():
    # Mirror the scorer: only trusted (verified=YES) outcomes train the model by default.
    return {did: int(v) for did, v in _load_trusted(DEALS, include_auto=INCLUDE_AUTO).items()}


def build_dataset(outcomes):
    """One feature row per deal that has at least one usable claim.

    Also returns claims_by_deal (meter-shaped claim dicts) so the incumbent
    meter.py heuristic can be scored on the SAME deals -- see meter_baseline()."""
    claims_by_deal = {}
    with open(CLAIMS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            did = (r.get("deal_id") or "").strip()
            stage = (r.get("stage") or "").strip().lower()
            src = (r.get("source_name") or "").strip()
            if did in outcomes and stage in STAGE_P and src:
                claims_by_deal.setdefault(did, []).append({
                    "source_name": src, "implied_p": STAGE_P[stage], "stage": stage,
                    "claim_date": (r.get("claim_date") or "").strip(),
                })

    rows, labels, ids = [], [], []
    for did, cs in claims_by_deal.items():
        ps = [c["implied_p"] for c in cs]
        rows.append([
            len(cs),                                       # n_claims
            len({c["source_name"] for c in cs}),           # n_sources (distinct)
            max(ps),                                        # strongest claim
            sum(ps) / len(ps),                              # average claim strength
            1.0 if any(c["stage"] == "denied" for c in cs) else 0.0,
        ])
        labels.append(outcomes[did])
        ids.append(did)
    return np.array(rows, dtype=float), np.array(labels, dtype=float), ids, claims_by_deal


def meter_baseline(claims_by_deal, ids):
    """Incumbent baseline: the LIVE meter.py probability per deal, UNTRAINED.

    The model must beat THIS on out-of-sample Brier to earn its place -- beating a
    coin-flip (majority class) is meaningless when a hand-tuned heuristic already
    ships. The meter is a fixed function (no learned params), so its in-sample ==
    out-of-sample; comparing it to the model's LOOCV is fair. (It does read source
    reliability from leaderboard.json, fit on these same outcomes, so the baseline
    is if anything slightly FLATTERED -- i.e. a conservative, harder bar to clear.)"""
    reliability, pop = meter.load_reliability()
    preds = []
    for did in ids:
        m = meter.deal_probability(claims_by_deal[did], reliability, pop)
        preds.append(m["probability"] if m else 0.5)
    return np.array(preds, dtype=float)


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def train(X, y, lr=0.1, iters=3000, l2=1.0):
    """Logistic regression via gradient descent. X is already standardized."""
    n, d = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])          # prepend bias column
    w = np.zeros(d + 1)
    for _ in range(iters):
        pred = sigmoid(Xb @ w)
        grad = Xb.T @ (pred - y) / n
        grad[1:] += (l2 / n) * w[1:]              # L2 on weights, not bias
        w -= lr * grad
    return w


def standardize_fit(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    return mu, sd


def loocv(X, y):
    """Leave-one-out CV: train on n-1, predict the held-out one. Honest for tiny n."""
    n = len(y)
    preds = np.zeros(n)
    for i in range(n):
        mask = np.arange(n) != i
        mu, sd = standardize_fit(X[mask])
        w = train((X[mask] - mu) / sd, y[mask])
        xi = (X[i] - mu) / sd
        preds[i] = sigmoid(np.hstack([1.0, xi]) @ w)
    return preds


def main():
    outcomes = load_outcomes()
    X, y, ids, claims_by_deal = build_dataset(outcomes)
    if len(y) < 6:
        print(f"Only {len(y)} featurizable deals (need claims with a stage). "
              f"Add more rows to {CLAIMS.name} before this means anything.")
        if len(y) == 0:
            return
    print(f"\nDeal-outcome predictor  -  {len(y)} deals, "
          f"{int(y.sum())} completed / {int(len(y)-y.sum())} collapsed\n")

    # --- LOOCV evaluation ---
    preds = loocv(X, y)
    eps = 1e-9
    brier = np.mean((preds - y) ** 2)
    logloss = -np.mean(y * np.log(preds + eps) + (1 - y) * np.log(1 - preds + eps))
    acc = np.mean((preds >= 0.5) == (y == 1))
    base = max(y.mean(), 1 - y.mean())            # always-guess-majority baseline
    print("Leave-one-out cross-validation (out-of-sample):")
    print(f"  accuracy : {acc:5.1%}   (baseline always-majority: {base:5.1%})")
    print(f"  Brier    : {brier:5.3f}   (lower is better; 0.25 = coin flip)")
    print(f"  log-loss : {logloss:5.3f}\n")

    # --- Incumbent baseline: the live meter.py heuristic on the SAME deals ---
    # The real bar is not coin-flip, it's the hand-tuned probability already shipping.
    mp = meter_baseline(claims_by_deal, ids)
    m_brier = np.mean((mp - y) ** 2)
    m_acc = np.mean((mp >= 0.5) == (y == 1))
    m_logloss = -np.mean(y * np.log(mp + eps) + (1 - y) * np.log(1 - mp + eps))
    print("Incumbent baseline - live meter.py heuristic (untrained = already out-of-sample):")
    print(f"  accuracy : {m_acc:5.1%}")
    print(f"  Brier    : {m_brier:5.3f}")
    print(f"  log-loss : {m_logloss:5.3f}\n")

    beats = brier < m_brier
    margin = m_brier - brier
    print(f"VERDICT: learned model {'BEATS' if beats else 'does NOT beat'} the meter on "
          f"out-of-sample Brier ({brier:.3f} vs {m_brier:.3f}, {'+' if margin>=0 else ''}{margin:.3f}, n={len(y)}).")
    print("  Promotion gate (all three required before ML goes near the live site):")
    print(f"    1. >= ~150 featurizable verified deals   -> have {len(y)}")
    print(f"    2. beats meter.py on out-of-sample Brier  -> {'PASS' if beats else 'FAIL'}")
    print( "    3. calibrated (pred P ~ realized freq)    -> not yet measured")
    print("  Until all three hold, meter.py + score.py stay the source of truth.\n")

    # --- Final model on all data, for coefficients + per-deal probabilities ---
    mu, sd = standardize_fit(X)
    w = train((X - mu) / sd, y)
    print("Learned weights (standardized - sign shows direction, magnitude shows pull):")
    print(f"  {'bias':<12} {w[0]:+.2f}")
    for name, coef in zip(FEATURES, w[1:]):
        print(f"  {name:<12} {coef:+.2f}")

    all_p = sigmoid(np.hstack([np.ones((len(X), 1)), (X - mu) / sd]) @ w)
    ranked = sorted(zip(ids, all_p, y), key=lambda t: t[1], reverse=True)
    print("\nIn-sample predicted P(completes) per deal:")
    for did, p, actual in ranked:
        flag = "ok " if (p >= 0.5) == (actual == 1) else "MISS"
        print(f"  deal {did:<3} p={p:4.0%}  actual={'completed' if actual else 'collapsed':<9} {flag}")

    out = ROOT / "ml" / "deal_predictions.json"
    out.write_text(json.dumps({
        "n_deals": len(y), "loocv": {"accuracy": acc, "brier": brier, "logloss": logloss},
        "baseline_meter": {"accuracy": m_acc, "brier": m_brier, "logloss": m_logloss},
        "beats_meter": bool(beats),
        "weights": {"bias": w[0], **dict(zip(FEATURES, w[1:].tolist()))},
        "predictions": [{"deal_id": d, "p_completes": float(p), "outcome": int(a)}
                        for d, p, a in ranked],
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
