#!/usr/bin/env python3
"""
Static reliability-leaderboard generator (editorial system, see DESIGN.md).

Reads scoring/leaderboard.json and writes docs/index.html as a single self-contained
file (no server, no build step, no JS beyond the theme toggle). Renders the same
broadsheet front page as the feed: a front-page nameplate, the top reporter as a lede
spotlight, a standings table, and a rail that ties back to the live desk.

The leaderboard ranks *reporters*, not deals — so the score is the single green brand
thread (green = verified/true throughout), NOT the 3-stop deal meter spectrum. A reporter
is never painted amber/red; rank position and bar length carry "who's better". This keeps
the number identical to how the feed's rail shows the same standings.

Re-run after each scoring pass:
    python scoring/score.py ground-truth/journalist_claims.csv   # regenerate JSON
    python site/build_leaderboard.py                             # regenerate docs/index.html
"""

import html
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))

import theme

DATA = ROOT / "scoring" / "leaderboard.json"
OUT = ROOT / "docs" / "index.html"

# Heuristic: the shipped sample uses these fictional source names. If any appear,
# show a banner so a sample render is never mistaken for real data.
SAMPLE_NAMES = {"TierOne_Reporter", "Hype_Account_99", "Cautious_Beat"}

# Page-specific CSS, scoped under .board so nothing collides with the feed page (which
# scopes its own under .feed). Mirrors the feed's editorial chrome — nameplate, broadsheet
# grid, lede spotlight, standings rows — so the two pages read as one masthead.
PAGE_CSS = """
  .board{max-width:var(--frame);margin:0 auto;padding:0 32px}
  /* nameplate (shared visual language with the feed) */
  .board .nameplate{text-align:center;padding:30px 0 0}
  .board .rule{height:0;border-top:1px solid var(--ink)}
  .board .rule.thin{border-top:1px solid var(--hair)}
  .board .np-name{font-family:var(--serif);font-optical-sizing:auto;font-weight:900;
                  font-size:clamp(40px,7vw,80px);line-height:.96;letter-spacing:-.03em;margin:14px 0 6px}
  .board .np-slogan{font-family:var(--serif);font-style:italic;font-weight:400;font-size:17px;
                    color:var(--muted);margin:0 0 14px}
  .board .np-line{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:10px;
                  font-size:11.5px;text-transform:uppercase;letter-spacing:.18em;color:var(--muted);padding:9px 0}
  .board .np-line .sep{opacity:.4}
  /* broadsheet grid */
  .board .grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:0;margin-top:8px}
  .board .rail{border-left:1px solid var(--hair);margin-left:40px;padding-left:40px}
  @media(max-width:900px){.board .grid{grid-template-columns:1fr}
    .board .rail{border-left:0;margin-left:0;padding-left:0;margin-top:24px;border-top:2px solid var(--ink);padding-top:8px}}
  /* lede spotlight: the top reporter */
  .board .lede{padding:34px 0 32px;border-bottom:1px solid var(--hair)}
  .board .kicker{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:var(--ink);
                 margin:0 0 16px;display:flex;align-items:center;gap:10px}
  .board .kicker::before{content:"";width:24px;height:2px;background:var(--likely)}
  .board .lede h2{font-family:var(--serif);font-optical-sizing:auto;font-weight:600;
                  font-size:clamp(36px,6vw,68px);line-height:1.0;letter-spacing:-.025em;margin:0 0 16px}
  .board .dek{font-size:18px;color:var(--muted);max-width:54ch;margin:0 0 26px;line-height:1.5}
  .board .dek em{font-style:italic;color:var(--ink)}
  /* the score gauge — green brand thread, reporter semantics (not a deal probability) */
  .board .meter{display:flex;align-items:center;gap:24px;max-width:600px;flex-wrap:wrap}
  .board .readout{flex:none}
  .board .readout .pct{display:block;font-weight:600;font-size:50px;letter-spacing:-.02em;line-height:.92;
                       color:var(--likely);font-variant-numeric:tabular-nums}
  .board .readout .pct s{font-size:19px;text-decoration:none;color:var(--muted);font-weight:500}
  .board .readout .verdict{display:block;margin-top:6px;font-size:12px;font-weight:700;text-transform:uppercase;
                           letter-spacing:.08em;color:var(--ink)}
  .board .meter .col{flex:1;min-width:180px}
  .board .meter .track{position:relative;height:9px;background:var(--hair);border-radius:2px;overflow:hidden}
  .board .meter .track .t{position:absolute;top:0;bottom:0;width:1px;background:color-mix(in srgb,var(--paper) 55%,var(--hair))}
  .board .meter .fill{height:100%;border-radius:2px;background:var(--likely)}
  .board .scale{display:flex;justify-content:space-between;margin-top:8px;font-size:11px;
                text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
  /* section head + standings rows */
  .board .sec{display:flex;align-items:baseline;justify-content:space-between;margin:32px 0 2px;
              padding-bottom:8px;border-bottom:1.5px solid var(--ink)}
  .board .sec h2{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;margin:0}
  .board .sec .count{font-size:12.5px;color:var(--muted)}
  .board .brow{display:grid;grid-template-columns:44px 1fr 150px 60px;align-items:center;gap:18px;
               padding:14px 0;border-bottom:1px solid var(--hair)}
  .board .brow .rk{font-variant-numeric:tabular-nums;font-size:18px;color:var(--muted);text-align:center}
  .board .brow.top .rk{color:var(--ink);font-weight:600}
  .board .brow .nm{font-size:17px;font-weight:500}
  .board .brow .sub{font-size:12.5px;color:var(--muted);margin-top:3px}
  .board .brow .minimeter{width:150px;height:6px;background:var(--hair);border-radius:2px;overflow:hidden}
  .board .brow .minimeter .fill{height:100%;border-radius:2px;background:var(--likely)}
  .board .brow .sc{font-weight:600;font-size:18px;min-width:54px;text-align:right;
                   color:var(--likely);font-variant-numeric:tabular-nums}
  /* rail */
  .board .railsec{margin-bottom:32px}
  .board .railhead{font-family:var(--serif);font-weight:700;font-size:21px;margin:0 0 3px;letter-spacing:-.01em}
  .board .railsub{font-size:12.5px;color:var(--muted);margin:0 0 14px}
  .board .stat{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
               padding:11px 0;border-bottom:1px solid var(--hair);font-size:15px}
  .board .stat .v{font-weight:600;font-variant-numeric:tabular-nums}
  .board .live{display:block;background:var(--surface);border:1px solid var(--hair);border-radius:10px;
               padding:16px 18px;text-decoration:none;color:inherit}
  .board .live .t{font-weight:600;font-size:15px;display:flex;align-items:center;gap:8px}
  .board .live p{font-size:13.5px;color:var(--muted);margin:6px 0 0;line-height:1.5}
  .board .live .go{color:var(--likely);font-weight:600}
  .board .explain{background:var(--surface);border:1px solid var(--hair);border-radius:10px;padding:16px 18px;
                  font-size:13.5px;color:var(--muted);line-height:1.6}
  .board .explain b{color:var(--ink)}
  .board .foot{margin-top:42px;color:var(--muted);font-size:13.5px;border-top:1px solid var(--hair);padding-top:18px}
  .board .foot b{color:var(--ink)}
  .board .empty{color:var(--muted);padding:30px 0}
  @media(max-width:640px){.board .brow{grid-template-columns:34px 1fr auto;gap:12px}
    .board .brow .minimeter{display:none}}
  /* dark mode — its own block so the approved feed page is never touched */
  body[data-theme="dark"]{--paper:#141210;--ink:#F2EEE4;--muted:#A99F8B;--hair:#2E2A24;--surface:#1B1814;
                          --denied:#E0675A;--contested:#E0AE5A;--likely:#4FB07E;}
"""


