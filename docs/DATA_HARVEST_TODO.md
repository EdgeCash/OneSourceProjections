# Data-harvesting to-do — road to model projections for every major sport

Goal: put out **premium** projections for every sport we cover, and add new sports
**only where there's a legit pathway to premium** (not mediocre content-filler).

This list is grounded in how the pipeline actually loads data today:

- Historical/reference data is **committed** under `data/history/…` in fixed shapes:
  - Games:  `data/history/backfill/<sport>/<year>/games.json.gz`
  - Player logs: `data/history/backfill/<sport>/<year>/player_games.jsonl.gz`
  - Closing lines: `data/history/closing_lines/<sport>/<year>.jsonl.gz`
  - NFL play-by-play: `data/history/pbp/nfl/*.parquet`
- Live slates come from **free** APIs (MLB StatsAPI, ESPN) + keyed vendors
  (FantasyPros, BettingPros, CFBD, The Odds API).
- A market only becomes a *headline play* once the **edge gate** proves CLV on it,
  so every new sport/market starts in probation and earns its way up. That's our
  guardrail against "mediocre but shipped."

Legend: **Effort** S/M/L · **Value** = impact on projection quality / new revenue.

---

## Where each sport stands today

| Sport | Game model | Player/props | Market (closing-line) history | Biggest gap |
|---|---|---|---|---|
| **MLB** | ✅ gold standard (neg-binomial sim, xFIP, park, weather) | ✅ live & calibrated | 2026 only | historical probable starters (backtest floor) |
| **WNBA** | ✅ tuned (Elo + off/def) | ⚠️ vendor-only; player logs **2018–26 on disk, unused** | 2026 only | wire on-disk logs into a rate model |
| **NBA** | ✅ tuned | ⚠️ vendor-only; logs 2026 only | 2020–2026 | historical player logs 2020–24 |
| **NFL** | ✅ primed, **EPA not wired** | ⚠️ vendor-only; logs 2025 only | 2016–2025 (deep) | nflverse PBP cache → turn on EPA/QB signal |
| **NCAAF** | ✅ configured, untested live | ❌ none (player logs omitted) | ❌ **missing entirely** | closing lines + cache CFBD (SP+/PPA/talent) |
| **NHL** | ✅ Poisson + Elo | ❌ **no skater data → props disabled** | 2021–2026 | skater game logs (unlocks props) |

---

## Part A — Priority order (do these in this sequence)

1. **NFL nflverse play-by-play cache** — biggest accuracy lever, free, easy. (M)
2. **NHL skater game logs** — unlocks a whole market (props) that's currently off; free official API. (M)
3. **NCAAF closing lines + CFBD cache** — NCAAF can't be validated at all without these. (M)
4. **WNBA: wire existing player logs into a rate model** — data's already committed; mostly code. (S–M)
5. **NBA historical player logs (2020–24)** — enables props backtest. (M)
6. **MLB historical probable starters (2016–25)** — clears the backtest "floor" caveat. (M, low urgency — live model already strong)
7. **New sport: Tennis** — strongest premium pathway of the three you asked about. (L)
8. **New sport: Club soccer** — real pathway via Dixon-Coles + xG; scope to leagues, not the World Cup. (L)
9. **New sport: UFC** — only if you want it; hardest to be non-mediocre. (L, optional)

---

## Part B — The six sports we already cover

### 1. NFL — cache nflverse PBP, then turn on EPA  ·  Value: high · Effort: M
The game model is built and the EPA code exists (`project547/epa.py`,
`clients/nflverse.py`) but has **never run in production** — `epa_blend` and
`qb_coeff` are pinned to 0 pending cached data.

- [ ] **Harvest:** nflverse play-by-play, seasons **2016–2025** (ideally back to 2019+).
  - Source (free, GitHub Releases):
    `https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_<year>.parquet`
  - Land in: `data/history/pbp/nfl/play_by_play_<year>.parquet`
- [ ] Run `scripts/validate_epa.py` to re-clear the Stage-1 gate, then set `epa_blend > 0`.
- [ ] **QB signal (Stage 4):** from the same PBP, build per-QB rolling passing EPA + CPOE,
  opponent-adjusted, shrunk by attempts; plumb projected starters from depth charts.
