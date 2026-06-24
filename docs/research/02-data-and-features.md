# Data Sources & Feature Engineering for NFL / NCAAF Projection Models

Research date: 2026-06-24
Scope: data sources (free + paid), feature engineering, market-derived features, and data hygiene for building NFL and college football (NCAAF/FBS) projection and betting models.

---

## 1. Executive summary

The cheapest, highest-leverage move is to add the **free play-by-play (PBP) stack**: `nflfastR`/`nflverse` for the NFL and `cfbfastR` + the **CollegeFootballData (CFBD) API** for college. These give you EPA, success rate, CPOE, win probability, drive data and box scores at the play level — the raw material for every modern projection model (SP+, FPI, EPA-based power ratings). They are free, well-documented, reliable, and go back to 1999 (NFL) / ~2001-2014 depending on field (CFB).

Layer **market data** on top: you already pay for **The Odds API** (multi-book odds + props), **BettingPros** (consensus props/picks), and **FantasyPros** (projections/expert consensus). The single most predictive market feature is the **no-vig closing line** — devig it (start multiplicative, move to power/Shin) and use it both as a feature and as the benchmark your model must beat (closing line value / CLV).

The features with the best predictive-value-to-effort ratio: **opponent-adjusted EPA per play (off & def)**, **success rate**, **no-vig market lines**, **QB CPOE/EPA**, and for college **returning production** and **portal-adjusted talent**. Red-zone and third-down efficiency are popular but largely *descriptive/noisy* — down-weight them relative to per-play EPA.

---

## 2. Free / open data sources

### 2.1 nflfastR / nflverse (NFL) — TOP PRIORITY
- **What it provides:** Cleaned NFL play-by-play with modeled **EPA, WPA, CPOE, completion probability (cp), expected YAC (xyac_epa)**, air yards, success indicator, and 300+ columns. Plus rosters, schedules, snap counts, depth charts, participation, Next Gen Stats, draft picks, contracts, and PFR-derived advanced stats via sibling packages.
- **Granularity:** play / drive / game / season. Updated **nightly** during the season.
- **History:** complete seasons back to **1999**; expected-pass columns (cp, cpoe, xyac) back to **2006** (NFL began marking scrambles then; pre-2006 returns NA).
- **Coverage:** NFL only. Regular season + postseason (`game_type` / `week`).
- **Cost / limits:** Free, no key. Data served as pre-built release files (parquet/csv/rds) from GitHub releases — **download the releases rather than scraping** (the docs explicitly recommend this). No meaningful rate limit when pulling release files.
- **Reliability:** Very high; the de-facto standard for NFL analytics and the data engineering template the rest of the ecosystem copies.
- **Access:** R (`nflreadr` for I/O, `nflfastR` for EPA/WPA modeling), Python (`nflreadpy`).
- **URLs:** https://nflfastr.com/ · https://github.com/nflverse · https://nflreadr.nflverse.com/

### 2.2 CollegeFootballData.com (CFBD API) + cfbfastR (NCAAF) — TOP PRIORITY
- **What it provides:** The richest free college source. PBP (~1M+ rows, ~362 cols), drives, box scores, game results, betting lines (consensus + book), team/player season stats, **recruiting rankings, transfer portal, returning production, SP+/SRS/Elo ratings, advanced opponent-adjusted stats**, rosters, venues, weather (for many games), and a GraphQL endpoint.
- **Granularity:** play / drive / game / season + recruiting/portal/ratings tables.
- **History:** Game results/lines back ~2001; **play-by-play reliably ~2005-2014 onward** (older seasons sparse); advanced stats strongest 2014+.
- **Coverage:** FBS primary; FCS games appear (often as opponent rows) but FCS-internal data is thin — see hygiene (§5).
- **Cost / limits:** **Free tier = 1,000 API calls/month** (free key required). Paid via **Patreon Tier 3 ($10/mo) = 75,000 calls/mo + GraphQL realtime**; higher tiers added on request. REST **API v2** is now GA.
- **Reliability:** High and actively maintained; the standard college source. Note the low free call cap — cache aggressively or use the `cfbfastR` pre-built data + only hit the API for deltas.
- **Access:** R (`cfbfastR`), Python (`sportsdataverse-py` / `cfbd` client), direct REST/GraphQL.
- **URLs:** https://collegefootballdata.com/ · https://collegefootballdata.com/key · https://collegefootballdata.com/api-tiers · https://cfbfastr.sportsdataverse.org/ · https://graphqldocs.collegefootballdata.com/

