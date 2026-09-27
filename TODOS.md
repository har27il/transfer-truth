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
- **2026-09-27: now the top data risk.** The window has closed, so collapses are
  finally resolvable, and the ledger's 2026 verified outcomes are 100 completions
  and 0 collapses (RESEARCH.md). In the promotion queue, 39 of 189 proposals cite
  only pre-2026 years: 31 collapses (15 "joined elsewhere", e.g. deal 418 Elliott
  → Valencia on a 2025 loan) and 8 completions. `promote.py` now WARNS on these
  (display only), which makes the human review safe today. The resolver fix below
  stops new ones.
- **Proposed design (awaiting go-ahead; resolution logic → plan mode per CLAUDE.md):**
  1. `outcome/source.py` prompt asks for the `move_date` (ISO, or null) of the
     move the evidence describes, alongside `evidence`.
  2. `detect.classify` returns UNKNOWN ("evidence outside the window") when
     `status == moved` and `move_date` falls outside the window's bounds. Add
     `WINDOW_OPEN` next to `WINDOW_CLOSE`. Only ever turns a verdict into UNKNOWN,
     never the reverse, so positive-evidence-only is strengthened, not bent.
  3. `status == stayed`: unchanged. A stay is legitimately undated.
  4. Tests: fixtures for deals 418 and 64 (stale "joined elsewhere" → UNKNOWN)
     plus a genuine in-window move that must still resolve.
  5. Existing proposals: not touched by code. They stay in the queue behind the
     warning, for a human to decide.

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

## 0d. Post-deadline claims are filed under the closed window — MEDIUM

- **What:** `ingest/pipeline.py` keys every claim to `DEFAULT_WINDOW = "2026-summer"`
  (a constant, passed as `pipeline.run(window=...)`), whatever the post's date.
  `site/build_feed.py` `ACTIVE_WINDOW` mirrors it. So a post published after the
  2026-09-01 deadline still lands on a `2026-summer` deal.
- **How it contributed to the Mbappé → Liverpool bug (found 2026-09-27):** BBC's
  "I was close to joining Liverpool in 2016 - Mbappe" ran on 2026-09-22, three
  weeks after the window shut, and became deal 458 / claim 995 in `2026-summer`.
  A closed window turns a resolver "stayed" into positive evidence of a collapse
  (`outcome/detect.classify`), so the next run could have auto-collapsed 458 and
  queued claim 995 to score against BBC Sport. The root cause was the
  retrospective story (fixed: `ingest/retro.py` + the prompt's PAST EVENTS rule);
  this is the contributing bug, left open by decision.
- **Side effect to know:** deal 458 is now `historical` under the key
  `kylian mbappe|2026-summer`, and the key is player + window only, so any later
  Mbappé claim attaches to that row until claims get the right window. The feed
  still shows such a claim live (the retrospective filter is per-claim), but in the
  ledger it sits on a deal that never resolves.
- **Start at:** derive the window from `claim_date` (the window containing it, or
  the next one when between windows) using `site/windows.py`'s calendar, and keep
  `ACTIVE_WINDOW` in step. Mind item 0's warning: `deal_key` is stored in the
  cached ingest.db, so a window change re-keys NEW claims only.

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
