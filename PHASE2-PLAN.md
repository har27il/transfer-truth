# Phase 2 Plan — Automated Outcome Detection

Status: REVIEWED (/plan-eng-review)
Scope: auto-resolve whether a rumored deal COMPLETED or COLLAPSED, so the
leaderboard self-updates and the live meter / ML model become falsifiable later.

> SCOPE REVERSAL (cross-model decision): the initial Step 0 scope was
> "ingestion + live meter only." The outside voice argued a live probability
> meter with no outcomes is unfalsifiable (the same "no ground truth = tuning by
> vibes" problem flagged on the ML model). User accepted: build OUTCOME DETECTION
> first. Ingestion + live meter move to Phase 3.

## Why this first
Everything downstream (live probability meter, ML predictor) needs resolved
outcomes to validate against. Phase 1 already proved the Brier scorer works on
hand-resolved outcomes. Automating outcome resolution closes the loop: new deals
flow in -> get resolved -> scorer + leaderboard update with no hand-labeling.

## What already exists (reuse, do not rebuild)
- `scoring/score.py` — Brier + shrinkage + earliness (reads deals.csv + claims)
- `site/build_leaderboard.py` — static renderer
- `engine/transfer-analyst-system-prompt.md` — extraction prompt (for the 'official' stage signal)
- `ground-truth/deals.csv` — has `outcome` (completed|collapsed|unknown) + `verified`

## Architecture (decisions locked in review)
```
   free transfers/squad API  +  official-announcement signal
                       |
                       v
            outcome_detect.py  (per deal with outcome=unknown)
                       |
        +--------------+--------------+
        | positive evidence DONE?     | -> completed
        | window closed AND player    | -> collapsed
        |   not at to_club?           |
        | otherwise                   | -> stay unknown (NOT scored)
        +--------------+--------------+
                       |
        atomic write -> deals.csv (only resolved rows; verified=auto)
                       |
            score.py  ->  build_leaderboard.py  ->  site/
                       |
   GitHub Actions (daily, [skip ci], concurrency lock, commit-on-change)
```

### Decisions
- **D1 Data store:** hybrid — curated ground truth stays CSV (hand-editable, git);
  machine state (when ingestion lands) goes to SQLite. Outcomes write back to deals.csv.
- **D5 DRY:** extract `stagemap.py` (STAGE_P single source of truth); `score.py`
  and `ml/deal_predictor.py` import it. Engine prompt references it in a comment.
- **D6 Error handling:** per-item try/except; one failed lookup is logged+skipped,
  run never aborts; deals.csv written atomically (temp + rename) so a crash can't
  truncate ground truth.
- **D-safety (CRITICAL):** outcome detection requires POSITIVE evidence for both
  completed and collapsed. Ambiguous -> stays `unknown`, never scored. Wrongly
  auto-marking an outcome poisons the ground truth the whole product rests on.
- **D-safety.1 (verified gate, /review security call):** auto-resolved rows are
  written `verified=auto` and are PROPOSED only — `ground_truth.py` (shared by
  score.py + deal_predictor.py) counts `verified=YES` rows by default; auto rows
  need a human to promote them (or `--include-auto` to preview). Stops one LLM read
  from silently moving every journalist's score. Path back to full automation =
  corroboration (2+ independent signals) before auto-write.
- **D-safety.2 (wrong-page guard):** `source.resolve` requires the deal's `from_club`
  to appear on the fetched Wikipedia page before trusting any extraction; else
  `unclear`. Catches same-name collisions / disambiguation pages.
- **D4 Runner:** GitHub Actions, ~daily, `[skip ci]` on bot commits, concurrency
  lock, commit ONLY when data changed. Requires git init + GitHub. Keys as secrets.

## Components
- `stagemap.py` — shared STAGE_P (D5)
- `outcome/detect.py` — resolve a deal to completed/collapsed/unknown
- `outcome/source.py` — adapter over the chosen data source (free API or Wikipedia season list)
- `outcome/apply.py` — atomic write of resolved rows into deals.csv, then trigger rescore+rebuild
- `.github/workflows/outcomes.yml` — daily runner

## Test plan (pytest; add tests/ — no infra yet)
- detect.py: completed (positive evidence) / collapsed (window closed + elsewhere)
  / unknown (ambiguous -> NOT resolved) / source API returns nothing
- apply.py: atomic write (crash mid-write leaves deals.csv intact); idempotent re-run
- stagemap.py: score.py + deal_predictor.py import the same STAGE_P
- source.py: network error -> empty, logged, no crash
- [EVAL] engine golden set (~15 labeled posts -> expected JSON) — regression guard for prompt edits

## Failure modes
| Codepath | Failure | Test? | Handled? | User sees |
|----------|---------|-------|----------|-----------|
| detect.py | misclassify collapsed (deal completed late) | YES (req'd) | D-safety: positive-evidence-only | nothing (stays unknown) — SAFE |
| apply.py | crash mid-write corrupts deals.csv | YES (req'd) | atomic temp+rename | nothing — SAFE |
| source.py | API down / rate-limited | YES (req'd) | log+skip, retry | stale (no new resolutions) |
| Actions | bot commit retriggers workflow | n/a | [skip ci] + concurrency lock | none |

CRITICAL GAP guard: the misclassify-collapsed path MUST have both a test and the
positive-evidence rule, or it silently corrupts ground truth. Plan requires both.

## NOT in scope (deferred, with rationale)
- **Ingestion (Reddit/RSS pollers, dedup, clustering)** — Phase 3. Needs outcomes first.
- **Live probability meter** — Phase 3. Unfalsifiable until outcomes are automated.
- **Clustering on (player + to_club)** — outside voice flagged to_club as the volatile
  field; treat as a research problem when ingestion lands, likely key on player.
- **Reddit signal-quality risk** — r/soccer launders tier-1 posts with attribution
  stripped; validate signal value before investing in ingestion.
- **Gazetteer / alias-map maintenance** — ongoing manual upkeep; scope it in Phase 3.
- **X/Twitter, ML model, accounts** — unchanged from Phase 1 deferrals.

## Parallelization
- Lane A: git init + GitHub + Pages (T1) — independent
- Lane B: stagemap.py refactor (T2) — independent of detection
- Lane C: source.py -> detect.py -> apply.py -> tests (T3-T6) — sequential, shared outcome/
- Then: Actions workflow (T7) after C. Lanes A/B/C launch in parallel.

## Implementation Tasks
Synthesized from this review's findings. Run with Claude Code; checkbox as you ship.

- [x] **T1 (P1)** — repo — git init, push to GitHub, enable Pages ✅ DONE
  - Repo: https://github.com/har27il/transfer-truth (was private; made PUBLIC so free-tier
    Pages works — key/data leak-safe: key is an Actions secret, workflow is dispatch-only,
    no private data in repo). Pages serves `main /docs`: https://har27il.github.io/transfer-truth/
  - Decision (2nd eng review D2): publish from `/docs` (no workflow, no secret in publish path).
  - Files: `.gitignore`, `.gitattributes`, `README.md`, output moved `site/` -> `docs/`
- [x] **T2 (P1, human: ~30min / CC: ~10min)** — stagemap — extract STAGE_P to shared module ✅ DONE — `stagemap.py` created; score.py + deal_predictor.py import it; output unchanged
  - Surfaced by: Code Quality Issue 5 — STAGE_P duplicated in 3 files (DRY)
  - Files: `stagemap.py`, `scoring/score.py`, `ml/deal_predictor.py`
  - Verify: `python scoring/score.py ...` unchanged output; import test passes
- [x] **T3 (P1)** — outcome/source — wire a free outcome data source ✅ DONE
  - Chose **Wikipedia player pages** (free, no key) + LLM extraction via **NVIDIA NIM**
    (OpenAI-compatible, free tier — `NVIDIA_API_KEY`). Source validated live on the 3
    hardest hijack/collapse cases (Eze, Guéhi, Woltemade) — all correct, incl. window-awareness.
  - Files: `outcome/source.py` (fetch = stdlib urllib; extract = pluggable; both injectable for tests)
  - Verify: live Wikipedia fetch test passes; resolve() chains correctly
- [x] **T4 (P1)** — outcome/detect — classify completed/collapsed/unknown (positive-evidence only) ✅ DONE
  - Pure, no-network decision engine. D-safety enforced: only positive evidence resolves; else `unknown`.
  - Files: `outcome/detect.py` (+ `same_club` normalization / alias map)
  - Verify: **re-derives all 38 ground-truth outcomes** incl. 4 hijack players; all branch tests pass
- [x] **T5 (P1)** — outcome/apply — atomic write to deals.csv + rescore/rebuild ✅ DONE
  - Atomic temp+os.replace; positive-evidence-only write; `verified=auto`; idempotent; `--dry-run`.
  - Files: `outcome/apply.py`
  - Verify: crash-mid-replace test leaves deals.csv intact + no temp leak; idempotent re-run is byte-identical
- [x] **T6 (P1)** — tests — pytest suite + engine golden-set eval ✅ DONE
  - **33 tests green**, 2 gated (live Wikipedia fetch; live NIM engine eval).
  - Detect: re-derives all 38 ground-truth outcomes + branch/normalization tests.
  - Source: JSON parse hardening, offline resolve() chaining, window logic.
  - Apply: atomic-write crash safety, positive-evidence gating, idempotency, dry-run.
  - Stagemap: score.py + deal_predictor.py share one STAGE_P object (identity check).
  - **Golden set:** `engine/run.py` makes the ingestion prompt executable; `engine/golden.py`
    is a tolerant grader (stage-equivalence by implied_p, club nicknames via same_club);
    `tests/golden/cases.jsonl` = 15 labeled posts (every stage, denied, free, multi-claim,
    nickname, 2 non-transfer). Offline tests prove the grader catches each regression
    class; live eval (`TM_LLM_TESTS=1` + key) asserts pass-rate >= 80%, field-acc >= 90%.
- [x] **T7 (P2)** — CI — Actions workflow, commit-on-change ✅ DONE (manual-trigger now)
  - 2nd eng review D1: `workflow_dispatch` only; daily `schedule:` line present but COMMENTED
    until Phase 3 ingestion creates `unknown` deals (a cron with no inputs is pure waste).
  - Hardening: `permissions: contents:write` (least privilege), concurrency lock, `pytest`
    gate before any auto-write, secret-leak guard (`git grep nvapi-`), commit `[skip ci]`.
  - Files: `.github/workflows/outcomes.yml`, `requirements.txt`
  - Verify: needs `gh secret set NVIDIA_API_KEY`, then "Run workflow" in Actions tab.
- [ ] **T8 (P3, human: ~1h / CC: ~10min)** — perf — recompute only changed clusters (later, at scale)
  - Surfaced by: Performance Review — full recompute fine now, matters at 10k+ deals
  - Files: `scoring/score.py`
  - Verify: n/a until volume grows

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR | run 1: 6 issues; run 2 (T1/T7 infra): 2 arch findings, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**Run 2 (T1/T7 runner/CI/deploy):**
- Step 0 scope: T7 cron has NO inputs until Phase 3 ingestion (all 38 deals already verified).
  Decided (D1): ship `workflow_dispatch` only; daily `schedule:` stays commented. Boring + reversible.
- Arch (D2): GitHub Pages can't serve `site/` natively → publish from `/docs` (no workflow, no
  secret in publish path = cannot leak the NIM key). Output moved `site/` -> `docs/`.
- Constraint hit: free-tier Pages needs a public repo. User chose public; key/data leak-safe
  (key is encrypted Actions secret, workflow dispatch-only so no fork-PR exfiltration, no private
  data in repo, CI secret-leak guard). Repo flipped private -> PUBLIC.
- NOT in scope: live `schedule:` cron, ingestion (Phase 3), private-repo Pages (needs Pro).
- Failure modes covered: broken code auto-writing (pytest gate), leaked key (git-grep guard),
  concurrent writes (concurrency lock), commit loop (`[skip ci]`).
- **CROSS-MODEL:** Codex not installed; outside voice not separately spawned for this small
  infra slice (1 workflow file). Findings stand on the single-model review.
- **VERDICT:** ENG CLEARED — T1 + T7 implemented and pushed. Repo live, Pages building,
  workflow registered (manual trigger). Remaining: `gh secret set NVIDIA_API_KEY` before first run.

NO UNRESOLVED DECISIONS