def _spotlight(t, mean):
    """The top reporter as a lede story: serif headline + green score gauge.
    Reuses the feed's gauge VISUAL but with reporter semantics — no deal-probability
    scale, the tick marks the field average a name has to clear."""
    name = html.escape(t["source"])
    score = t["score"]
    early = " &middot; broke deals early" if t.get("earliness_bonus") else ""
    dek = (f"<em>{name}</em> tops the desk &mdash; {t['n_claims']} resolved calls graded against what "
           f"actually happened, {t['raw_accuracy']:.0f}% landing before the score is shrunk toward the field{early}.")
    tick = max(0, min(100, mean)) if isinstance(mean, (int, float)) else 50
    return f"""<section class="lede">
        <p class="kicker">Top of the table</p>
        <h2>{name}</h2>
        <p class="dek">{dek}</p>
        <div class="meter">
          <div class="readout"><span class="pct">{score:.0f}<s>%</s></span>
            <span class="verdict">Most reliable</span></div>
          <div class="col">
            <div class="track"><span class="t" style="left:{tick}%"></span>
              <div class="fill" style="width:{max(3, min(100, score)):.0f}%"></div></div>
            <div class="scale"><span>Reliability score</span><span>Field avg {mean}%</span></div>
          </div>
        </div>
      </section>"""


def _brow(i, r):
    name = html.escape(r["source"])
    score = r["score"]
    bar = max(3, min(100, score))
    early = f" &middot; +{r['earliness_bonus']:.1f} scoop" if r.get("earliness_bonus") else ""
    top = " top" if i == 1 else ""
    return f"""<div class="brow{top}">
        <div class="rk">{i}</div>
        <div><div class="nm">{name}</div>
          <div class="sub">{r['n_claims']} resolved calls &middot; raw {r['raw_accuracy']:.0f}%{early}</div></div>
        <div class="minimeter"><div class="fill" style="width:{bar:.0f}%"></div></div>
        <div class="sc">{score:.0f}%</div>
      </div>"""


