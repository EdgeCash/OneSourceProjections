# Daily Wager Workbook

A downloadable, **editable** Excel/Google-Sheets workbook of the day's slate.
The model's win probability for every market is **locked**; the bettor types
the price *their* sportsbook is showing into the yellow cells, and the edge,
expected value, and quarter-Kelly stake recompute **in-cell** — in Excel *or*
Google Sheets, offline. Odds are the only thing that changes through the day, so
they're the only thing you edit.

Built by `project547/workbook.py`, served from the dashboard
(**🎯 Projections → 📥 Daily workbook**), and rebuilt every hour by the GitHub
Action into `data/output/workbook/latest.xlsx` (+ a dated archive), so it ships
and redeploys with the rest of the data.

## Why this exists (the gap it fills)

Competitive research across the +EV tool category (OddsJam, Unabated, Outlier,
RebelBetting…) and the DFS projection-sheet vendors found a consistent shape:

- The betting tools **de-vig a sharp market for you and lock the math in a live
  web app** you can't touch — and their **#1 user complaint is stale lines**.
- The DFS vendors ship genuinely editable sheets, but for **fantasy
  projections, not betting odds → EV → Kelly**.
- DIY bettors hand-build Google-Sheets EV/Kelly calculators, but must **be their
  own modeler** and babysit fragile live-odds imports.

So an **editable workbook that brings its own model probability, takes the
user's own odds, and pre-builds the correct EV/Kelly math** sits in open space.
Bring-your-own-odds also structurally **neutralizes the staleness problem** —
there's no feed to go stale because the price is the one the user is literally
looking at.

## How the math stays honest

Every formula mirrors `project547/odds.py` exactly, so the sheet and the engine
never disagree:

| Column | Formula (per row) |
|---|---|
| Implied % | from the editable American-odds cell |
| Your Edge (EV) | `p·b − (1−p)` where `b` = decimal profit, `p` = model prob |
| ¼-Kelly % | `max(0, (p·b − (1−p))/b) × KellyFraction` |
| Stake $ | `min(Kelly%, MaxStake%) × Bankroll` |
| Verdict | banded text off the EV cell |

**Probability reconciliation.** The engine shrinks raw model probabilities
toward the market (`MARKET_SHRINK`) before pricing the bets it actually makes,
so the workbook displays the probability the engine *bet on*: where a stored EV
exists, it recovers `p = (EV+1)/(b+1)` from the engine's own EV and price, so the
sheet's EV at the posted price equals the engine's to the 4th decimal. Where the
engine didn't price a market, it falls back to the raw model probability (still
useful for bring-your-own-odds) and the Verdict flags large raw edges as
**⚠ Verify**. **Top Plays only ever shows engine-priced rows** — never raw fat
edges on markets the engine deliberately skips.

**Verdict bands** (research- and brand-aligned — the model's validated edge
lives in the moderate band; very high EV usually means a stale price, not free
money):

- `Pass` — no edge at your price.
- `Thin` — positive but below your min-edge setting.
- `✅ Sharp band` — 2–6% EV, where forward-tested CLV held up.
- `Strong` — 6–8% EV.
- `⚠ Verify (stale?)` — 8%+; double-check the line.

## Tabs

- **Read Me** — how it works, what each Verdict means, the disclaimer + responsible-gambling line.
- **Settings** — bankroll, Kelly fraction, min edge, max stake. Edit once; every tab reprices.
- **Top Plays** — the biggest engine-priced edges, glanceable on a phone. Nothing's a lock.
- **Research Hub** — an index plus one premium matchup *page* per game (see below).
- **`<SPORT>` Games** — moneyline / total / spread, every game, bring your own odds.
- **`<SPORT>` Props** — priced player props (Over side off the model).
- **Track Record** — the performance summary (Brier, units, ROI, CLV beat-rate), losses included.

## Research Hub (the "website to go")

Each game gets its own page: a **premium matchup card** (the same graphic the
dashboard renders — team panels with records/streak/recent results/power & SOS
ranks, projected score, ML/RL/Total confidence gauges, per-side top-advantage
star panels, and the mirrored offense-vs-defense tables with rank pills + an
advantage column) rendered to a crisp PNG and embedded, followed by a native
**all-windows** table (Season / L30 / L20 / L15 / L10 / L5 + league ranks,
rank-shaded) so every recency window is readable offline.

The shared renderer lives in `app.ui.matchup_card_html`; `project547.cardimage`
wraps it in the brand cream/graphite theme and screenshots it with the
pre-installed **Chromium** binary (no Playwright). Data — splits, league ranks,
the advantage flag, power rank, strength-of-schedule rank, and days rest — comes
from `project547.teamstats.matchup(..., window=...)`, computed from our own
box-score logs.

**Recency window.** On the website the matchup view has a live
**L5 / L10 / L15 / L20 / L30 / Season** toggle that recomputes ranks,
advantages, and strength of schedule (people weight recency differently). The
static workbook image is rendered at one window, but the native table carries
*all* of them, so nothing is lost offline.

The Hub is **best-effort**: it needs box-score logs (and a browser for the
images), so the on-demand dashboard build skips it for speed and the hourly job
bakes the full version into `data/output/workbook/latest.xlsx`. The download
button serves that pre-built file when present and falls back to a fast,
hub-less build otherwise. A missing browser or a single bad game is skipped,
never fatal.

## Design constraints (from research)

- **Editable cells are unmistakable by sight** (yellow fill + border + ✏ header),
  not only by sheet protection — opening a protected `.xlsx` in Google Sheets
  silently drops protection, so locking can never be the only cue.
- **No macros, no Power Query, no live web imports.** `IMPORTXML` can't read a
  JS-rendered sportsbook anyway; everything is plain values + formulas that
  survive the Excel ⇄ Sheets round-trip.
- **No data-validation dropdowns as a hard dependency** (they break on Mac Excel).
- **Delivered as a download**, which sidesteps the Google-Sheets "make a copy /
  request-access / stale-tab" sharing problems.

## Building it

```bash
python scripts/build_workbook.py                 # from data/output/latest.json
python scripts/build_workbook.py --out my.xlsx   # custom path
```

The hourly job (`scripts/hourly_update.py`) calls
`project547.workbook.build_to_disk` directly after writing `latest.json`, as a
best-effort step that can never sink the run. Tests: `tests/test_workbook.py`
(formula math, EV reconciliation, structure, and — when the optional `formulas`
engine is installed — a real in-cell evaluation pass).

**Personal research. Not financial advice. No bankroll promises. Bet responsibly
— 1-800-GAMBLER.**
