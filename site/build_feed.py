#!/usr/bin/env python3
"""
Render the live transfer-rumour feed to docs/feed.html (editorial system, see DESIGN.md).

Reads clustered claims from the ingest store, computes each deal's probability via
ingest.meter, and renders a broadsheet front page: a front-page nameplate, the most
CONTESTED deal as a lede story, then "Agreed (here we go)", a cold "More deals" tail,
and a standings rail (credibility leaderboard + settled deals + explainer). The
credibility meter is the only colour on the page; colour is earned (cold = gray, red
only for denials). When the store is empty it falls back to a DEMO feed (real meter
math on placeholder rumours) and switches to live data once ingestion produces claims.

    python site/build_feed.py
"""
import csv
import html
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "site"))

import theme
from ingest import store, cluster, meter

OUT = ROOT / "docs" / "feed.html"
DEALS = ROOT / "ground-truth" / "deals.csv"
STANDINGS = ROOT / "scoring" / "leaderboard.json"
# The window the live feed covers. Must match ingest.pipeline.DEFAULT_WINDOW so the
# resolved-deal join lines up (both sides key on cluster.deal_key(player, window)).
ACTIVE_WINDOW = "2026-summer"
# Only show deals whose newest claim is within this many days (persisted ingest.db keeps
# accreting claims; this keeps the live feed current). ~1.5x the meter's 14-day half-life.
DISPLAY_MAX_AGE_DAYS = 21
_RESOLVED = ("completed", "collapsed")

