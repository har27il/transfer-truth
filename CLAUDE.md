# Transfer Truth

Football transfer-rumour **credibility** site: extracts structured claims from RSS
rumour text via LLM, scores journalist reliability (Brier), shows a per-deal
probability meter, and a tiered feed (contested → agreed → cold → done). Static
pages on GitHub Pages, rebuilt by a daily GitHub Actions cron.

## Design System
Always read **DESIGN.md** before making any visual or UI decision. All fonts, colours,
spacing, layout, and aesthetic direction are defined there. Do not deviate without
explicit user approval. In QA, flag any code that doesn't match DESIGN.md.
- The one rule: ink-on-paper monochrome, **the credibility meter is the only colour**.
- **Mobile is a hard requirement** — design every viewport (see DESIGN.md → Responsive).
  Never ship "just stacked on mobile"; test 360 / 414 / 768 / 1180px.

## Guardrails (do not break)
- **Secrets:** `NVIDIA_API_KEY` lives only in GitHub Secrets or a local gitignored
  `.env` — never in tracked files, never in chat. Repo is public by design.
  Same rule applies to `LANGSMITH_API_KEY` once observability lands — extend the
  secret-guard grep pattern (see Pipeline step 11) to cover the `lsv2_pt_` prefix too.
- **Outcome resolution is positive-evidence-only.** `verified=auto` rows in
  `ground-truth/deals.csv` are PROPOSED; they don't score until promoted to `verified=YES`.
  "Here we go" / agreed ≠ officially completed — never mark a deal `completed` without
  positive evidence (the resolver's Wikipedia check, `outcome/apply.py`).
- **Two data planes:** `ingest/ingest.db` (live claims → `docs/feed.html`, cached via
  `actions/cache@v4`, never committed to git) and `ground-truth/deals.csv` (resolved
  outcomes → leaderboard, committed). The feed joins resolved deals via
  `cluster.deal_key(player, window)`.
- Tests gate every auto-write: `pytest -q` runs first; nothing else runs on a red test.
- **Do not claim something is fixed, passing, or scoring correctly without having run
  it and shown the actual output.**

## Build
- Feed: `python site/build_feed.py` · Leaderboard: `python site/build_leaderboard.py`
- Shared theme: `site/theme.py` (both pages pull head + header from here).
- Tests: `python -m pytest -q`

## Pipeline (update-site.yml — the real orchestrator)
Runs at 06:17/11:17/16:17 UTC or via `workflow_dispatch`. This IS the orchestration
layer — it's a straight line, not a graph, and that's correct for what it does:

1. checkout → setup-python 3.12 → `pip install -r requirements.txt`
2. `pytest -q` — hard gate
3. Restore `ingest/ingest.db` from `actions/cache@v4`
4. `python ingest/pipeline.py` — RSS pull, NIM extraction (concurrent), cluster, write ingest.db
5. *(dispatch-only)* `python ingest/backfill.py` if `backfill_since` passed
6. `python ingest/bridge.py` — proposes new `deals.csv` rows as `verified=auto`
7. *(skipped on backfill dispatches)* `python outcome/apply.py` — Wikipedia positive-evidence resolution
8. `scoring/score.py` → `site/build_leaderboard.py` → `site/build_feed.py`
9. Secret guard: `git grep` for `nvapi-` pattern (excluding `.github`), fails loud if found
10. Commit-on-change: stages exactly `ground-truth/deals.csv`, `ground-truth/journalist_claims.csv`,
    `docs/`, `scoring/leaderboard.json` → commits as `transfer-bot` → rebase → push

**LangGraph: evaluated, not adopted.** No LLM call here decides its own next step —
both call sites (see Observability) are one-shot calls with deterministic retry, not
branching reasoning chains. GitHub Actions already gives per-step failure isolation
that a monolithic graph process would lose. Do not reintroduce this migration for the
existing linear pipeline. **Scoped exception:** if the market-signal feature (reconciling
conflicting odds sources, deciding which sources to check) ships, that's genuinely
agentic control flow — LangGraph belongs there specifically, not retrofitted here.

## Observability (LangSmith — still worth doing, independent of the above)
Two real call sites to trace, both via NIM:
- `engine/run.py:94-95` (`_nim_complete`, `NIM_MODEL`) — claim extraction, called from
  `ingest/pipeline.py:110` → `analyze()` at `engine/run.py:104-115`
- `outcome/source.py:229` — outcome resolution, `RESOLVER_MODEL`

Wrap both with `@traceable`. Env vars (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`,
`LANGSMITH_PROJECT`) as GitHub Actions secrets/env. If `tests/golden/` exists, add
any fixed production extraction miss there as a regression case, not just a patch.

## Working style
- **Explore, then plan, then code** for anything touching the ground-truth
  gate, scoring math, or the pipeline YAML — use plan mode, show the plan,
  wait for go-ahead before editing.
- Small, verifiable steps over sweeping rewrites.
- Reference files by path/line — don't paste large blocks back in conversation.