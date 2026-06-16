#!/usr/bin/env python3
"""
Static leaderboard site generator (Pitch design system, see site/theme.py).

Reads scoring/leaderboard.json and writes docs/index.html as a single
self-contained file (no server, no build step, no JS dependencies). GitHub Pages
serves the docs/ folder.

Re-run after each scoring pass:
    python scoring/score.py ground-truth/journalist_claims.csv   # regenerate JSON
    python site/build_leaderboard.py                             # regenerate docs/index.html
"""

import html
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))

import theme

DATA = ROOT / "scoring" / "leaderboard.json"
OUT = ROOT / "docs" / "index.html"

# Heuristic: the shipped sample uses these fictional source names. If any appear,
# show a banner so a sample render is never mistaken for real data.
SAMPLE_NAMES = {"TierOne_Reporter", "Hype_Account_99", "Cautious_Beat"}

PAGE_CSS = """
  .spotlight{background:linear-gradient(180deg,#fff,#f1fcf6);border:1px solid #bbf0d6;border-radius:22px;
             padding:24px 22px;text-align:center;box-shadow:0 10px 30px rgba(18,183,106,.10)}
  .sl-label{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
            color:var(--gold);margin-bottom:12px}
  .sl-ring{position:relative;width:120px;height:120px;margin:0 auto 14px}
  .sl-ring svg{display:block}
  .sl-ring .ring .p{animation:ringgrow 1.1s cubic-bezier(.2,.8,.2,1) both}
  .sl-score{line-height:1}
  @keyframes ringgrow{from{stroke-dashoffset:var(--circ)}}
  .sl-score{position:absolute;inset:0;display:grid;place-items:center;font-family:'Space Grotesk';
            font-weight:700;font-size:32px;color:var(--ink)}
  .sl-score s{font-size:14px;text-decoration:none;opacity:.5}
  .sl-name{font-weight:700;font-size:18px}
  .sl-sub{color:var(--muted);font-size:13px;margin-top:3px}
  .row{display:grid;grid-template-columns:46px 1fr 220px 96px;align-items:center;gap:18px;
       background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px 20px;
       margin-bottom:10px;box-shadow:var(--shadow);transition:transform .15s ease,box-shadow .15s ease}
  .row:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(16,24,40,.10)}
  .row.r1{border-color:#fde7b3;background:linear-gradient(0deg,#fff,#fffaf0)}
  .rank{font-family:'Space Grotesk';font-weight:700;font-size:20px;color:var(--muted);text-align:center}
  .row.r1 .rank,.row.r2 .rank,.row.r3 .rank{color:#fff;border-radius:999px;width:32px;height:32px;
       display:grid;place-items:center;margin:0 auto;font-size:15px;box-shadow:var(--shadow)}
  .row.r1 .rank{background:linear-gradient(135deg,#f59e0b,#fbbf24)}
  .row.r2 .rank{background:linear-gradient(135deg,#94a3b8,#cbd5e1)}
  .row.r3 .rank{background:linear-gradient(135deg,#c2703a,#e0975a)}
  .name{font-weight:700;font-size:17px}
  .sub{color:var(--muted);font-size:13px;margin-top:2px}
  .score{font-family:'Space Grotesk';font-weight:700;font-size:30px;text-align:right}
  .score s{font-size:14px;text-decoration:none;opacity:.55}
  @media(max-width:640px){.row{grid-template-columns:34px 1fr 84px;gap:12px}.track{display:none}}
"""


def tier(score):
    if score >= 75:
        return theme.GREEN
    if score >= 60:
        return theme.AMBER
    return theme.RED


def ring(pct, c0, c1):
    """Animated circular progress ring (SVG) for the spotlight score."""
    r = 54
    circ = 2 * math.pi * r
    off = circ * (1 - min(100, max(0, pct)) / 100)
    return (f'<svg class="ring" viewBox="0 0 120 120" width="120" height="120">'
            f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="#e6f4ec" stroke-width="11"/>'
            f'<circle class="p" cx="60" cy="60" r="{r}" fill="none" stroke="url(#g)" stroke-width="11" '
            f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{off:.1f}" '
            f'style="--circ:{circ:.1f}px" transform="rotate(-90 60 60)"/>'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{c0}"/><stop offset="1" stop-color="{c1}"/>'
            f'</linearGradient></defs></svg>')