### 2.3 Sportsdataverse
- **What:** Umbrella project / Python package (`sportsdataverse-py`) and R packages (`cfbfastR`, `hoopR`, `wehoop`, `baseballr`, `fastRhockey`, `worldfootballR`). For football it is a convenience layer over nflverse and CFBD, not a new data source. Useful if you want one Python dependency spanning NFL + CFB.
- **URLs:** https://github.com/sportsdataverse · https://pypi.org/project/sportsdataverse/

### 2.4 ESPN hidden / undocumented endpoints
- **What it provides:** Free JSON scoreboards, team/game summaries, win-probability, drives, box scores, news, and (for CFB) FPI ratings — used under the hood by cfbfastR for live data.
- **Granularity:** game / drive / play (summary), live in-game.
- **Cost / limits:** Free, no key, but **undocumented and unsupported** — schemas change without notice; throttle politely.
- **Reliability:** Medium. Great for live scores/in-game state and as a cross-check; don't make it your sole backbone.
- **Example endpoints:**
  - CFB scoreboard: `https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80` (groups=80 = FBS)
  - NFL scoreboard: `https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard`
  - Core league/teams: `https://sports.core.api.espn.com/v2/sports/football/leagues/college-football`
  - Game summary (drives/PBP/winprob): `.../summary?event={gameId}`

### 2.5 Pro Football Reference (PFR)
- **What it provides:** Deep historical NFL stats — team/player season & game logs, **full play-by-play since 1978, snap counts since 2012, game participation since 1940**, advanced passing/rushing/defense splits, Approximate Value.
- **History:** team season since 1920; game stats complete since 1939; PBP since 1978.
- **Cost / limits:** Free to read; **no official API** — scraping only (BeautifulSoup/pandas/R). Aggressive scraping gets rate-limited/blocked; respect ~1 req/3s and cache. Much of what you'd scrape is already in nflfastR/nflreadr (which ships PFR advanced stats), so use PFR mainly for fields nflverse lacks.
- **URL:** https://www.pro-football-reference.com/

### 2.6 Sports Reference — College Football
- **What it provides:** Historical CFB team/player season & game stats, schedules, ratings (SRS/SOS), poll history.
- **History:** team/player season **complete 1956-present**; offense+defense+ST for all FBS **1976-present**; pre-1956 unavailable.
- **Cost / limits:** Free to read; **no API**, scrape-only, same throttling caveats as PFR. CFBD covers most modern needs more cleanly.
- **URL:** https://www.sports-reference.com/cfb/

### Free-source comparison

| Source | League | Granularity | History | Key/Cost | API? | Best for |
|---|---|---|---|---|---|---|
| nflfastR/nflverse | NFL | play→season | 1999 (epa 2006) | free, no key | release files | EPA/PBP backbone |
| CFBD + cfbfastR | NCAAF | play→season + ratings/portal | ~2001/2005+ | free 1k/mo; $10 75k | REST + GraphQL | college backbone |
| Sportsdataverse | both | wrapper | — | free | wrapper | one Python dep |
| ESPN endpoints | both | game/drive live | recent | free, no key | undocumented | live scores/winprob |
| PFR | NFL | season/game/PBP | 1920/1978 | free | scrape | deep NFL history |
| SR-CFB | NCAAF | season/game | 1956/1976 | free | scrape | deep CFB history |

---

## 3. Paid sources (already owned: The Odds API, BettingPros, FantasyPros)

### 3.1 The Odds API
- **Unique value:** Real-time and historical **odds across ~40 mainstream US books** (DraftKings, FanDuel, etc.) for moneyline / spread / total, plus **player props in season**. This is your machine-readable line-shopping + closing-line + line-movement feed.
- **Pricing:** Free $0 (25 req/day, limited sports) → Professional **$29/mo (20k credits)** → Business **$99/mo (200k credits)**; player props for NFL require the higher tier. **Cost = 1 credit per sport-key per region per call** — combine markets with comma separators to save credits; 429 = back off.
- **Limitations:** No sharp/exchange books (no Pinnacle/Circa/crypto). For a "true line" anchor you lack a single sharpest book; mitigate by taking the **no-vig consensus / best-line** across the soft books, or pair with a separate Pinnacle feed later.
- **URL:** https://the-odds-api.com/liveapi/guides/v4/

### 3.2 BettingPros (FantasyPros)
- **Unique value:** **Consensus from 150+ experts** plus a **Prop Bet Analyzer** (consensus prop line vs. historical hit rate, matchup/defense context), grades, and a consensus dashboard tracking where high-confidence money is going. Treat its consensus prop line as a crowd-sourced "fair line" feature and a sanity check on your own prop projections.
- **Best use in a model:** expert-consensus pick direction and consensus prop lines as features; the prop analyzer for prop-specific context (recent form, defense allowed).
- **URLs:** https://www.bettingpros.com/ · https://blog.fantasypros.com/tag/bettingpros/

