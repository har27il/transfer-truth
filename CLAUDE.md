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
- **Outcome resolution is positive-evidence-only.** `verified=auto` rows in
  `ground-truth/deals.csv` are PROPOSED; they don't score until promoted to `verified=YES`.
  "Here we go" / agreed ≠ officially completed — never mark a deal `completed` without
  positive evidence (the resolver's Wikipedia check).
- **Two data planes:** `ingest/ingest.db` (live claims → `docs/feed.html`) and
  `ground-truth/deals.csv` (resolved outcomes → leaderboard). The feed joins resolved
  deals via `cluster.deal_key(player, window)`.
- Tests gate every auto-write: `pytest -q` runs before the cron commits.

## Build
- Feed: `python site/build_feed.py` · Leaderboard: `python site/build_leaderboard.py`
- Shared theme: `site/theme.py` (both pages pull head + header from here).
- Tests: `python -m pytest -q`
