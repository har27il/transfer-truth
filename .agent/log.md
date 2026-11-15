# Agent log

One entry per task: date, commit, what changed, how it was verified.

## 2026-11-15 — e4a0045 — README matches how the project runs today

- **Changed:** README.md only. Automation now describes `update-site.yml` and
  `golden-eval.yml` (it named `outcomes.yml`, which no longer exists, and said
  "manual-run only"); dropped the stale "38 tests" count; noted that the build
  commands overwrite tracked `docs/` pages and that a fresh clone's feed is demo data.
- **Verified:** a one-off script that checks every path the README names exists:
  on main 885d1de, 26 of 27 (MISSING `.github/workflows/outcomes.yml`), exit 1;
  on this branch, 30 of 30, exit 0. `pytest -q`: 222 passed, 3 skipped on both.
  The README's commands (score.py, build_leaderboard.py, build_feed.py) ran
  with exit 0 on a throwaway copy of main; the feed built as DEMO, as the README now says.
- **Not proven:** that the README reads clearly to a newcomer.
