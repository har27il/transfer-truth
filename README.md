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
pytest -q
python scoring/score.py ground-truth/journalist_claims.csv
python site/build_leaderboard.py                        # -> docs/index.html
python site/build_feed.py                               # -> docs/feed.html (live meter)
```

The build commands overwrite the tracked pages in `docs/`. On a fresh clone the feed
shows demo data, because the live claims database exists only in the GitHub Actions
cache, so don't commit `docs/` from a local run.

## Automation

`.github/workflows/update-site.yml` is the live pipeline. It runs on the schedule set
in that file (its comments explain the cadence) or on demand with the "Run workflow"
button. It runs the tests, pulls the RSS feeds, proposes new deals, resolves outcomes,
rebuilds the site, and commits any changes to `main` as `transfer-bot`.

`.github/workflows/golden-eval.yml` is the model and prompt quality gate. It runs on
demand, and on pushes that change the engine, the model choice or the golden tests
(paths listed in the file).

Both need an `NVIDIA_API_KEY` repo secret (free key at build.nvidia.com);
`update-site.yml` also passes `LANGSMITH_API_KEY` for tracing:

```bash
gh secret set NVIDIA_API_KEY
```
