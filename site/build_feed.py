#!/usr/bin/env python3
"""
Render the live transfer-rumour feed to docs/feed.html (Pitch design, see site/theme.py).

Reads clustered claims from the ingest store, computes each deal's probability via
ingest.meter (real reliability weights), and renders a card per deal: player ->
destination, the colour-coded % meter, label, and reporting sources. When the store
is empty it shows a DEMO feed built from real meter math (clearly banner'd) and
switches to live data automatically once ingestion produces claims.

    python site/build_feed.py
"""
import html
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "site"))

import theme
from ingest import store, cluster, meter

OUT = ROOT / "docs" / "feed.html"
TIER = {"green": theme.GREEN, "yellow": theme.AMBER, "red": theme.RED}

PAGE_CSS = """
  .card{background:var(--surface);border:1px solid var(--line);border-radius:16px;
        padding:18px 20px;margin-bottom:12px;box-shadow:var(--shadow);
        transition:transform .15s ease,box-shadow .15s ease}
  .card:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(16,24,40,.10)}
  .feature{background:linear-gradient(180deg,#fff,#f3fdf8);border:1px solid #bbf0d6;border-radius:20px;
           padding:22px 24px;margin-bottom:18px;box-shadow:0 12px 34px rgba(18,183,106,.12)}
  .flabel{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
          color:var(--accent);margin-bottom:10px}
  .feature .deal{font-size:24px}
  .feature .pct{font-size:48px}
  .top{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .deal{font-size:19px}
  .deal .player{font-weight:700}
  .deal .arrow{color:#94a3b8;margin:0 6px}
  .deal .to{color:#334155;font-weight:600}
  .pct{font-family:'Space Grotesk';font-weight:700;font-size:34px;line-height:1}
  .pct s{font-size:15px;text-decoration:none;opacity:.55}
  .meta{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:13px;margin-top:12px}
  .label{border:1px solid;border-radius:999px;padding:2px 11px;font-size:12px;font-weight:700}
  .srcs{margin-top:12px;display:flex;flex-wrap:wrap;gap:6px}
  .src{background:#f1f4f8;border:1px solid var(--line);border-radius:8px;padding:3px 9px;
       font-size:12.5px;color:#475467}
  .src b{color:var(--ink)}
  @media(max-width:640px){.deal{font-size:16px}.pct{font-size:28px}}
"""

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


def _card(m, reliability, feature=False):
    c0, c1 = TIER.get(m["color"], theme.AMBER)
    player = html.escape(m["player"] or "Unknown")
    to_club = html.escape(m["to_club"] or "?")
    from_club = html.escape(m["from_club"] or "")
    src_chips = "".join(
        f'<span class="src">{html.escape(s)}'
        + (f' <b>{round(reliability[s]*100)}%</b>' if s in reliability else "")
        + "</span>" for s in m["sources"])
    flabel = '<div class="flabel">&#128293; Hottest right now</div>' if feature else ""
    return f"""
      <div class="{'feature' if feature else 'card'}">
        {flabel}
        <div class="top">
          <div class="deal"><span class="player">{player}</span>
            <span class="arrow">&rarr;</span><span class="to">{to_club}</span></div>
          <div class="pct" style="color:{c0}">{m['percent']}<s>%</s></div>
        </div>
        <div class="track"><div class="fill" style="width:{max(2,m['percent'])}%;background:linear-gradient(90deg,{c0},{c1})"></div></div>
        <div class="meta">
          <span class="label" style="color:{c0};border-color:{c0}">{m['label']}</span>
          <span>from {from_club}</span><span>&middot; {html.escape(m['latest_stage'] or '')}</span>
        </div>
        <div class="srcs">{src_chips}</div>
      </div>"""


def main():
    reliability, _ = meter.load_reliability()
    conn = store.connect()
    rows = meter.meters(conn)
    is_demo = not rows
    if is_demo:
        rows = _demo_meters()

    banner = ('<div class="banner">Showing <b>demo data</b> — live ingestion isn&rsquo;t running '
              'yet (needs an NVIDIA_API_KEY). Numbers are real meter output on placeholder rumours; '
              'the feed switches to live data automatically once ingestion produces claims.</div>'
              ) if is_demo else ""
    feature = _card(rows[0], reliability, feature=True) if rows else ""
    rest = "".join(_card(m, reliability) for m in rows[1:])
    if not rows:
        feature = '<div class="empty">No live rumours yet.</div>'
    more = (f'<div class="secthead"><h2>More deals</h2><h2>Probability</h2></div>{rest}'
            if rest else "")
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")

    page = f"""{theme.head("Transfer Truth — Live Rumour Feed", PAGE_CSS)}
<body>
  {theme.header("feed")}
  <div class="wrap">
    <h1 class="h">Live <em>rumour feed</em></h1>
    <p class="lede">Every deal&rsquo;s probability, weighted by how reliable the reporting
       journalists are — and how recently they said it.</p>
    <div class="chips">
      <span class="chip">{len(rows)} live deals</span>
      <span class="chip alt">Reliability-weighted</span>
      <span class="chip alt">Updated live</span>
    </div>
    {banner}
    {feature}
    {more}
    <p class="foot"><b>How the meter works.</b> Probability is a reliability-weighted,
      recency-decayed average of each source&rsquo;s claim, plus a corroboration boost when
      independent sources agree. A denial from a trusted source drags it down. Generated {gen}.</p>
  </div>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}  ({len(rows)} deals{', DEMO' if is_demo else ''})")


if __name__ == "__main__":
    main()
