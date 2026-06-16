#!/usr/bin/env python3
"""
Static leaderboard site generator.

Reads scoring/leaderboard.json and writes docs/index.html as a single
self-contained file (no server, no build step, no JS dependencies). Open it
directly in a browser, or let GitHub Pages serve the docs/ folder.

This script lives in site/ but the PUBLISHED output is docs/index.html, because
GitHub Pages serves a branch folder (root or /docs) with no workflow and no
secret — the safest publish path (it cannot leak the NIM API key).

Re-run after each scoring pass:
    python scoring/score.py ground-truth/journalist_claims.csv   # regenerate JSON
    python site/build_leaderboard.py                             # regenerate docs/index.html

Usage:
    python site/build_leaderboard.py
"""

import json, html
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "scoring" / "leaderboard.json"
OUT = ROOT / "docs" / "index.html"

# Heuristic: the shipped sample uses these fictional source names. If any appear,
# show a banner so a sample render is never mistaken for real data.
SAMPLE_NAMES = {"TierOne_Reporter", "Hype_Account_99", "Cautious_Beat"}


def colour(score):
    if score >= 75: return "#22c55e"      # green
    if score >= 60: return "#eab308"      # amber
    return "#ef4444"                       # red


def row_html(i, r):
    s = r["score"]
    bar = max(2, min(100, s))
    name = html.escape(r["source"])
    early = f" &middot; +{r['earliness_bonus']:.1f} scoop" if r.get("earliness_bonus") else ""
    return f"""
      <div class="row">
        <div class="rank">{i}</div>
        <div class="who">
          <div class="name">{name}</div>
          <div class="meta">{r['n_claims']} resolved claims &middot; raw {r['raw_accuracy']:.0f}%{early}</div>
        </div>
        <div class="bar"><span style="width:{bar}%;background:{colour(s)}"></span></div>
        <div class="score" style="color:{colour(s)}">{s:.0f}<span>%</span></div>
      </div>"""


def main():
    if not DATA.exists():
        raise SystemExit(f"No data at {DATA}. Run scoring/score.py first.")
    d = json.loads(DATA.read_text(encoding="utf-8"))
    board = d.get("leaderboard", [])
    is_sample = any(r["source"] in SAMPLE_NAMES for r in board)

    rows = "".join(row_html(i, r) for i, r in enumerate(board, 1)) or \
        '<div class="empty">No scored journalists yet. Fill journalist_claims.csv and run score.py.</div>'

    banner = ('<div class="banner">⚠ Showing <b>sample data</b> with fictional '
              'sources. Replace journalist_claims.sample.csv with real claims, '
              're-run score.py, then rebuild.</div>') if is_sample else ""

    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transfer Truth — Journalist Reliability Leaderboard</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0b0f17; color:#e7ecf3;
         font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
  h1 {{ font-size:30px; margin:0 0 6px; letter-spacing:-.02em; }}
  .sub {{ color:#9aa7b8; margin:0 0 10px; }}
  nav a {{ color:#7aa2f7; text-decoration:none; font-size:14px; }}
  .banner {{ background:#3a2a08; border:1px solid #7c5e12; color:#f5d98a;
            padding:10px 14px; border-radius:10px; font-size:14px; margin-bottom:24px; }}
  .row {{ display:grid; grid-template-columns:34px 1fr 120px 84px; align-items:center;
         gap:14px; padding:14px 12px; border-bottom:1px solid #1b2433; }}
  .row:first-of-type {{ border-top:1px solid #1b2433; }}
  .rank {{ color:#6b7888; font-weight:700; text-align:center; }}
  .name {{ font-weight:650; }}
  .meta {{ color:#7e8b9c; font-size:12.5px; }}
  .bar {{ background:#161e2b; border-radius:6px; height:8px; overflow:hidden; }}
  .bar span {{ display:block; height:100%; border-radius:6px; }}
  .score {{ text-align:right; font-weight:750; font-size:24px; }}
  .score span {{ font-size:13px; opacity:.7; margin-left:1px; }}
  .empty {{ color:#7e8b9c; padding:30px 0; }}
  .method {{ margin-top:34px; color:#8a97a8; font-size:13.5px; border-top:1px solid #1b2433;
            padding-top:18px; }}
  .method b {{ color:#c3cedb; }}
  code {{ background:#161e2b; padding:1px 5px; border-radius:4px; }}
</style></head>
<body><div class="wrap">
  <h1>Journalist Reliability Leaderboard</h1>
  <p class="sub">Who actually calls transfers right — scored against real outcomes,
     not vibes. {d.get('n_claims', 0)} resolved claims.</p>
  <nav><a href="feed.html">Live rumour feed &rarr;</a></nav>
  {banner}
  <div class="board">{rows}</div>
  <div class="method">
    <p><b>How the score works.</b> Every claim is graded by a Brier score against
    what actually happened: saying “here we go” (99%) on a deal that collapsed is
    punished hard; “in talks” (35%) on the same deal barely dents you. Scores are
    shrunk toward the average by sample size (<code>K={d.get('k','?')}</code>) so a
    spammer with a few lucky calls can’t top the table, and a small bonus rewards
    genuinely breaking a deal early.</p>
    <p>Population mean accuracy: <b>{d.get('population_mean_accuracy','?')}%</b>.
    Generated {gen}. Static file — no backend.</p>
  </div>
</div></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}  ({len(board)} journalists"
          f"{', SAMPLE data' if is_sample else ''})")


if __name__ == "__main__":
    main()