# Feed-specific CSS, scoped under .feed so nothing collides with the leaderboard page
# (which still defines its own .row/.lede). Lifted from the approved design preview.
PAGE_CSS = """
  .feed{max-width:var(--frame);margin:0 auto;padding:0 32px}
  /* nameplate */
  .feed .nameplate{text-align:center;padding:30px 0 0}
  .feed .rule{height:0;border-top:1px solid var(--ink)}
  .feed .rule.thin{border-top:1px solid var(--hair)}
  .feed .np-name{font-family:var(--serif);font-optical-sizing:auto;font-weight:900;
                 font-size:clamp(40px,7vw,80px);line-height:.96;letter-spacing:-.03em;margin:14px 0 6px}
  .feed .np-slogan{font-family:var(--serif);font-style:italic;font-weight:400;font-size:17px;
                   color:var(--muted);margin:0 0 14px}
  .feed .np-line{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:10px;
                 font-size:11.5px;text-transform:uppercase;letter-spacing:.18em;color:var(--muted);padding:9px 0}
  .feed .np-line .sep{opacity:.4}
  /* broadsheet grid */
  .feed .grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:0;margin-top:8px}
  .feed .rail{border-left:1px solid var(--hair);margin-left:40px;padding-left:40px}
  @media(max-width:900px){.feed .grid{grid-template-columns:1fr}
    .feed .rail{border-left:0;margin-left:0;padding-left:0;margin-top:24px;border-top:2px solid var(--ink);padding-top:8px}}
  /* lede story */
  .feed .lede{padding:34px 0 32px;border-bottom:1px solid var(--hair)}
  .feed .kicker{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:var(--ink);
                margin:0 0 16px;display:flex;align-items:center;gap:10px}
  .feed .kicker::before{content:"";width:24px;height:2px;background:var(--contested)}
  .feed .lede h2{font-family:var(--serif);font-optical-sizing:auto;font-weight:600;
                 font-size:clamp(36px,6vw,68px);line-height:1.0;letter-spacing:-.025em;margin:0 0 16px}
  .feed .lede h2 .arr{color:var(--muted);font-weight:400}
  .feed .dek{font-size:18px;color:var(--muted);max-width:54ch;margin:0 0 26px;line-height:1.5}
  .feed .dek em{font-style:italic;color:var(--ink)}
  /* the meter: credibility gauge */
  .feed .meter{display:flex;align-items:center;gap:24px;max-width:600px;flex-wrap:wrap}
  .feed .readout{flex:none}
  .feed .readout .pct{display:block;font-weight:600;font-size:50px;letter-spacing:-.02em;line-height:.92;font-variant-numeric:tabular-nums}
  .feed .readout .pct s{font-size:19px;text-decoration:none;color:var(--muted);font-weight:500}
  .feed .readout .verdict{display:block;margin-top:6px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--ink)}
  .feed .meter .col{flex:1;min-width:180px}
  .feed .meter .track{position:relative;height:9px;background:var(--hair);border-radius:2px;overflow:hidden}
  .feed .meter .track .t{position:absolute;top:0;bottom:0;width:1px;background:color-mix(in srgb,var(--paper) 55%,var(--hair))}
  .feed .meter .fill{height:100%;border-radius:2px;position:relative;z-index:1}
  .feed .scale{display:flex;justify-content:space-between;margin-top:8px;font-size:11px;
               text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
  .feed .sources{margin-top:22px;display:flex;flex-wrap:wrap;gap:8px 20px;font-size:13.5px;color:var(--muted)}
  .feed .sources b{color:var(--ink);font-weight:600}
  /* section heads + data rows */
  .feed .sec{display:flex;align-items:baseline;justify-content:space-between;margin:32px 0 2px;
             padding-bottom:8px;border-bottom:1.5px solid var(--ink)}
  .feed .sec h2{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;margin:0}
  .feed .sec .count{font-size:12.5px;color:var(--muted)}
  .feed .row{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:18px;
             padding:14px 0;border-bottom:1px solid var(--hair)}
  .feed .row .deal{font-size:17px;font-weight:500}
  .feed .row .deal .arr{color:var(--muted);margin:0 7px}
  .feed .row .deal .stage{display:block;font-size:12px;color:var(--muted);margin-top:3px;text-transform:uppercase;letter-spacing:.05em}
  .feed .row .minimeter{width:130px;height:6px;background:var(--hair);border-radius:2px;overflow:hidden}
  .feed .row .minimeter .fill{height:100%;border-radius:2px}
  .feed .row .num{font-weight:600;font-size:18px;min-width:54px;text-align:right;font-variant-numeric:tabular-nums}
  .feed .row.cold .deal,.feed .row.cold .num{color:var(--muted)}
  /* rail */
  .feed .railsec{margin-bottom:32px}
  .feed .railhead{font-family:var(--serif);font-weight:700;font-size:21px;margin:0 0 3px;letter-spacing:-.01em}
  .feed .railsub{font-size:12.5px;color:var(--muted);margin:0 0 14px}
  .feed .stand{display:grid;grid-template-columns:18px 1fr auto;gap:12px;align-items:baseline;
               padding:11px 0;border-bottom:1px solid var(--hair);font-size:15px}
  .feed .stand .rk{color:var(--muted);font-variant-numeric:tabular-nums;font-size:13px}
  .feed .stand .who{font-weight:500}
  .feed .stand .who s{display:block;text-decoration:none;color:var(--muted);font-size:12px;margin-top:1px}
  .feed .stand .sc{font-weight:600;font-variant-numeric:tabular-nums;color:var(--likely)}
  .feed .doneitem{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
                  padding:10px 0;border-bottom:1px solid var(--hair);font-size:14.5px}
  .feed .tick{color:var(--likely);font-weight:600}
  .feed .stayed{color:var(--denied);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.05em}
  .feed .explain{background:var(--surface);border:1px solid var(--hair);border-radius:10px;padding:16px 18px;
                 font-size:13.5px;color:var(--muted);line-height:1.6}
  .feed .explain b{color:var(--ink)}
  .feed .foot{margin-top:42px}
  @media(max-width:420px){.feed .row .minimeter{display:none}}
  /* dark mode — feed page only (leaderboard keeps its light styling until its redesign) */
  body[data-theme="dark"]{--paper:#141210;--ink:#F2EEE4;--muted:#A99F8B;--hair:#2E2A24;--surface:#1B1814;
                          --denied:#E0675A;--contested:#E0AE5A;--likely:#4FB07E;}
"""

