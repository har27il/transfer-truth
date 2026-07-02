# TODOS

Deferred work with full context. Each entry: what, why, and where to start —
so a future session (or a future you) doesn't have to re-derive the reasoning.

## 1. Live canary against provider model drift

- **What:** At the top of each cron ingest run, send 1–2 golden cases
  (`tests/golden/cases.jsonl`) through the real NIM model and abort before any
  DB write if they fail to parse or miss critical fields.
- **Why:** NVIDIA's free tier retires/re-aliases models. The evaluated-model
  allowlist and golden-eval workflow only defend against *local* edits — nothing
  detects the provider changing behaviour under the same model ID before a run.
- **Pros:** Converts provider drift from a same-day alarm into a pre-flight
  abort; ~20 lines in `ingest/pipeline.py`.
- **Cons:** Adds latency + a new failure mode to every cron run. Since retry
  semantics landed (parse-failed posts are re-attempted next run), drift no
  longer loses data — the payoff shrank, which is why this was deferred.
- **Context:** Deferred during the July 2026 eng review (Tension 2) of the
  June 25 model-swap incident. Depends on: WS-A retry semantics (landed).
- **Start at:** `ingest/pipeline.py` phase 2 entry; reuse `engine/golden.py`
  grading for the canary cases.

## 2. Revisit ML predictor at ~150 verified deals

- **What:** When `ground-truth/deals.csv` reaches ~150 featurizable
  `verified=YES` deals, re-run `python ml/deal_predictor.py
  ground-truth/journalist_claims.csv`. If it beats `meter.py` on out-of-sample
  Brier AND is calibrated, plan promotion of learned probabilities into the
  live meter.
- **Why:** The deferred half of the July 2026 decision "fix the pipeline,
  defer ML behind a written data threshold." With ~26–56 verified deals the
  promotion gate provably fails; training on that would ship a predictor worse
  than the hand-tuned meter.
- **Pros:** The three-part gate and the meter baseline comparison are already
  codified in `ml/deal_predictor.py:20-26` — the check is one command.
- **Cons:** None beyond list upkeep; no code is written ahead of the data.
- **Context:** Post-promotion of the current backlog ≈ 56 verified deals; the
  WS-B claims bridge + promotion flow and the September window close (collapse
  resolutions) drive accumulation toward the threshold.
- **Depends on:** WS-B promotion flow landed; deals accumulating.
