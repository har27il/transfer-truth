#!/usr/bin/env python3
"""
Render the live transfer-rumour feed to docs/feed.html.

Reads clustered claims from the ingest store, computes each deal's probability via
ingest.meter (real reliability weights from the leaderboard), and renders a card per
deal: player -> destination, the colour-coded % meter, label, and the reporting
sources. When the store is empty (no live ingestion yet) it shows a DEMO feed built
from real meter math, clearly banner'd, and switches to live data automatically once
ingestion produces claims.

    python site/build_feed.py
"""
import html
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import store, cluster, meter

OUT = ROOT / "docs" / "feed.html"
HEX = {"green": "#22c55e", "yellow": "#eab308", "red": "#ef4444"}

# Illustrative deals for the empty state — rendered through the SAME meter math, so
# the numbers are honest meter output (not hardcoded), just on placeholder claims.
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


def _card(m, reliability):
    c = HEX[m["color"]]
    player = html.escape(m["player"] or "Unknown")
    to_club = html.escape(m["to_club"] or "?")
    from_club = html.escape(m["from_club"] or "")
    src_chips = "".join(
        f'<span class="src">{html.escape(s)}'
        + (f' <b>{round(reliability[s]*100)}%</b>' if s in reliability else "")
        + "</span>"
        for s in m["sources"])
    return f"""
      <div class="card">
        <div class="head">
          <div class="deal"><span class="player">{player}</span>
            <span class="arrow">&rarr;</span> <span class="to">{to_club}</span></div>
          <div class="pct" style="color:{c}">{m['percent']}<span>%</span></div>
        </div>
        <div class="bar"><span style="width:{max(2,m['percent'])}%;background:{c}"></span></div>
        <div class="meta">
          <span class="chip" style="color:{c};border-color:{c}">{m['label']}</span>
          <span>from {from_club}</span>
          <span>&middot; {m['latest_stage']}</span>
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

    banner = ('<div class="banner">Showing <b>demo data</b> — live ingestion is not '
              'running yet (needs an NVIDIA_API_KEY). Numbers are real meter output on '
              'placeholder rumours; the feed switches to live data automatically once '
              'ingestion produces claims.</div>') if is_demo else ""
    cards = "".join(_card(m, reliability) for m in rows) or \
        '<div class="empty">No live rumours yet.</div>'
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")

    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transfer Truth — Live Rumour Feed</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0b0f17; color:#e7ecf3;
         font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
  h1 {{ font-size:30px; margin:0 0 4px; letter-spacing:-.02em; }}
  .sub {{ color:#9aa7b8; margin:0 0 20px; }}
  nav a {{ color:#7aa2f7; text-decoration:none; font-size:14px; margin-right:14px; }}
  .banner {{ background:#3a2a08; border:1px solid #7c5e12; color:#f5d98a;
            padding:10px 14px; border-radius:10px; font-size:14px; margin:16px 0 24px; }}
  .card {{ background:#0f1521; border:1px solid #1b2433; border-radius:12px;
          padding:16px 18px; margin-bottom:14px; }}
  .head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
  .deal {{ font-size:18px; }}
  .player {{ font-weight:700; }}
  .arrow {{ color:#6b7888; margin:0 4px; }}
  .to {{ color:#cdd6e3; }}
  .pct {{ font-weight:800; font-size:30px; line-height:1; }}
  .pct span {{ font-size:14px; opacity:.7; }}
  .bar {{ background:#161e2b; border-radius:6px; height:8px; overflow:hidden; margin:12px 0 10px; }}
  .bar span {{ display:block; height:100%; border-radius:6px; }}
  .meta {{ display:flex; align-items:center; gap:10px; color:#8a97a8; font-size:13px; }}
  .chip {{ border:1px solid; border-radius:999px; padding:1px 9px; font-size:12px; font-weight:650; }}
  .srcs {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:6px; }}
  .src {{ background:#161e2b; border-radius:6px; padding:2px 8px; font-size:12.5px; color:#aeb9c8; }}
  .src b {{ color:#e7ecf3; }}
  .empty {{ color:#7e8b9c; padding:30px 0; }}
  .foot {{ margin-top:30px; color:#8a97a8; font-size:13px; border-top:1px solid #1b2433; padding-top:16px; }}
</style></head>
<body><div class="wrap">
  <h1>Live Rumour Feed</h1>
  <p class="sub">Each deal's probability, weighted by how reliable the reporting journalists are.</p>
  <nav><a href="index.html">&larr; Reliability leaderboard</a></nav>
  {banner}
  <div class="feed">{cards}</div>
  <div class="foot">Probability = reliability-weighted, recency-decayed average of each
    source's claim, plus a corroboration boost for independent sources. Generated {gen}.</div>
</div></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}  ({len(rows)} deals{', DEMO' if is_demo else ''})")


if __name__ == "__main__":
    main()