EXPLAINER = ('<div class="explain"><b>How the meter works.</b> Each rumour&rsquo;s probability is a '
             'reliability-weighted, recency-decayed average of every source&rsquo;s claim, plus a '
             'corroboration boost when independent outlets agree. A denial from a trusted name drags it down.</div>')

# Illustrative deals for the empty state — rendered through the SAME meter math.
_DEMO = [
    ("Alexander Isak", "Liverpool", "Newcastle United",
     [(0.99, "Fabrizio Romano", "2025-09-01", "here_we_go"),
      (0.99, "Sky Sports", "2025-09-01", "official"),
      (0.80, "David Ornstein", "2025-08-30", "agreement")]),
    ("Marc Guehi", "Liverpool", "Crystal Palace",
     [(0.99, "Fabrizio Romano", "2025-09-01", "here_we_go"),
      (0.02, "David Ornstein", "2025-09-01", "denied"),
      (0.35, "BBC Sport", "2025-08-30", "talks")]),
    ("Nick Woltemade", "Newcastle United", "VfB Stuttgart",
     [(0.80, "Sky Sports", "2025-08-29", "agreement"),
      (0.60, "Florian Plettenberg", "2025-08-28", "advanced")]),
    ("Marcus Rashford", "Barcelona", "Manchester United",
     [(0.15, "Goal", "2025-08-20", "rumour_link"),
      (0.15, "Football Espana", "2025-08-22", "interest")]),
]


def _demo_meters():
    conn = store.connect(":memory:")
    for player, to, frm, claims in _DEMO:
        key = cluster.deal_key(player, "2025-summer")
        for i, (p, src, d, stage) in enumerate(claims):
            url = f"demo://{player}/{i}"
            store.add_post(conn, {"url": url, "source": src, "title": "", "summary": ""})
            store.add_claim(conn, {"post_url": url, "deal_key": key, "player": player,
                                   "from_club": frm, "to_club": to, "stage": stage, "implied_p": p,
                                   "source_name": src, "source_identifiable": 1,
                                   "direction_confidence": 0.9, "fee_eur": None, "claim_date": d})
    return meter.meters(conn, today=date(2025, 9, 1))


def load_resolved(path=DEALS, window=ACTIVE_WINDOW):
    """Read settled deals for the active window from the outcome ledger (deals.csv).

    Returns (resolved_keys, done_rows): keys to PULL OUT of the live feed (a deal that
    already happened/collapsed stops showing as a live rumour), and the settled rows
    themselves (newest first) for the rail. deals.csv is authoritative and survives a
    rumour aging out of the RSS, so a confirmed deal stays on the page."""
    resolved_keys, done = set(), []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("window") or "").strip() != window:
                    continue
                if (r.get("outcome") or "").strip().lower() not in _RESOLVED:
                    continue
                resolved_keys.add(cluster.deal_key(r["player"], window))
                done.append(r)
    except FileNotFoundError:
        return set(), []
    done.sort(key=lambda r: (r.get("outcome_date") or ""), reverse=True)
    return resolved_keys, done


def load_standings(path=STANDINGS, top=5):
    """Top reporters from the scoring leaderboard, for the feed's standings rail.
    Independent of ingest.db, so it shows even when the live feed is in demo mode."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return d.get("leaderboard", [])[:top]


def _hue(m, tier):
    """Bar + number colour. Colour is EARNED (see DESIGN.md): agreed -> green,
    contested -> amber, a real denial -> red, every quiet/cold rumour -> muted gray."""
    if tier == "agreed":
        return "var(--likely)"
    c = m.get("color")
    if c == "green":
        return "var(--likely)"
    if c == "yellow":
        return "var(--contested)"
    return "var(--denied)" if m.get("label") == "Denied" else "var(--muted)"


def _dek(m):
    """A one-line editorial dek composed from the deal's own signals."""
    n = m.get("n_sources", 0)
    label = m.get("label")
    if label == "Contested":
        return f"Sources are split &mdash; {n} outlets reporting, no consensus yet."
    if label == "Denied":
        return "A trusted source has knocked this back. The meter reflects the doubt."
    if label == "Likely":
        return f"{n} sources lean yes; the meter rises as reliable names corroborate."
    return f"Early days &mdash; {n} source(s) so far, nothing settled."


