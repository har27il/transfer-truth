#!/usr/bin/env python3
"""
Shared "Pitch" design system for the static site (chosen via /plan-design-review).

Clean / professional / energetic, 2026: warm off-white, pitch-green accent,
Space Grotesk display + Inter body, sticky wordmark header with pill tab nav,
score-as-hero with animated gradient meters. Both pages (leaderboard, feed) pull
their head + header from here so the brand never drifts between them.
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700'
         '&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">')

# Tier colours (gradient start, end) shared by leaderboard scores and feed meters.
GREEN = ("#12b76a", "#34d399")
AMBER = ("#f59e0b", "#fbbf24")
RED = ("#ef4444", "#f87171")

BASE_CSS = """
  :root{ --bg:#f5f7fa; --surface:#fff; --ink:#0b1220; --muted:#64748b; --line:#e7ebf0;
         --accent:#12b76a; --accent2:#34d399; --gold:#f59e0b;
         --shadow:0 1px 2px rgba(16,24,40,.04),0 8px 24px rgba(16,24,40,.06); }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 500px at 50% -10%,#e9fbf2 0%,var(--bg) 55%);
       color:var(--ink);font:16px/1.55 Inter,system-ui,-apple-system,Segoe UI,sans-serif;
       -webkit-font-smoothing:antialiased;min-height:100vh}
  .h{font-family:'Space Grotesk',Inter,sans-serif;letter-spacing:-.02em}
  a{color:inherit}
  header{position:sticky;top:0;z-index:5;backdrop-filter:saturate(1.4) blur(10px);
         background:rgba(245,247,250,.78);border-bottom:1px solid var(--line)}
  .bar{max-width:880px;margin:0 auto;padding:14px 20px;display:flex;align-items:center;gap:14px}
  .mark{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--accent2));
        display:grid;place-items:center;color:#04130c;font-weight:800;box-shadow:0 6px 16px rgba(18,183,106,.35)}
  .brand{font-weight:700;font-size:18px}.brand span{color:var(--accent)}
  nav{margin-left:auto;display:flex;gap:6px;background:#eef1f5;padding:4px;border-radius:999px}
  nav a{padding:7px 16px;border-radius:999px;font-size:14px;font-weight:600;color:var(--muted);text-decoration:none}
  nav a.on{background:#fff;color:var(--ink);box-shadow:var(--shadow)}
  .wrap{max-width:880px;margin:0 auto;padding:44px 20px 80px}
  h1{font-size:46px;line-height:1.04;margin:0 0 12px;font-weight:700}
  h1 em{font-style:normal;background:linear-gradient(90deg,#0fae63,#34d399);
        -webkit-background-clip:text;background-clip:text;color:transparent}
  .lede{color:var(--muted);font-size:18px;max-width:540px;margin:0 0 18px}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin:0}
  .chip{font-size:13px;font-weight:600;color:#0f766e;background:#d8f6e8;border:1px solid #b6ecd3;
        padding:6px 12px;border-radius:999px}
  .chip.alt{color:#475467;background:#fff;border-color:var(--line)}
  /* hero: text column + a strong visual anchor on the right */
  .hero{display:grid;grid-template-columns:1fr 280px;gap:30px;align-items:center;margin:0 0 40px}
  .hero-l{min-width:0}
  .livedot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block;
           margin-right:6px;box-shadow:0 0 0 0 rgba(18,183,106,.5);animation:pulse 1.8s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(18,183,106,.5)}70%{box-shadow:0 0 0 7px rgba(18,183,106,0)}
                   100%{box-shadow:0 0 0 0 rgba(18,183,106,0)}}
  @media(max-width:720px){.hero{grid-template-columns:1fr;gap:22px}}
  .banner{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;padding:11px 14px;
          border-radius:12px;font-size:14px;margin:0 0 24px}
  .secthead{display:flex;align-items:baseline;justify-content:space-between;margin:0 0 14px}
  .secthead h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
               margin:0;font-weight:700}
  .track{height:10px;border-radius:99px;background:#eef1f5;overflow:hidden}
  .fill{height:100%;border-radius:99px;transform-origin:left;
        animation:grow 1s cubic-bezier(.2,.8,.2,1) both}
  @keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
  .foot{margin-top:30px;color:var(--muted);font-size:13.5px;border-top:1px solid var(--line);padding-top:18px}
  .foot b{color:var(--ink)}
  .empty{color:var(--muted);padding:30px 0}
  @media(max-width:640px){h1{font-size:34px}.lede{font-size:16px}}
  @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


# Inline SVG favicon (green rounded square + check) — matches the wordmark mark.
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
           "%3Crect width='32' height='32' rx='8' fill='%2312b76a'/%3E%3Cpath d='M9 17l5 5 9-11' "
           "stroke='white' stroke-width='3' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")


def head(title, page_css="", description="Football journalists ranked by how often their transfer calls come true."):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title>'
            f'<meta name="description" content="{description}">'
            f'<meta name="theme-color" content="#12b76a">'
            f'<meta property="og:title" content="{title}">'
            f'<meta property="og:description" content="{description}">'
            f'<meta property="og:type" content="website">'
            f'<link rel="icon" href="{FAVICON}">'
            f'{FONTS}<style>{BASE_CSS}{page_css}</style></head>')


def header(active):
    """Sticky wordmark + pill tab nav. active in {'leaderboard','feed'}."""
    lb = "on" if active == "leaderboard" else ""
    fd = "on" if active == "feed" else ""
    return ('<header><div class="bar">'
            '<div class="mark h">&#10003;</div>'
            '<div class="brand h">Transfer<span>Truth</span></div>'
            f'<nav><a class="{lb}" href="index.html">Leaderboard</a>'
            f'<a class="{fd}" href="feed.html"><span class="livedot"></span>Live Feed</a></nav>'
            '</div></header>')