def row_html(i, r):
    s = r["score"]
    c0, c1 = tier(s)
    bar = max(2, min(100, s))
    name = html.escape(r["source"])
    early = f" &middot; +{r['earliness_bonus']:.1f} scoop" if r.get("earliness_bonus") else ""
    cls = f"row r{i}" if i <= 3 else "row"
    return f"""
      <div class="{cls}">
        <div class="rank">{i}</div>
        <div>
          <div class="name">{name}</div>
          <div class="sub">{r['n_claims']} resolved claims &middot; raw {r['raw_accuracy']:.0f}%{early}</div>
        </div>
        <div class="track"><div class="fill" style="width:{bar}%;background:linear-gradient(90deg,{c0},{c1})"></div></div>
        <div class="score" style="color:{c0}">{s:.0f}<s>%</s></div>
      </div>"""


def main():
    if not DATA.exists():
        raise SystemExit(f"No data at {DATA}. Run scoring/score.py first.")
    d = json.loads(DATA.read_text(encoding="utf-8"))
    board = d.get("leaderboard", [])
    is_sample = any(r["source"] in SAMPLE_NAMES for r in board)
    n = d.get("n_claims", 0)

    rows = "".join(row_html(i, r) for i, r in enumerate(board, 1)) or \
        '<div class="empty">No scored journalists yet. Fill journalist_claims.csv and run score.py.</div>'

    banner = ('<div class="banner">Showing <b>sample data</b> with fictional sources. '
              'Replace journalist_claims.sample.csv with real claims, re-run score.py, then rebuild.</div>'
              ) if is_sample else ""
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")

    spotlight = ""
    if board:
        t = board[0]
        c0, c1 = tier(t["score"])
        spotlight = f"""<aside class="spotlight">
        <div class="sl-label">&#9733; Top of the table</div>
        <div class="sl-ring">{ring(t['score'], c0, c1)}<div class="sl-score"><span>{t['score']:.0f}<s>%</s></span></div></div>
        <div class="sl-name">{html.escape(t['source'])}</div>
        <div class="sl-sub">{t['n_claims']} resolved claims</div>
      </aside>"""

    page = f"""{theme.head("Transfer Truth — Reliability Leaderboard", PAGE_CSS)}
<body>
  {theme.header("leaderboard")}
  <div class="wrap">
    <div class="hero">
      <div class="hero-l">
        <h1 class="h">Who actually <em>calls it right?</em></h1>
        <p class="lede">Football journalists, ranked by how often their transfer calls come true —
           scored against real outcomes, not vibes.</p>
        <div class="chips">
          <span class="chip">{n} resolved claims</span>
          <span class="chip alt">Brier-scored</span>
          <span class="chip alt">Pop. avg {d.get('population_mean_accuracy','?')}%</span>
        </div>
      </div>
      {spotlight}
    </div>
    {banner}
    <div class="secthead"><h2>Reliability leaderboard</h2><h2>Accuracy</h2></div>
    <div class="board">{rows}</div>
    <p class="foot"><b>How the score works.</b> Every claim is graded by a Brier score against
      what actually happened: saying &ldquo;here we go&rdquo; (99%) on a deal that collapsed is
      punished hard; &ldquo;in talks&rdquo; (35%) on the same deal barely dents you. Scores are
      shrunk toward the average by sample size (K={d.get('k','?')}) so a spammer with a few lucky
      calls can&rsquo;t top the table, and a small bonus rewards genuinely breaking a deal early.
      Generated {gen}. Static file, no backend.</p>
  </div>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}  ({len(board)} journalists"
          f"{', SAMPLE data' if is_sample else ''})")


if __name__ == "__main__":
    main()