def _lead(m, reliability):
    """The most-contested live deal, rendered as the lede story with a gauge meter."""
    hue = _hue(m, "live")
    player = html.escape(m.get("player") or "Unknown")
    to_club = html.escape(m.get("to_club") or "?")
    pct = m["percent"]
    verdict = html.escape(m.get("label") or "")
    kicker = "Today&rsquo;s most contested" if m.get("spread", 0.0) > 0.4 else "Leading the feed"
    chips = "".join(
        f'<span><b>{html.escape(s)}</b>'
        + (f' {round(reliability[s]*100)}%' if s in reliability else "")
        + "</span>" for s in m.get("sources", []))
    return f"""<section class="lede">
        <p class="kicker">{kicker}</p>
        <h2><span>{player}</span> <span class="arr">&rarr;</span> {to_club}</h2>
        <p class="dek">{_dek(m)}</p>
        <div class="meter">
          <div class="readout"><span class="pct" style="color:{hue}">{pct}<s>%</s></span>
            <span class="verdict">{verdict}</span></div>
          <div class="col">
            <div class="track"><span class="t" style="left:25%"></span><span class="t" style="left:50%"></span><span class="t" style="left:75%"></span>
              <div class="fill" style="width:{max(3, pct)}%;background:{hue}"></div></div>
            <div class="scale"><span>Denied</span><span>Coin-flip</span><span>Done</span></div>
          </div>
        </div>
        <div class="sources">{chips}</div>
      </section>"""


def _empty_lead():
    return ('<section class="lede"><p class="kicker">All quiet</p>'
            '<h2>No contested deals right now</h2>'
            '<p class="dek">The window has gone quiet. Agreed and settled deals are in the rail; '
            'fresh rumours surface here as reliable reporters file them.</p></section>')


def _row(m, tier):
    hue = _hue(m, tier)
    cold = (hue == "var(--muted)")
    player = html.escape(m.get("player") or "Unknown")
    to_club = html.escape(m.get("to_club") or "?")
    stage = html.escape((m.get("latest_stage") or "").replace("_", " "))
    pct = m["percent"]
    return (f'<div class="row{" cold" if cold else ""}">'
            f'<div class="deal">{player} <span class="arr">&rarr;</span> {to_club}'
            f'<span class="stage">{stage}</span></div>'
            f'<div class="minimeter"><div class="fill" style="width:{max(3, pct)}%;background:{hue}"></div></div>'
            f'<div class="num" style="color:{hue}">{pct}%</div></div>')


def _section(title, count, rows, tier):
    cards = "".join(_row(m, tier) for m in rows)
    return f'<div class="sec"><h2>{title}</h2><span class="count">{count}</span></div>{cards}'


def _standings_html(rows):
    if not rows:
        return ""
    items = "".join(
        f'<div class="stand"><span class="rk">{i}</span>'
        f'<span class="who">{html.escape(r["source"])}<s>{r.get("n_claims", 0)} calls</s></span>'
        f'<span class="sc">{round(r["score"])}%</span></div>'
        for i, r in enumerate(rows, 1))
    return ('<div class="railsec"><h3 class="railhead">Credibility standings</h3>'
            '<p class="railsub">Top reporters this window, by how often their calls come true.</p>'
            f'{items}</div>')


def _done_rail(done_rows):
    if not done_rows:
        return ""
    items = ""
    for r in done_rows[:6]:
        player = html.escape(r.get("player") or "Unknown")
        if (r.get("outcome") or "").strip().lower() == "completed":
            sub = f'<span style="color:var(--muted)">&rarr; {html.escape(r.get("to_club") or "")}</span>'
            badge = '<span class="tick">Done &check;</span>'
        else:
            sub = '<span style="color:var(--muted)">stayed put</span>'
            badge = '<span class="stayed">Move off</span>'
        items += f'<div class="doneitem"><span>{player} {sub}</span>{badge}</div>'
    return ('<div class="railsec"><h3 class="railhead">Done &amp; dusted</h3>'
            f'<p class="railsub">Settled this window.</p>{items}</div>')


