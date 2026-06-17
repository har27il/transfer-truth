#!/usr/bin/env python3
"""
Shared editorial design system for the static site. See DESIGN.md (source of truth).

"Journalism of record": Fraunces serif (nameplate + headlines) + Geist (body + data,
tabular figures), warm ink-on-paper monochrome where THE CREDIBILITY METER IS THE ONLY
COLOUR. Both pages pull their head + sticky header from here so the brand never drifts.

Both the feed (build_feed.py) and the leaderboard (build_leaderboard.py) are fully on the
editorial system: shared nameplate + broadsheet grid + standings rail, each scoping its
page-specific CSS under its own container (.feed / .board) so class names never collide.
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?'
         'family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;0,9..144,900;1,9..144,400'
         '&family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">')

# Meter spectrum (gradient start, end). The ONLY colour on the page; semantic.
GREEN = ("#1F7A4D", "#2E9A63")   # likely / verified
AMBER = ("#B5791F", "#CA9135")   # contested
RED = ("#C0392B", "#D6564A")     # denied

BASE_CSS = """
  :root{
    --paper:#FAF6EF; --ink:#16130E; --muted:#5E5746; --hair:#E4DCCB; --surface:#FFFDF9;
    --denied:#C0392B; --contested:#B5791F; --likely:#1F7A4D;
    --serif:'Fraunces',Georgia,'Times New Roman',serif;
    --sans:'Geist',system-ui,-apple-system,'Segoe UI',sans-serif;
    --frame:1180px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--paper);color:var(--ink);
       font:16px/1.55 var(--sans);font-feature-settings:"tnum" 1;
       -webkit-font-smoothing:antialiased;min-height:100vh}
  .h{font-family:var(--serif);letter-spacing:-.02em}
  a{color:inherit}

  /* sticky mini-nav (shared) */
  header{position:sticky;top:0;z-index:5;backdrop-filter:saturate(1.2) blur(8px);
         background:color-mix(in srgb,var(--paper) 88%,transparent);border-bottom:1px solid var(--hair)}
  .bar{max-width:var(--frame);margin:0 auto;padding:11px 24px;display:flex;align-items:center;gap:14px}
  .mini{font-family:var(--serif);font-weight:700;font-size:16px;letter-spacing:-.01em;white-space:nowrap}
  nav{margin-left:auto;display:flex;gap:22px;align-items:center}
  @media(max-width:480px){.bar{gap:10px;padding:10px 16px}nav{gap:14px}}
  nav a{font-size:14px;font-weight:500;color:var(--muted);text-decoration:none;
        padding-bottom:2px;border-bottom:2px solid transparent;white-space:nowrap}
  nav a.on{color:var(--ink);border-color:var(--likely)}
  .livedot{width:7px;height:7px;border-radius:50%;background:var(--likely);display:inline-block;
           margin-right:5px;box-shadow:0 0 0 0 color-mix(in srgb,var(--likely) 50%,transparent);
           animation:pulse 2s infinite}
  @keyframes pulse{70%{box-shadow:0 0 0 6px transparent}100%{box-shadow:0 0 0 0 transparent}}
  .toggle{background:none;border:1px solid var(--hair);color:var(--muted);border-radius:8px;
          min-height:44px;min-width:44px;padding:0 12px;font:inherit;font-size:12.5px;cursor:pointer;
          display:inline-flex;align-items:center;gap:6px}
  .toggle:hover{color:var(--ink);border-color:var(--muted)}

  /* shared content shell + bits the leaderboard still uses */
  .wrap{max-width:880px;margin:0 auto;padding:40px 24px 80px}
  h1{font-size:46px;line-height:1.04;margin:0 0 12px;font-weight:600;font-family:var(--serif);letter-spacing:-.025em}
  h1 em{font-style:italic;color:var(--likely)}
  .lede{color:var(--muted);font-size:18px;max-width:560px;margin:0 0 18px}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin:0}
  .chip{font-size:12.5px;font-weight:600;color:var(--ink);background:var(--surface);
        border:1px solid var(--hair);padding:6px 12px;border-radius:999px}
  .chip.alt{color:var(--muted)}
  .hero{display:grid;grid-template-columns:1fr 280px;gap:30px;align-items:center;margin:0 0 40px}
  .hero-l{min-width:0}
  @media(max-width:720px){.hero{grid-template-columns:1fr;gap:22px}}
  .banner{background:var(--surface);border:1px solid var(--hair);color:var(--ink);padding:11px 14px;
          border-radius:10px;font-size:14px;margin:0 0 24px}
  .secthead{display:flex;align-items:baseline;justify-content:space-between;
            margin:0 0 14px;padding-bottom:8px;border-bottom:1.5px solid var(--ink)}
  .secthead h2{font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:var(--ink);
               margin:0;font-weight:700}
  .track{height:10px;border-radius:99px;background:var(--hair);overflow:hidden}
  .fill{height:100%;border-radius:99px;transform-origin:left;animation:grow 1s cubic-bezier(.2,.8,.2,1) both}
  @keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
  .foot{margin-top:30px;color:var(--muted);font-size:13.5px;border-top:1px solid var(--hair);padding-top:18px}
  .foot b{color:var(--ink)}
  .empty{color:var(--muted);padding:30px 0}
  @media(max-width:640px){h1{font-size:34px}.lede{font-size:16px}}
  @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

# Inline SVG favicon (green rounded square + check) — matches the verified-green accent.
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
           "%3Crect width='32' height='32' rx='8' fill='%231F7A4D'/%3E%3Cpath d='M9 17l5 5 9-11' "
           "stroke='white' stroke-width='3' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")


def head(title, page_css="", description="Football journalists ranked by how often their transfer calls come true."):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title>'
            f'<meta name="description" content="{description}">'
            f'<meta name="theme-color" content="#FAF6EF">'
            f'<meta property="og:title" content="{title}">'
            f'<meta property="og:description" content="{description}">'
            f'<meta property="og:type" content="website">'
            f'<link rel="icon" href="{FAVICON}">'
            f'{FONTS}<style>{BASE_CSS}{page_css}</style></head>')


def header(active, trailing=""):
    """Sticky mini-wordmark + tab nav. active in {'leaderboard','feed'}.
    `trailing` injects extra controls after the nav (the feed passes its dark-mode toggle)."""
    lb = "on" if active == "leaderboard" else ""
    fd = "on" if active == "feed" else ""
    return ('<header><div class="bar">'
            '<span class="mini">Transfer Truth</span>'
            f'<nav><a class="{lb}" href="index.html">Leaderboard</a>'
            f'<a class="{fd}" href="feed.html"><span class="livedot"></span>Live Feed</a>'
            f'{trailing}</nav>'
            '</div></header>')
