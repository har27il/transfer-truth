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

FEATURES = ["n_claims", "n_sources", "max_p", "mean_p", "denied_flag"]


def load_outcomes():
    # Mirror the scorer: only trusted (verified=YES) outcomes train the model by default.
    return {did: int(v) for did, v in _load_trusted(DEALS, include_auto=INCLUDE_AUTO).items()}


def build_dataset(outcomes):
    """One feature row per deal that has at least one usable claim."""
    claims_by_deal = {}
    with open(CLAIMS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            did = (r.get("deal_id") or "").strip()
            stage = (r.get("stage") or "").strip().lower()
            src = (r.get("source_name") or "").strip()
            if did in outcomes and stage in STAGE_P and src:
                claims_by_deal.setdefault(did, []).append((src, STAGE_P[stage], stage))

    rows, labels, ids = [], [], []
    for did, cs in claims_by_deal.items():
        ps = [p for _, p, _ in cs]
        rows.append([
            len(cs),                                   # n_claims
            len({s for s, _, _ in cs}),                # n_sources (distinct)
            max(ps),                                   # strongest claim
            sum(ps) / len(ps),                         # average claim strength
            1.0 if any(st == "denied" for _, _, st in cs) else 0.0,
        ])
        labels.append(outcomes[did])
        ids.append(did)
    return np.array(rows, dtype=float), np.array(labels, dtype=float), ids


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
    X, y, ids = build_dataset(outcomes)
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
        "weights": {"bias": w[0], **dict(zip(FEATURES, w[1:].tolist()))},
        "predictions": [{"deal_id": d, "p_completes": float(p), "outcome": int(a)}
                        for d, p, a in ranked],
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