### 3.3 FantasyPros
- **Unique value:** **Expert-consensus player projections and rankings** (the ECR aggregation that reduces single-analyst bias). For a betting model the player projections feed prop modeling and the team/player context layer. Strong for fantasy-adjacent player-level priors.
- **URL:** https://www.fantasypros.com/

**Net:** The Odds API = the odds/line backbone (most important paid source for a betting model). BettingPros = consensus props + crowd direction. FantasyPros = player projection priors. None replace the free PBP stack — they sit on top of it.

---

## 4. High-value features & how to compute them

### 4.1 The core efficiency layer (from PBP — highest value)
- **EPA per play (offense & defense), opponent-adjusted.** EPA = change in expected points; aggregate per team per unit. **Adjust for opponent** via ridge/penalized regression with each team (off & def) as a dummy predictor of play EPA (CFBD documents this ridge approach; FPI/SP+ are EPA-based). *"If you're not adjusting for opponent strength, you're modeling schedule, not skill."* Compute: `EPA ~ off_team + def_team` (ridge), team coefficients = adjusted ratings. Split by **pass vs rush** and **early-down** for stability.
- **Success rate.** Binary per play (≥50% of needed yards on 1st down, 70% on 2nd, 100% on 3rd/4th). Opponent-adjust the same way. More stable than raw EPA; pair them (success rate = consistency, EPA = explosiveness-weighted value).
- **CPOE (completion % over expected)** and **EPA+CPOE composite** — strongest single QB-quality signal in nflfastR; very stable for QB projection.
- **Explosive-play rate.** Share of plays over a yardage threshold (commonly 15+ pass / 12+ rush, or use EPA-explosive). Predictive but definition-sensitive and noisier than success rate — include as a secondary feature.

### 4.2 Pace & volume
- **Plays per game / seconds per play (pace), situational (not garbage-time, not score-adjusted leading).** Drives volume of EPA → points. Use **adjusted pace** (control for score/situation) so you measure tempo intent, not game-script. Important for totals modeling.

### 4.3 Situational efficiency (lower priority — descriptive/noisy)
- **Third-down conversion rate, red-zone TD rate.** Popular but high-variance and largely downstream of overall efficiency; they regress hard. Use as small features or context, not primary drivers. Per-play stats are favored over per-drive as more predictive.

### 4.4 QB metrics
- EPA/play, CPOE, EPA+CPOE composite, sack rate, aDOT, pressure-to-sack. Track at the **starter** level and adjust ratings when the starter changes (injury/benching) — a major NFL edge.