def main():
    if not DATA.exists():
        raise SystemExit(f"No data at {DATA}. Run scoring/score.py first.")
    d = json.loads(DATA.read_text(encoding="utf-8"))
    board = d.get("leaderboard", [])
    is_sample = any(r["source"] in SAMPLE_NAMES for r in board)
    n = d.get("n_claims", 0)
    mean = d.get("population_mean_accuracy", "?")

    rows = "".join(_brow(i, r) for i, r in enumerate(board, 1)) or \
        '<div class="empty">No scored reporters yet. Fill journalist_claims.csv and run score.py.</div>'
    spotlight = _spotlight(board[0], mean) if board else (
        '<section class="lede"><p class="kicker">Pre-season</p><h2>No reporters scored yet</h2>'
        '<p class="dek">Once resolved calls are graded the most reliable name leads here, '
        'and the full table fills in below.</p></section>')

    banner = ('<div class="banner">Showing <b>sample data</b> with fictional sources. '
              'Replace journalist_claims.sample.csv with real claims, re-run score.py, then rebuild.</div>'
              ) if is_sample else ""

    today = date.today()
    datestr = f"{today.strftime('%a')} {today.day} {today.strftime('%B %Y')}"
    nameplate = (f'<div class="nameplate"><div class="rule"></div>'
                 f'<h1 class="np-name">Transfer&nbsp;Truth</h1>'
                 f'<p class="np-slogan">The credibility desk for football transfers</p>'
                 f'<div class="rule thin"></div>'
                 f'<div class="np-line"><span>Reliability standings</span>'
                 f'<span class="sep">&middot;</span><span>{datestr}</span>'
                 f'<span class="sep">&middot;</span><span>{len(board)} reporters</span>'
                 f'<span class="sep">&middot;</span><span>{n} resolved calls</span>'
                 f'</div><div class="rule"></div></div>')

    rail = (f'<div class="railsec"><a class="live" href="feed.html">'
            f'<span class="t"><span class="livedot"></span>From the live desk</span>'
            f'<p>See these reporters&rsquo; calls play out in real time &mdash; the contested deals, '
            f'the &ldquo;here we go&rdquo;s, and what&rsquo;s already settled. <span class="go">Open the feed &rarr;</span></p>'
            f'</a></div>'
            f'<div class="railsec"><h3 class="railhead">By the numbers</h3>'
            f'<p class="railsub">This window, across every graded call.</p>'
            f'<div class="stat"><span>Field average</span><span class="v">{mean}%</span></div>'
            f'<div class="stat"><span>Resolved calls</span><span class="v">{n}</span></div>'
            f'<div class="stat"><span>Shrinkage prior K</span><span class="v">{d.get("k","?")}</span></div></div>'
            f'<div class="railsec"><h3 class="railhead">How the score works</h3>'
            f'<p class="railsub">Brier-graded against real outcomes.</p>'
            f'<div class="explain">Every claim is scored against what actually happened: a confident '
            f'&ldquo;here we go&rdquo; on a deal that <b>collapsed</b> is punished hard; a hedged '
            f'&ldquo;in talks&rdquo; on the same deal barely dents you. Scores are shrunk toward the '
            f'field average by sample size (K={d.get("k","?")}) so a few lucky calls can&rsquo;t top the '
            f'table, and breaking a deal genuinely early earns a small bonus.</div></div>')

    toggle = ('<button class="toggle" type="button" onclick="ttTheme()" '
              'aria-label="Toggle dark mode">&#9681;</button>')
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    foot = (f'<p class="foot"><b>Reliability, made visible.</b> Reporters ranked by how often their '
            f'transfer calls come true &mdash; scored against real outcomes, not vibes. The score is the '
            f'only colour on the page. Static file, rebuilt daily. Generated {gen}.</p>')

    page = f"""{theme.head("Transfer Truth — Reliability Leaderboard", PAGE_CSS)}
<body>
  {theme.header("leaderboard", trailing=toggle)}
  <div class="board">
    {nameplate}
    {banner}
    <div class="grid">
      <main>
        {spotlight}
        <div class="sec"><h2>Reliability standings</h2><span class="count">Score</span></div>
        {rows}
      </main>
      <aside class="rail">
        {rail}
      </aside>
    </div>
    {foot}
  </div>
  <script>function ttTheme(){{var b=document.body;b.setAttribute('data-theme',b.getAttribute('data-theme')==='dark'?'':'dark');}}</script>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}  ({len(board)} reporters"
          f"{', SAMPLE data' if is_sample else ''})")


if __name__ == "__main__":
    main()
