# Transfer Truth

Aggregates football transfer rumours, scores how reliable each journalist is against
**real outcomes** (not vibes), and shows a per-deal probability the move actually
happens. Learning side project.

## How it fits together

```
  rumour text ──> engine (LLM extract) ──> claims ─┐
                                                    ├─> score.py (Brier + shrinkage)
  Wikipedia ──> outcome detect ──> deals.csv ──────┘        │
                  (verified=auto, human-promoted)           v
                                              docs/index.html  (GitHub Pages)
```

- **`ingest/`** — Phase 3 ingestion: RSS sources -> extract (engine) -> dedup -> cluster by player -> SQLite (`sources.py`, `store.py`, `cluster.py`, `pipeline.py`). `bridge.py` turns new clusters into proposed `deals.csv` rows (`unknown`/`auto`) for the outcome workflow to resolve. `meter.py` computes each deal's live probability (reliability-weighted, recency-decayed, corroboration-boosted) -> `docs/feed.html`.
- **`engine/`** — turns one raw post into strict JSON (`run.py` + system prompt). Graded by `tests/golden/`.
- **`outcome/`** — resolves whether a rumoured deal completed or collapsed (`source.py` fetch + `detect.py` decision, positive-evidence only). `apply.py` writes results back atomically.
- **`ground_truth.py`** — single trusted-outcome gate: auto-resolved rows (`verified=auto`) are *proposed* and don't score until a human promotes them to `verified=YES`.
- **`scoring/score.py`** — Journalist Truth Score (Brier vs real outcomes, sample-size shrinkage, earliness bonus).
- **`ml/deal_predictor.py`** — numpy logistic-regression experiment (honest scaffold, not trusted yet). Dormant: not in the cron/feed/scoring. It self-benchmarks against the live `meter.py` heuristic and prints a 3-condition **promotion gate** (≥~150 deals + beats meter on out-of-sample Brier + calibrated) that must pass before it could ever go live.
- **`site/build_leaderboard.py`** — renders `docs/index.html` (served by GitHub Pages from `/docs`).

## Run it

```bash
pip install -r requirements.txt
pytest -q                                               # 38 tests
python scoring/score.py ground-truth/journalist_claims.csv
python site/build_leaderboard.py                        # -> docs/index.html
python site/build_feed.py                               # -> docs/feed.html (live meter; demo until ingestion runs)
```

## Automation (Phase 2)

`.github/workflows/outcomes.yml` resolves `unknown` deals, rebuilds the site, and
commits on change. Manual-run only for now (no `unknown` deals exist until Phase 3
ingestion). Needs an `NVIDIA_API_KEY` repo secret (free key at build.nvidia.com):

```bash
gh secret set NVIDIA_API_KEY
```