def main():
    reliability, _ = meter.load_reliability()
    conn = store.connect()
    rows = meter.meters(conn, max_age_days=DISPLAY_MAX_AGE_DAYS)
    is_demo = not rows
    if is_demo:
        rows = _demo_meters()

    # Pull settled deals out of the live feed (they live in the rail's Done section instead).
    resolved_keys, done_rows = (set(), []) if is_demo else load_resolved()
    if resolved_keys:
        rows = [m for m in rows if m.get("deal_key") not in resolved_keys]

    # Split near-certainties (Agreed) from everything still in play (live), then pick the
    # hero from LIVE only so a 99%-everyone-agrees deal can't win "most contested".
    agreed_rows, live_rows = [], []
    for m in rows:
        (agreed_rows if meter.classify_tier(m) == "agreed" else live_rows).append(m)
    hero = max(live_rows, key=lambda m: (m.get("spread", 0.0), m.get("uncertainty", 0.0))) if live_rows else None
    more_rows = [m for m in live_rows if m is not hero]

    lead_html = _lead(hero, reliability) if hero else _empty_lead()
    agreed_html = _section("Agreed &mdash; here we go", f"{len(agreed_rows)} pending official",
                           agreed_rows, "agreed") if agreed_rows else ""
    more_html = _section("More deals", "Probability", more_rows, "live") if more_rows else ""
    standings_html = _standings_html(load_standings())
    done_html = _done_rail(done_rows)

    today = date.today()
    datestr = f"{today.strftime('%a')} {today.day} {today.strftime('%B %Y')}"
    nameplate = (f'<div class="nameplate"><div class="rule"></div>'
                 f'<h1 class="np-name">Transfer&nbsp;Truth</h1>'
                 f'<p class="np-slogan">The credibility desk for football transfers</p>'
                 f'<div class="rule thin"></div>'
                 f'<div class="np-line"><span><span class="livedot"></span>Live edition</span>'
                 f'<span class="sep">&middot;</span><span>{datestr}</span>'
                 f'<span class="sep">&middot;</span><span>Summer window</span>'
                 f'<span class="sep">&middot;</span><span>{len(live_rows)} contested &middot; {len(agreed_rows)} agreed</span>'
                 f'</div><div class="rule"></div></div>')

    banner = ('<div class="banner">Showing <b>demo data</b> &mdash; live ingestion isn&rsquo;t running yet '
              '(needs an NVIDIA_API_KEY). Numbers are real meter output on placeholder rumours; the feed '
              'switches to live data automatically once ingestion produces claims.</div>') if is_demo else ""

    toggle = ('<button class="toggle" type="button" onclick="ttTheme()" '
              'aria-label="Toggle dark mode">&#9681;</button>')
    # UTC-explicit: stamp the zone so the footer never reads an hour "off" against a
    # local clock (the build runs on a UTC runner; this also stays correct for any
    # local rebuild in a non-UTC timezone).
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    foot = (f'<p class="foot">Reliability-weighted, recency-decayed. The meter is the only colour on the page. '
            f'Static file, rebuilt daily. Generated {gen} UTC.</p>')

    page = f"""{theme.head("Transfer Truth — Live Rumour Feed", PAGE_CSS)}
<body>
  {theme.header("feed", trailing=toggle)}
  <div class="feed">
    {nameplate}
    {banner}
    <div class="grid">
      <main>
        {lead_html}
        {agreed_html}
        {more_html}
      </main>
      <aside class="rail">
        {standings_html}
        {done_html}
        {EXPLAINER}
      </aside>
    </div>
    {foot}
  </div>
  <script>function ttTheme(){{var b=document.body;b.setAttribute('data-theme',b.getAttribute('data-theme')==='dark'?'':'dark');}}</script>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}  ({len(live_rows)} live + {len(agreed_rows)} agreed"
          f"{', DEMO' if is_demo else ''})")


if __name__ == "__main__":
    main()
