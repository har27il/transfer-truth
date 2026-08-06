# TODOS

Deferred work with full context. Each entry: what, why, and where to start —
so a future session (or a future you) doesn't have to re-derive the reasoning.

## 0. Split players: bare surname vs full name are separate deals — HIGH

- **What:** `cluster.deal_key(player, window)` keys on `normalize_name`
  (`ingest/cluster.py:15`), which handles case/accents but NOT the full-name vs
  surname case. `"Olise"` and `"Michael Olise"` hash to different deals, so one
  transfer becomes two rows with the claims split between them.
- **Why it matters (measured 2026-08-06, 13 players affected):**
  - `Bruno Guimaraes -> Arsenal` carries **14** claims (incl. "Arsenal agree £75m
    fee with Newcastle"); `Guimaraes -> Arsenal` carries **2**. Same deal.
  - Worse, the split separates DISSENT from ASSENT. `Olise -> Real Madrid`
    (deal 79) holds a single claim — "Real Madrid deny interest" — while the 3
    interest claims sit on `Michael Olise` (deal 57). Deal 57's meter therefore
    never sees the denial and reads too HIGH, and deal 79 looks like a dead
    rumour. The corroboration boost in `meter.py` also needs 2+ sources on ONE
    deal, so splitting suppresses it on both halves.
  - The feed renders the same transfer twice, which reads as broken to anyone
    who follows football.
- **Do NOT "just match on surname".** `Diomande` is genuinely ambiguous in the
  live data — both `Ousmane Diomande` and `Yan Diomande` exist as real, separate
  deals. A blind surname merge attributes one player's claims to another, which
  is a ground-truth corruption, not a display bug.
- **Rule that was validated against the real data:** merge only when
  **surname + destination club + window** all match. Result: 9 safe merges
  (Guimaraes, Olise, Munoz, Raskin, Palestra, Makhanya, Hogh, Fosso, Baur),
  4 correctly refused — `Diomande` (ambiguous), `Bowie` and `Kroupi` (different
  destinations, possibly a hijack), `Gusto` (both destinations null, unconfirmable).
- **Start at:** `ingest/cluster.py` `deal_key` — note it currently receives only
  `(player, window)`, so destination-aware merging changes the key signature.
  **This is the same change as 0c below** (destination-aware clustering for the
  hijack case); do them together, once, rather than twice.
- **Gate:** touches clustering, which feeds scoring — use plan mode per CLAUDE.md.
  Needs a backfill decision for the 13 already-split rows in `deals.csv`.

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

## 0b. Club-alias normalization in outcome matching — MEDIUM

- **What:** `same_club("Brighton", "Brighton & Hove Albion")` must be True.
- **Why:** deal 89 resolved COLLAPSED with evidence "joined Brighton & Hove
  Albion, not Brighton" — the rumoured and actual club were the same club.
  Any short-form/long-form pair (Spurs/Tottenham Hotspur, Wolves/Wolverhampton
  Wanderers) can produce a false collapse, which unfairly punishes journalists.
- **Start at:** `outcome/detect.py` same_club; consider reusing the engine
  prompt's canonicalization table or a small alias map shared via a module.

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