- [ ] Backfill NFL `player_games.jsonl.gz` for 2016–2024 (only 2025 on disk today) for props backtesting.

### 2. NHL — harvest skater logs, switch props on  ·  Value: high · Effort: M
NHL is the most data-starved sport: **no player/skater data at all**, so props
are entirely disabled. Game model (Poisson + Elo) is fine.

- [ ] **Harvest:** per-game skater + goalie box lines, seasons **2021–2026**.
  - Source (free, official, no key): NHL Web API `https://api-web.nhle.com`
    (game boxscores → skater G/A/SOG/TOI, goalie saves). Optional xG from MoneyPuck.
  - Land in: `data/history/backfill/nhl/<year>/player_games.jsonl.gz`
    (match the WNBA/NBA JSONL shape so `playerlogs.py` picks it up).
- [ ] Add an NHL props branch (SOG, points, goalie saves) modeled like MLB props
  (Poisson/neg-binomial per-player rates), gated by the edge gate.

### 3. NCAAF — closing lines + cache CFBD  ·  Value: high · Effort: M
Configured but **cannot be validated**: no closing-line history on disk, and CFBD
advanced stats (SP+/PPA/FPI/talent) are live-fetched, not cached.

- [ ] **Harvest closing lines**, seasons **2019–2025** → `data/history/closing_lines/ncaaf/<year>.jsonl.gz`.
  - Source: **CFBD `/lines` endpoint** (same key you already need) is the cleanest path —
    it returns per-game book lines incl. closing. (Avoids a paid odds vendor.)
- [ ] **Cache CFBD** `/ppa/teams`, `/ratings/sp`, `/talent`, `/player/returning` per season
  → a committed JSON under `data/history/` so backtests don't burn the API quota.
  - Key: `CFBD_API_KEY` (free ~1k calls/mo; the **$10/mo Patreon tier** lifts to ~75k —
    worth it before any multi-season backfill).
- [ ] Wire PPA/SP+ into the model as a prior; seed early-season teams with SP+ + returning
  production + talent (Stage 2), and handle FCS opponents (replacement level) + neutral sites (no HFA).

### 4. WNBA — turn committed logs into a rate model  ·  Value: med · Effort: S–M
Player logs **2018–2026 are already committed** but the model still leans on vendor
projections. This is mostly a code task, not harvesting.

- [x] **Built + validated** `project547/models/wnba_props.py`: per-player
  recency-weighted, shrunk rate → negative-binomial P(over) for PTS/REB/AST/
  threes/PRA (dispersion fit from within-player variance of the logs). Walk-
  forward calibration (`scripts/validate_wnba_props.py`, n=320k) beats the naive
  baseline on LogLoss (0.651 vs 0.687), Brier (0.230 vs 0.242) and ECE (0.010 vs
  0.068) — well-calibrated across the whole reliability curve. Per-market
  dispersions were train/test-split validated (points & PRA refined on a held-
  out 2024+ set).
- [ ] **Wire into the live pipeline**: use the model's P(over) as the WNBA prop
  probability, blended with the vendor projection as a prior; surface on the prop
  sheet. Ships behind the edge gate (probation until CLV proves out).
- [ ] **Harvest closing lines 2018–2025** → `data/history/closing_lines/wnba/<year>.jsonl.gz`
  (only 2026 on disk) so props/edges can be backtested for CLV.
  - Source: The Odds API historical (paid) or an existing EdgeCash archive if available.

### 5. NBA — historical player logs for props backtest  ·  Value: med · Effort: M
Game model is live and tuned; props are vendor-dependent and only 2026 player data
exists, so props can't be backtested.

- [ ] **Harvest** `player_games.jsonl.gz` for **2020–2024**.
  - Source (free): `stats.nba.com` (via `nba_api`) or ESPN box scores.
  - Land in: `data/history/backfill/nba/<year>/player_games.jsonl.gz`
- [ ] Optional: internal per-player rate model (like MLB/WNBA) to reduce vendor dependence.

### 6. MLB — historical probable starters (low urgency)  ·  Value: low-med · Effort: M
Gold standard already; the only real gap is that the **backtest** runs without
historical probable starters, so it reports a conservative "floor."

- [ ] **Harvest** probable/actual starting pitchers per game, **2016–2025** → extend
  `data/history/backfill/mlb/<year>/starters.json.gz` (only 2026 today).
  - Source (free): Retrosheet (already used for game context) has starters, or MLB
    StatsAPI historical schedule `probablePitcher`.
