# TODOS

Deferred work with full context. Each entry: what, why, and where to start —
so a future session (or a future you) doesn't have to re-derive the reasoning.

## 0. Split players: bare surname vs full name are separate deals — DONE 2026-08-07

Shipped in `786c5d6` (alias layer + feed grouping) and `fe4dd91` (ledger merge).

- **Rule that shipped is BROADER than the one specified here.** This entry said
  merge on surname + destination + window (9 merges). That rule cannot fix the
  case that prompted the work: deal 73 `Diomande / RB Leipzig / Liverpool` and
  deal 124 `Yan Diomande / RB Leipzig / PSG` are the same player, and their
  destinations disagree only because one was stale. Requiring surname + window +
  (**destination OR origin** match) gives **11 merges, 2 refusals** — it adds
  Diomande and Gusto (both destinations blank, both origins Chelsea), and still
  correctly refuses Ousmane Diomande, Bowie and Kroupi. `from_club` is the stable
  side: a hijack moves the destination, never the origin.
- **Implemented as an ALIAS MAP, not a `deal_key` format change.** The key is
  stored in `ingest.db`, which is cached across CI runs — changing the format
  would leave old claims on old keys and new claims on new ones, re-splitting the
  same deal by epoch. The map is recomputed from the live claim population each
  run, so warm and cold caches group identically. See `ingest/cluster.py`.
- Backfill was `bridge.merge_split_deals()` — permanent and idempotent, reads
  profiles from `deals.csv` itself so it needs no store and can be dry-run
  offline. 271 → 260 rows, 14 claims reattached, zero orphaned.

## 0a. Resolver window-awareness (stale-evidence class) — HIGH

- **What:** `outcome/source.py` / `outcome/detect.py` must check that the
  career line used as evidence falls INSIDE the deal's window before resolving.
- **Why:** 8 of 34 proposals in the July 2026 promotion review cited 2024/25
  Wikipedia lines to settle 2026-summer rumours. Worst case: Ben Godfrey
  resolved COLLAPSED via an Aug-2025 loan while BBC/Sky reported him actually
  joining Rangers on 2026-06-29 — a factually wrong outcome that only the
  human gate caught.
- **Pros:** removes the largest observed source of wrong auto-outcomes.
- **Cons:** date parsing of Wikipedia prose is fiddly; needs golden cases.
- **Start at:** the evidence sentences in `res["evidence"]`; extraction prompt
  in `outcome/source.py` `_SYS` could demand the join DATE and `detect.classify`
  compare it to the window bounds (`WINDOW_CLOSE` map).
- **Unpromoted examples to retest after the fix:** deals 50, 56, 64, 74, 90,
  99, 107, 114 in `ground-truth/deals.csv`.

## 0b. Club-alias normalization in outcome matching — DONE 2026-08-07

Shipped in `eb4a996`. Was scoped MEDIUM on one known victim (deal 89); measuring
the re-open candidates showed **7 of 15 qualifying collapses were this bug**, each
one scoring a journalist wrong for a call they got right:

| deal | rumoured | resolver found |
|---|---|---|
| 70 Harry Wilson | Leeds | Leeds United |
| 192 / 231 | West Ham | West Ham United |
| 205 Laurent Mendy | Hearts | Heart of Midlothian |
| 271 Juanlu | Bournemouth | "Premier League side Bournemouth" |
| 174 / 251 | Chelsea | "Women's Super League club Chelsea" |

`_ALIASES` gained the short/long pairs the data shows, and `_canon` now strips a
leading competition qualifier — conservatively: a competition word must precede
the club|side|team connector, so "Athletic Club" and "Club Brugge" are untouched
and bare "Milan" still refuses to resolve. `outcome/detect.py`.

**Still open:** `same_club("SC Paderborn 07", "Paderborn")` is False (the numeric
suffix survives `_STRIPPABLE`). It did not block the Baur merge, which matched on
`to_club`. Fix it if a false collapse ever traces to it.

## 0c. Destination-aware claim clustering (hijack conflation) — MEDIUM

- **What:** claims about DIFFERENT destination clubs for the same player+window
  land on one deal row, so a collapse verdict for destination A punishes
  journalists who correctly reported the move to destination B.
- **Why:** 2026-07-15 review, deal 68 (Monga, Leicester→Arsenal, collapsed):
  Sky's here_we_go "Man City sign Monga" and Guardian's agreement were attached
  to the Arsenal deal and would have scored as failures despite being right.
  Same shape as deal 148 (Manzambi: Newcastle agreement then Villa hijack).
  Promotion of 68 was skipped to avoid the mis-scoring.
- **Start at:** `cluster.deal_key(player, window)` — either add destination to
  the key (splits deals, matches the hand-labelled hijack pairs already in
  ground truth) or make the bridge assign claims to a per-destination deal.
- **Retest after fix:** deals 68 and 148.
- **DEFERRED 2026-08-07, deliberately, against item 0's "do them together".**
  `to_club` was proven volatile that day: deal 124 carried a destination nine
  claims out of date because the ledger froze it at row creation, and that stale
  value is what produced a factually wrong `collapsed`. Making a field with that
  failure mode part of a deal's IDENTITY would build on sand, and it contradicts
  `cluster.py`'s own rationale ("to_club is the volatile field; the player is the
  stable identity"). Item 0's merge rule is different in kind: it needs only ONE
  of the two clubs to match, and its 2+-candidate guard bounds any `to_club`
  error to over-refusal, never mis-attribution.
  **Revisit once the refresh in `bridge.refresh_and_reopen` has been running long
  enough that destinations can be trusted.**

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
