# Agent log

One entry per task: date, commit, what changed, how it was verified.

## 2026-11-15 — e4a0045 (+ a wording fix in the commit after this log's) — README matches how the project runs today

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

## 2026-11-22 — c70aa77 — Dependabot alerts, security updates, monthly grouped config

- **Changed:** repo settings (live at once): Dependabot alerts and security updates
  turned on. `.github/dependabot.yml` (live once on main): monthly, one group each
  for pip and github-actions; ignore rules mirror the caps in requirements.txt.
- **Verified:** settings read back with gh, before -> after: vulnerability-alerts
  404 -> 204, automated-security-fixes enabled false -> true (paused false),
  dependabot/alerts 403 -> 200. A one-off config check (schema, grouping, every
  published stable release against the caps) exits 1 without the file and on 4 broken
  copies, 0 with it. `pytest -q`: 222 passed, 3 skipped.
- **Known gap:** requirements.txt has ranges and no lockfile, so GitHub records the
  Python packages without versions. The ranges admit versions with published
  advisories (langsmith <0.8.18, pytest <9.0.3) and 0 alerts were raised; Dependabot
  source also refuses pip security-update PRs without a known version. The 35
  packages the 2026-11-19 cron run installed had no advisories when queried.
- **Not proven:** that Dependabot accepts the file or opens a PR (needs main).
  Dependabot PRs run no tests; actions/cache bumps need a cache-restore check,
  because ingest.db lives only in that cache.
- **Review fixes:** d47c826 added `.python-version`, because without it Dependabot
  resolves for Python 3.15 (seen in run 45015006815, job 137185363244) and could set
  floors the 3.12 cron can't install; it also corrected comments in dependabot.yml
  and requirements.txt. Its value, 3.12.16, was wrong: Dependabot's bundled pyenv
  (v2.6.16) doesn't list it, so Dependabot ignores it, and the check for it passed
  falsely by reading the wrong list. 4a3f25f sets 3.12.12 and corrects three
  comments in dependabot.yml. The corrected check replays that pyenv list: no file
  and 3.12.16 -> 3.15.2 (fail), 3.12.12 -> 3.12.16 (pass). After merge, the first
  pip job's log must say "Dependabot is using Python version '3.12.16'".
- **Also, at Harshil's request:** CLAUDE.md now says to comment
  `@coderabbitai review` on every PR opened in this repo.
- **Undo:** `gh api -X DELETE` on the repo's automated-security-fixes, then
  vulnerability-alerts; revert the PR merge commit for the files.
