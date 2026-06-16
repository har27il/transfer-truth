# Ground-Truth Dataset — Phase 1

This folder is the foundation of the whole project. If these outcomes are wrong,
every journalist score built on them is wrong. Fill it carefully and verify
every outcome against a real source.

Two files:
- `deals.csv` — what actually happened (the ground truth)
- `journalist_claims.csv` — who claimed what, and at what confidence stage

The three `EX*` rows in each file are examples showing the format. **Delete them
before you start.**

---

## Step 1 — Fill `deals.csv` (target: 50 deals)

Pick ONE closed window: **Summer 2025** (transfers completed roughly Jun–Sep 2025).
Closed = the window is over, so every outcome is known. That's what makes it usable.

Columns:
| column | what to put |
|--------|-------------|
| `deal_id` | a number, 1..50 |
| `player` | player's full name |
| `from_club` / `to_club` | the SELLING club and the BUYING club |
| `window` | `2025-summer` |
| `outcome` | one of: `completed`, `collapsed`, `unknown` |
| `fee_eur_actual` | final fee in EUR if completed (approx is fine), blank if collapsed |
| `outcome_date` | YYYY-MM-DD the deal was confirmed or definitively died |
| `outcome_source_url` | a link proving the outcome (club announcement, BBC, etc.) |
| `verified` | `YES` once you've confirmed it against a real source |
| `notes` | anything useful |

**Critical: you need BOTH outcomes.**
- ~35 `completed` deals (transfers that actually happened)
- ~15 `collapsed` deals (transfers that were heavily reported but did NOT happen —
  the player stayed, or went somewhere else, or the move was officially denied)

The collapsed ones are the hard negatives. Without them you can't tell a careful
journalist from one who just shouts "DONE DEAL" at everything. They're harder to
find — look for sagas that dominated headlines for weeks then fizzled.

Where to get outcomes (free, legal):
- Wikipedia "List of [year] summer transfers" pages
- Club official announcement pages / verified club social accounts
- BBC Sport, Sky Sports transfer centre
- A free transfers API later (football-data.org, etc.) — manual is fine for 50 rows

---

## Step 2 — Fill `journalist_claims.csv` (5 deals × ~3 journalists)

Pick 5 of your deals (mix completed + collapsed). For each, find 3 different
journalists who reported on it and log what they claimed and when.

Columns:
| column | what to put |
|--------|-------------|
| `claim_id` | a number |
| `deal_id` | the matching `deal_id` from deals.csv |
| `source_name` | the journalist or outlet |
| `platform` | `twitter`, `news_site`, `reddit`, etc. |
| `claim_date` | YYYY-MM-DD they made the claim |
| `stage` | one of the stages below |
| `source_url` | link to the claim |
| `raw_quote` | the actual words they used |
| `verified` | `YES` once confirmed |

### Stage vocabulary → implied probability (the scoring key)

Pick the stage that matches the *strength* of their claim. This implied
probability is what the Brier score grades them against.

| stage | meaning | implied p(completes) |
|-------|---------|----------------------|
| `rumour_link` | "linked with", loose gossip | 0.15 |
| `interest` | "X are interested in" | 0.15 |
| `talks` | "in talks / negotiating" | 0.35 |
| `advanced` | "advanced talks / close" | 0.60 |
| `agreement` | "agreement reached / personal terms done" | 0.80 |
| `medical` | "medical scheduled/underway" | 0.92 |
| `here_we_go` | "done deal / here we go" | 0.99 |
| `official` | club has officially announced | 0.99 |
| `denied` | source says it's NOT happening | 0.02 |

This is the whole trick: someone who said `talks` (0.35) about a deal that
collapsed barely loses points — they hedged. Someone who said `here_we_go` (0.99)
about the same dead deal gets hammered. That asymmetry is what makes the score mean
something.

---

## Step 3 — sanity check before any code

Once filled, eyeball it: do the journalists you *think* are reliable line up with
deals that completed, claimed at high stages? Do the noisy accounts have
`here_we_go` claims on `collapsed` deals? If yes, the model will work. If the data
looks random, the idea needs rethinking — and you found that out in an afternoon
instead of after building a scraper.

Then run `/plan-eng-review` to build the scoring script on top of this.
