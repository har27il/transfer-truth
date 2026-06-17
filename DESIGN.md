# Design System — Transfer Truth

> Source of truth for all visual + UI decisions. Read this before changing
> `site/theme.py`, `site/build_feed.py`, `site/build_leaderboard.py`, or anything
> that renders to `docs/`. Don't deviate without explicit user approval.

## The one idea (never lose this)
**The page is ink-on-paper monochrome; the credibility meter is the only colour.**
Because the whole product is a trust verdict, the colour literally *is* the verdict.
Restraint everywhere else is what makes the meter the thing your eye goes to. If a
new element wants colour, it has to earn it (see "Colour is earned").

## Product Context
- **What this is:** a football transfer-rumour *credibility* site — rates how
  trustworthy each rumour is and ranks journalists by how often their calls come true.
- **Who it's for:** football fans following the transfer window who are sick of
  "99% everywhere" noise and want to know what to actually believe.
- **Space:** sports editorial / data. Peers in feel: The Athletic, FT, Opta.
- **Type:** editorial data product, static pages on GitHub Pages.

## Aesthetic Direction
- **Direction:** Editorial / "journalism of record" (sports-desk authority).
- **Decoration:** intentional — hairline rules, a front-page nameplate, paper warmth.
  Not minimal-austere, not expressive. Rules + type + one earned colour.
- **Mood:** credible, calm, authoritative. Read the FT sports desk, not a fan blog,
  not a SaaS dashboard.
- **Memorable thing:** *trust, made visible.* The meter as a verdict.
- **Approved visual reference:** `~/.gstack/projects/TransferMarket/designs/design-system-20260617/preview.html`

## Typography
- **Display / nameplate / headlines:** **Fraunces** (variable serif, optical sizing).
  Weights 600 for headlines, 900 for the nameplate, italic 400 for the masthead slogan.
  Broadsheet character — this is the brand voice. Never swap for a geometric sans.
- **Body / UI / data:** **Geist**. All numbers use `font-variant-numeric: tabular-nums`
  (`font-feature-settings:"tnum" 1` on body) so probabilities, scores and dates align.
- **Loading:** Google Fonts —
  `Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;0,9..144,900;1,9..144,400`
  + `Geist:wght@400;500;600;700`. Fallbacks: Fraunces → Georgia, serif; Geist → system-ui, sans.
- **Blacklist for this project:** Inter, Space Grotesk (the old SaaS look — do not bring back).
- **Scale (px):** nameplate `clamp(40,7vw,80)` / lede h1 `clamp(42,6.2vw,72)` /
  rail head 21 / section caps 13 / body 16 / data 18 / micro-caps 11–12.
  Headlines: line-height ~1.0, letter-spacing -.025em. Caps labels: letter-spacing .1em.

## Colour
- **Approach:** restrained to one idea. Paper + ink + hairline are the whole page;
  the 3-stop meter spectrum is the only chroma and it is semantic.
- **Light:** paper `#FAF6EF` · ink `#16130E` · muted `#5E5746` · hairline `#E4DCCB` ·
  surface `#FFFDF9`.
- **Meter spectrum (semantic):** denied `#C0392B` · contested `#B5791F` · likely/verified `#1F7A4D`.
  `likely` green also carries the brand thread (wordmark dot, active nav, standings) —
  green = verified/true throughout.
- **Dark mode (re-skin, ~15% less saturation):** paper `#141210` · ink `#F2EEE4` ·
  muted `#A99F8B` · hairline `#2E2A24` · surface `#1B1814` ·
  denied `#E0675A` · contested `#E0AE5A` · likely `#4FB07E`. Toggle via `[data-theme="dark"]`.
- **Colour is earned (rule):** colour is reserved for deals that warrant attention —
  **agreed (green) / contested (amber) / denied (red)**. The cold long tail
  (low-probability "interest / rumour link / talks", no conflict) renders in **muted gray**.
  Red means a *denial actually happened*, never just "low number." Don't paint the cold pile red.