### 4.5 NCAAF-specific
- **Returning production** (Connelly/CFBD): weighted % of prior production returning; **fold transfers' prior production into the numerator** (half-credit for players transferring up from lower divisions). Strong preseason prior; lower than ever in 2026 due to the portal.
- **Recruiting talent (Blue-Chip/247 composite):** now only ~1-2% of preseason SP+ weight (was 20-25% a decade ago) — **transfer-portal class quality now matters more than HS recruiting.** Use blended talent (recruiting + portal additions' prior production).
- **SP+ / FPI / SRS / Elo** (available directly from CFBD/ESPN) as ready-made opponent-adjusted power ratings — use as features or as priors/blends.

### 4.6 Game-context features
- **Rest / bye / short week:** encode rest-day differential. Evidence is mixed — older "off-bye 55-58%" edge has shrunk; recent Bayesian work finds little significant rest advantage. Include but **weight modestly**; short-week (Thu) and post-international travel effects are real.
- **Travel distance / time-zone change:** compute from venue lat/long (CFBD venues; NFL stadium coords). West→East body-clock games for night kicks.
- **Weather:** **wind is the most reliable/predictive** (>15 mph cuts passing efficiency ~8-12%); rain ~ -3-4 pts/game; temperature/snow secondary. Dome = neutral. Source: CFBD weather fields, NFL stadium dome flags, plus a weather API for forecasts. Big for **totals**.
- **Injuries:** at minimum QB in/out; ideally key-position availability. nflverse injury reports / ESPN; harder to fully automate — start with QB.

### 4.7 Strength of schedule / ratings
- Derive SOS from the opponent-adjusted ratings; feed power-rating differential (+ HFA, + rest/travel/weather adjustments) into a margin model, then map margin/win-prob to a spread/total projection.

---

## 5. Market data as features

### 5.1 Why market data is the strongest single feature
The **closing line** absorbs essentially all public information (injuries, weather, sharp money) and is the **best single predictor of long-term betting success**. Use it three ways: (1) as a feature/prior in the model, (2) as the benchmark — your projection should be evaluated by **CLV** (did you beat the close?), (3) line-**movement** (open→close) as a sharp-money signal.

### 5.2 No-vig implied probability and devigging
Convert American/decimal odds → implied prob `q_i`, then remove the bookmaker overround (`Σ q_i > 1`) to get fair probabilities `p_i`.

- **Implied prob:** decimal `d` → `q = 1/d`. American `+a` → `100/(a+100)`; `-a` → `a/(a+100)`.
- **Overround / vig:** `Σ q_i − 1`.

**Devig methods (use in this order of sophistication):**

1. **Multiplicative (normalization)** — simplest: `p_i = q_i / Σ q_j`. Spreads vig proportionally; allocates more vig to favorites. Fast, fine as a default for ~50/50 markets; biased by the favorite-longshot effect.
2. **Additive** — subtract the overround equally: `p_i = q_i − (Σq_j − 1)/n`. Equivalent to Shin for two-outcome markets; can produce out-of-range probs in lopsided markets.
3. **Power method** — solve for exponent `k` such that `Σ q_i^k = 1`, then `p_i = q_i^k`. Keeps all `p_i` in (0,1), corrects favorite-longshot bias; **recommended default for most bettors** once you can solve numerically (1-D root find on `k`).
4. **Shin method** — assumes a proportion `z` of insider/informed money; solve iteratively for `z` so probabilities sum to 1, then back out `p_i`. Best handling of favorite-longshot bias; modestly better predictive accuracy than multiplicative; needs an iterative solver. For 2-outcome markets it reduces to additive.

**Practical recipe:** start with multiplicative for speed, switch to **power** (cheap to implement, robust), and reserve **Shin** for lopsided/longshot-heavy markets and props. For props with two sides (over/under), all methods on the 2-outcome case are simple — devig over/under to a fair line, then compare to your projection.

### 5.3 Market features to engineer
- No-vig spread/total/moneyline (devigged consensus or best line across The Odds API books).
- Line movement (open→close), and your-bet-price vs close (CLV).
- Book disagreement / dispersion across books (steam/soft-line detection).
- Consensus prop line (BettingPros) vs your prop projection.

---

## 6. Data hygiene

- **Missing data:** prefer release/parquet files over live scrapes; cache CFBD calls (1k/mo free cap). For missing PBP fields use model-imputable proxies (e.g., older seasons lacking CPOE → omit those features for that era). Never forward-fill ratings across a starter change.
- **FCS games (NCAAF):** FBS-vs-FCS games distort opponent adjustment. Options: (a) drop FCS games, (b) assign a single replacement-level "FCS" team in the ridge so the blowout doesn't inflate the FBS team's rating, or (c) cap EPA contribution. Don't let an FBS team get full credit for crushing an FCS opponent. Flag games by opponent classification (CFBD provides division).
- **Neutral-site games:** zero out home-field advantage for these (CFBD/`nflverse` provide a neutral-site flag); bowls, kickoff games, international NFL games. Maintain a separate HFA term you switch off.
- **Schedule strength / opponent adjustment:** always opponent-adjust per-play metrics (§4.1); raw stats early-season are mostly schedule noise.
- **Sample-size shrinkage early season:** metrics are unstable until identities solidify — CFBD advice: **start the training/trust window around Week 5.** Use **empirical-Bayes shrinkage toward a prior** (preseason SP+/FPI for CFB; prior-year + market line for NFL), blending the prior with in-season data and increasing the data weight as the season progresses. For the NFL, the **market line is itself a strong shrinkage anchor** weeks 1-4.
- **Garbage time / score effects:** filter or down-weight low-leverage plays for pace and efficiency; use win-probability bounds (e.g., WP between 5-95%).

---

## 7. Prioritized feature list (predictive value × ease of acquisition)

Tiers: value (V) and effort to acquire/compute (E), both 1-5 (5 = high).

| Rank | Feature | League | V | E (ease) | Source |
|---|---|---|---|---|---|
| 1 | No-vig closing line (spread/total/ML) | both | 5 | 5 | The Odds API + devig |
| 2 | Opponent-adjusted EPA/play (off & def) | both | 5 | 4 | nflfastR / cfbfastR + ridge |
| 3 | Success rate (opp-adjusted) | both | 4 | 4 | PBP |
| 4 | QB EPA + CPOE composite (starter-level) | NFL (CFB partial) | 5 | 4 | nflfastR / cfbfastR |
| 5 | Power ratings (SP+/FPI/Elo/SRS) as prior | both | 4 | 5 | CFBD / ESPN / nflverse |
| 6 | Line movement & CLV | both | 4 | 4 | The Odds API |
| 7 | Returning production (portal-adjusted) | NCAAF | 4 | 4 | CFBD |
| 8 | Pace / plays per game (situational) | both | 3 | 4 | PBP |
| 9 | Weather (esp. wind) for totals | both | 3 | 3 | CFBD weather / weather API |
| 10 | Rest / bye / short week / travel | both | 2-3 | 4 | schedules + venue coords |
| 11 | Talent: recruiting + transfer class | NCAAF | 3 | 4 | CFBD |
| 12 | Explosive-play rate | both | 3 | 4 | PBP |
| 13 | Consensus prop line vs projection | both | 3 | 4 | BettingPros / The Odds API |
| 14 | Injuries (QB first) | both | 3 | 2 | nflverse / ESPN |
| 15 | Red-zone & 3rd-down efficiency | both | 2 | 4 | PBP (use as context only) |

---

## 8. Recommended additions for BestBets

**Add now (free, high value):**
1. **nflfastR / nflverse** (via `nflreadpy` / `nflreadr`) — NFL PBP + EPA backbone. Pull release parquet files.
2. **CFBD API + cfbfastR** — get the free key today; budget for **Patreon Tier 3 ($10/mo, 75k calls)** before the season since the 1k free cap is tight for backfills. This is the college backbone (PBP, ratings, recruiting, portal, returning production, lines, weather).
3. **ESPN endpoints** — free live scores / win-prob / FPI as a real-time and cross-check layer.

**Already owned — wire in:** The Odds API (odds/props/closing lines → devig pipeline), BettingPros (consensus props), FantasyPros (player projection priors).

**Defer / optional:** PFR & SR-CFB scraping only for historical fields the APIs lack; a sharp-book (Pinnacle) feed later to anchor "true" lines since The Odds API lacks sharp books.

**Build first:** an odds ingestion + **devig module** (multiplicative → power → Shin) and an **opponent-adjusted EPA/success-rate ridge** pipeline with **Week-5 trust window + empirical-Bayes shrinkage** toward SP+/FPI/market priors. FCS-collapse and neutral-site/HFA handling baked into the rating step.

---

## 9. Sources

- nflfastR / nflverse: https://nflfastr.com/ , https://github.com/nflverse , https://nflreadr.nflverse.com/
- CollegeFootballData: https://collegefootballdata.com/ , https://collegefootballdata.com/key , https://collegefootballdata.com/api-tiers , https://blog.collegefootballdata.com/api-v2-is-now-in-general-availability/ , https://blog.collegefootballdata.com/opponent-adjusted-stats-ridge-regression/ , https://blog.collegefootballdata.com/college-football-modeling-tips/ , https://graphqldocs.collegefootballdata.com/
- cfbfastR / sportsdataverse: https://cfbfastr.sportsdataverse.org/ , https://github.com/sportsdataverse , https://pypi.org/project/sportsdataverse/
- ESPN endpoints (undocumented): https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80 , https://sports.core.api.espn.com/v2/sports/football/leagues/college-football
- Pro Football Reference: https://www.pro-football-reference.com/ , https://www.pro-football-reference.com/about/coverage.htm
- Sports Reference CFB: https://www.sports-reference.com/cfb/ , https://www.sports-reference.com/cfb/about/data-coverage.html
- The Odds API: https://the-odds-api.com/ , https://the-odds-api.com/liveapi/guides/v4/ , https://the-odds-api.com/guide/rate-limit.html
- BettingPros / FantasyPros: https://www.bettingpros.com/ , https://blog.fantasypros.com/tag/bettingpros/ , https://www.fantasypros.com/
- Devigging: https://betherosports.com/blog/devigging-methods-explained , https://www.datawisebets.com/blog/devigging-sportsbook-odds
- Returning production / SP+ (Connelly): https://www.espn.com/college-football/story/_/id/48259759/college-football-returning-production-2026-notre-dame-texas , https://www.espn.com/college-football/story/_/id/48306284/2026-college-football-sp+-rankings-138-fbs-teams
- Key CFB statistical factors: https://insider.espn.com/college-football/story/_/id/11592181/the-six-key-statistical-factors-college-football
- CLV: https://vsin.com/how-to-bet/the-importance-of-closing-line-value/ , https://oddsjam.com/betting-education/closing-line-value
- Rest/weather: https://www.frontiersin.org/journals/behavioral-economics/articles/10.3389/frbhe.2024.1479832/full , https://www.covers.com/nfl/how-weather-affects-betting