- [ ] Backfill the **2019** `game_context` gap (park/weather/umpires) from Retrosheet.

---

## Part C — New sports: is there a legit premium pathway?

Short version: **Tennis — yes, strongly. Club soccer — yes, with more work. UFC — marginal, do it last if at all.** Ranked by how realistic "premium, not mediocre" is.

### ✅ Tennis — the best new-sport bet
Why it fits us: it's a head-to-head event (like our games), the data is **free and
clean**, and the modeling is well-established and beatable by a disciplined small op.

- **Data (free):** Jeff Sackmann's `tennis_atp` / `tennis_wta` GitHub repos — every
  match back decades, plus point-by-point for recent years; rankings, surfaces, H2H.
- **Model:** surface-adjusted **Elo** (separate hard/clay/grass ratings) blended with a
  **serve/return point model** (each player's P(win serve point) → Monte-Carlo the
  game/set/match). This is exactly our "Elo + structural model" pattern, and it
  naturally produces match winner, set spread, and **total games** — multiple markets.
- **Why premium is realistic:** surfaces, layoffs, and H2H are underpriced on
  smaller ATP/WTA/Challenger matches where books are thin. Best-of-3 vs best-of-5
  and fatigue are modelable edges.
- **First slice:** ATP + WTA main-tour singles, match winner + total games only.
- **Effort:** L (new ingest + point-sim), but low data-risk. **Recommend starting here.**

### ✅ Club soccer — real pathway; scope it to leagues, not the World Cup
Why cautious on "World Cup": international/tournament soccer is small-sample,
high-variance, and hard to be premium at. **League** soccer is very modelable.

- **Data (mostly free):** football-data.co.uk (results + closing odds CSVs),
  **ClubElo** (free API for team Elo), FBref/Understat for **xG** (scrape).
- **Model:** **Dixon-Coles / bivariate Poisson** on team attack/defense strength →
  full scoreline distribution → 1X2, totals, Asian handicap, BTTS. xG as the input
  (instead of goals) is the single biggest quality lever. This is the **same
  low-event Poisson family as our NHL model** — infrastructure largely reusable.
- **Watch-outs:** three-way result (draws), promotion/relegation + summer roster
  churn (rating resets), fixture congestion.
- **First slice:** top-5 European leagues, 1X2 + Over/Under 2.5, xG-driven.
- **Effort:** L. **Recommend second**, after tennis.

### ⚠️ UFC/MMA — possible, but hardest to be non-mediocre
Honest take: MMA is where the "mediocre content" risk is highest.

- **Data:** ufcstats.com (scrape) or Kaggle mirrors — significant strikes,
  takedowns, control time, sub attempts per 15 min; fighter age/reach/layoff.
- **Model:** fighter **Elo** + style-adjusted logistic on stat differentials →
  moneyline; method/round totals are much noisier.
- **Why it's marginal:** tiny samples per fighter (a few fights/year), extreme
  variance (one strike ends it), short-notice replacements and weight-cut/layoff
  effects, and sharp markets on marquee fights. CLV converges **slowly**, so the
  edge gate would keep most of it in probation for a long time — which is honest
  but means little headline content early.
- **If you do it:** moneyline only at first, lean entirely on the edge gate, and
  treat it as a research product until CLV proves out. **Recommend last / optional.**

---

## Part D — Suggested sequencing (calendar-aware, today = July)

- **Now (MLB + WNBA in season):** WNBA rate model from on-disk logs (#4) — pure upside,
  no harvest needed. MLB is already carrying revenue.
- **Aug–Sep (NFL/NCAAF ramp):** NFL nflverse cache + EPA on (#1); NCAAF closing lines +
  CFBD cache (#3). These must land before their seasons to be validated live.
- **Oct (NBA/NHL tip-off):** NHL skater logs → props (#2); NBA historical player logs (#5).
- **In parallel / off-season R&D:** Tennis (#7) → Club soccer (#8). Both are year-round,
  so they backfill nicely between the big-4 pushes. UFC (#9) only if you want it.

Every new market ships **behind the edge gate** — surfaced and tracked in probation,
never a headline until it proves CLV. That's how we add sports without getting mediocre.
