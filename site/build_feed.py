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
import csv
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
DEALS = ROOT / "ground-truth" / "deals.csv"
# The window the live feed covers. Must match ingest.pipeline.DEFAULT_WINDOW so the
# resolved-deal join lines up (both sides key on cluster.deal_key(player, window)).
ACTIVE_WINDOW = "2026-summer"
# Only show deals whose newest claim is within this many days. With ingest.db now
# persisted across runs, this keeps the live feed current instead of accreting
# weeks-old dead rumours (~1.5x the meter's 14-day half-life).
DISPLAY_MAX_AGE_DAYS = 21
TIER = {"green": theme.GREEN, "yellow": theme.AMBER, "red": theme.RED}
_RESOLVED = ("completed", "collapsed")

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
  /* Done / confirmed deals: muted, factual, clearly past-tense — the track record. */
  .done{background:#fbfcfd;border:1px solid var(--line);border-radius:12px;
        padding:12px 16px;margin-bottom:8px;display:flex;align-items:center;
        justify-content:space-between;gap:12px}
  .done .deal{font-size:16px}
  .done .player{font-weight:700}
  .done .to{color:#475467;font-weight:600}
  .done .right{display:flex;align-items:center;gap:10px;white-space:nowrap}
  .done .when{color:var(--muted);font-size:12.5px}
  .outcome{border:1px solid;border-radius:999px;padding:2px 11px;font-size:12px;font-weight:700}
  @media(max-width:640px){.deal{font-size:16px}.pct{font-size:28px}
    .done{flex-direction:column;align-items:flex-start;gap:6px}}
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
    flabel = '<div class="flabel">&#128293; Most contested right now</div>' if feature else ""
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


def load_resolved(path=DEALS, window=ACTIVE_WINDOW):
    """Read settled deals for the active window from the outcome ledger (deals.csv).

    Returns (resolved_keys, done_rows):
      resolved_keys -- accent-folded deal keys to PULL OUT of the live feed, so a deal
                       that already happened/collapsed stops showing as a live rumour.
      done_rows     -- the settled deals themselves, newest first, for the Done section.
    deals.csv is authoritative and survives a rumour aging out of the RSS, so a confirmed
    deal stays on the page even once it's gone from the live ingest store."""
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


def _done_card(r):
    """One settled deal, rendered factually past-tense (no live meter)."""
    player = html.escape(r.get("player") or "Unknown")
    completed = (r.get("outcome") or "").strip().lower() == "completed"
    to_club = html.escape(r.get("to_club") or "")
    if completed:
        line = (f'<span class="player">{player}</span><span class="arrow">&rarr;</span>'
                f'<span class="to">{to_club}</span>')
        badge = f'<span class="outcome" style="color:{theme.GREEN[0]};border-color:{theme.GREEN[0]}">Done &check;</span>'
    else:  # collapsed
        line = f'<span class="player">{player}</span> <span class="arrow">stayed put</span>'
        badge = f'<span class="outcome" style="color:{theme.RED[0]};border-color:{theme.RED[0]}">Move off</span>'
    when = html.escape((r.get("outcome_date") or "")[:10])
    return (f'<div class="done"><div class="deal">{line}</div>'
            f'<div class="right">{badge}<span class="when">{when}</span></div></div>')


def main():
    reliability, _ = meter.load_reliability()
    conn = store.connect()
    rows = meter.meters(conn, max_age_days=DISPLAY_MAX_AGE_DAYS)
    is_demo = not rows
    if is_demo:
        rows = _demo_meters()

    # Split the live feed from the track record: any deal already settled in the outcome
    # ledger leaves the live meter and moves to the Done section. (Demo state has no real
    # ledger to join against, so it stays a pure illustrative feed.)
    resolved_keys, done_rows = (set(), []) if is_demo else load_resolved()
    if resolved_keys:
        rows = [m for m in rows if m.get("deal_key") not in resolved_keys]

    banner = ('<div class="banner">Showing <b>demo data</b> — live ingestion isn&rsquo;t running '
              'yet (needs an NVIDIA_API_KEY). Numbers are real meter output on placeholder rumours; '
              'the feed switches to live data automatically once ingestion produces claims.</div>'
              ) if is_demo else ""

    # Hero = the most CONTESTED open deal (sources disagree most, verdict nearest a
    # coin-flip), not the most certain one — the near-certainties everyone agrees on are
    # the least interesting. `rows` stays probability-sorted (from meter.meters) for the
    # list below, so we get "most debated up top, closest-to-done underneath".
    hero = max(rows, key=lambda m: (m.get("spread", 0.0), m.get("uncertainty", 0.0))) if rows else None
    feature = _card(hero, reliability, feature=True) if hero else '<div class="empty">No live rumours yet.</div>'
    rest = "".join(_card(m, reliability) for m in rows if m is not hero)
    more = (f'<div class="secthead"><h2>More deals</h2><h2>Probability</h2></div>{rest}'
            if rest else "")

    done_html = ""
    if done_rows:
        cards = "".join(_done_card(r) for r in done_rows)
        done_html = (f'<div class="secthead"><h2>Done &amp; dusted</h2>'
                     f'<h2>{len(done_rows)} settled</h2></div>{cards}')
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
    {done_html}
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