- **Contrast:** target WCAG AA. Small coloured text fails on paper — keep small labels
  (kicker, verdict, micro-caps) in **ink**, let the hue live in the big number + the bar.
  The big contested number (amber) only passes at large size; if ever in doubt, render
  the number in ink and carry the hue in the bar.

## Layout
- **Approach:** hybrid — editorial *story* treatment up top (the most-contested deal as
  a lede: kicker + serif headline + dek + meter), disciplined data *tables* below.
- **Front-page nameplate:** opens the page — top rule, big Fraunces wordmark, serif-italic
  slogan, thin rule, caps dateline (`● Live edition · <date> · <window>`), bottom rule.
  The sticky header is a *separate* compact mini-wordmark + nav (shows when scrolled).
- **Broadsheet grid:** lead column + a `340px` standings rail divided by a hairline rule.
  Rail = Credibility standings (top reporters) + Done & dusted + meter explainer. The rail
  ties the Leaderboard into the Feed and fills the sheet with meaning, not stretch.
- **Max content width:** `1180px`. **Base unit:** 4px, comfortable density (generous around
  the lede, tighter in the rows).
- **Border radius:** meters/bars 2px · chips/cards 6–10px · nothing bubbly. Hairline rules
  (1px hair, 1.5–2px ink for section heads) are the primary divider, not shadows.
- **Dividers over shadows:** flat surfaces + rules. Avoid heavy drop-shadows / cards-everywhere.

## Responsive / Mobile (REQUIRED — design every viewport, never just "stacked")
Mobile is a first-class target, not an afterthought. Rules:
- **Breakpoint `≤900px`:** the broadsheet grid collapses to a **single column**; the
  standings rail moves **below** the deals, its left hairline becomes a top 2px ink rule.
- **Fluid headline sizing:** nameplate `clamp(40,7vw,80)` and lede `clamp(42,6.2vw,72)`
  already scale down — verify the lede never overflows on a 360px screen.
- **Touch targets ≥ 44px:** nav links, the theme toggle, and any future interactive row
  must meet 44px min on touch. Don't rely on hover for anything discoverable (no hover on mobile).
- **Tabular numbers stay right-aligned** in the data rows at every width; the `%` column
  must not wrap under the deal name.
- **Cold-tail density:** on small screens the "More deals" rows stay compact; consider
  hiding the mini-meter bar under ~420px and keeping just the number (the bar is secondary).
- **Spec strip / 3-col type block** collapses to 1 column `≤760px`.
- Test at 360px, 414px, 768px, 1180px before shipping any page.

## Motion
- **Approach:** minimal-functional. A credibility product doesn't bounce.
- **Keep:** the meter `fill` grow on load (`scaleX 0→1`, ~1s, `cubic-bezier(.2,.8,.2,1)`) —
  it helps you read the value. The live dot pulse (subtle, 2s).
- **Easing/duration:** enter ease-out, ~150–250ms for UI transitions; theme swap ~300ms.
- **Always** honor `@media (prefers-reduced-motion: reduce)` → kill animations/transitions.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-17 | Editorial "journalism of record" direction, Fraunces + Geist, meter-only colour | /design-consultation + /plan-design-review. Replaces the old "Pitch" SaaS-green/Inter/Space-Grotesk look; an editorial language fits a *credibility* product and differentiates from every other transfer site. |
| 2026-06-17 | Broadsheet two-column grid + standings rail (1180px) | Fixed dead side-whitespace by filling the sheet with meaning (leaderboard next to live deals), not by stretching a single column. |
| 2026-06-17 | "Colour is earned" — cold tail goes gray, red reserved for denials | A quiet 15% interest link is not a rejected deal; sharing alarm-red with denials was a false equivalence. Makes colour meaningful and the page calmer. |
| 2026-06-17 | Mobile-friendly is a hard requirement | User instruction. Every viewport gets intentional design; see Responsive section. |
