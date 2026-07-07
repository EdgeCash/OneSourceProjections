# Tennis projections — what best-in-class systems do that we don't

**Scope.** Projection accuracy only: (1) match-win probability and (2) a *real*
score projection — a distribution over sets and total games — so that games
handicaps and totals fall out of the same object. Wagering logic is downstream
and out of scope. Every claim about our code is grounded in a file:line read on
2026-07-07.

---

## 1. What we produce today (audited)

Our tennis path is a **surface-blended player Elo that emits one number per match:
P(player 1 wins).** That is the entire output.

| Thing | Where | What it does |
|---|---|---|
| Rating store | `models/tennis.py:26-63` | Overall Elo + per-surface Elo (`hard/clay/grass`), `K=24`, base `1500`. `update()` (`:47`) consumes **winner/loser + surface only** — the score is never seen. |
| Surface blend | `models/tennis.py:65-72` | Effective rating = `0.5·surface + 0.5·overall` (`SURFACE_WEIGHT=0.5`, `:22`), falling back to overall when the surface rating is unseen (`:43-45`). |
| Match prob | `models/tennis.py:74-79` | `match_prob()` = the standard Elo logistic `1/(1+10^((rb-ra)/400))` on the rating gap. **This is the only projection the model can make.** |
| Surface source | `clients/espn.py:320-327` | Inferred from **tournament-name keyword lists** (`_GRASS`/`_CLAY`, `:313-317`); everything else defaults to hard. ESPN carries no surface field (`:311`). |
| Feed payload | `clients/espn.py:354-364` | `_parse_tennis` returns players, winner (bool), tournament, surface, date, completed. **No set scores, no game scores, no serve/return stats — the score is discarded before it reaches us.** |
| Pipeline rows | `pipeline.py:1044-1055` | Emits `player1_win_prob`, `player2_win_prob`, `p1_matches`, `p2_matches`. **No games, no sets, no total-games column.** |
| Pricing | `pipeline.py:1058-1103` | `_attach_tennis_edges` prices exactly one market: `tennis_moneyline` (`:1103`). No spread/total pricing exists because no spread/total projection exists. |
| Config | `sports.py:148-155` | ATP/WTA `form_days=540`; the team-sport fields (`league_ppg`, `hfa`, `sigma_*`) are explicitly unused. |

**What we DON'T produce:** a games-won distribution, a sets distribution (2-0 /
2-1 / straight-sets rate), a total-games number, a games handicap, hold%/break%,
or any serve/return quantity. And **pure Elo structurally cannot produce them** —
it is fit to win/loss outcomes and throws the scoreline away at `update()`
(`models/tennis.py:47-63`), so there is no internal object from which a games
distribution could be derived. To get games/sets you must model the match one
level lower, at the point.

**We already have the pattern in-house.** `models/soccer.py` is the proof: from
two expected-goals inputs it builds a full scoreline grid (`score_matrix`,
`:43-54`) and then reads *match result* (`outcome_probs`, `:57-70`) **and**
*totals* (`over_prob`, `:73-83`) off the same distribution. Tennis needs the
identical shape — a scoreline distribution engine — but driven by a
point-win-probability model instead of Poisson goals. The gap is that tennis has
no such engine and no serve/return inputs to feed one.

---

## 2. How best-in-class tennis systems work

There are two complementary families. The sharp market and the best public
models use **both**, then blend.

### 2a. Surface-weighted Elo (the match-win backbone) — Tennis Abstract / Sackmann

Sackmann keeps four Elos per player (overall + hard + clay + grass) and predicts
a match with a **50/50 blend of the surface Elo and the overall Elo** — which is
exactly what we do (`models/tennis.py:71`). So our *backbone is already
best-in-class in shape.* Where the reference implementations go further:

- **Match-count-aware K / confidence.** K shrinks as a player accumulates
  matches (à la Glicko), so a 1500 with 5 matches moves fast and a veteran moves
  slowly. We use a flat `K=24` (`:21`) and only *report* match counts
  (`seen()`, `:81`) without using them to damp updates.
- **Margin-of-victory / dominance weighting.** Better Elos scale the update by
  how decisively the match was won (games/sets margin), not just W/L. We can't —
  we discard the score at ingest (`clients/espn.py:354-364`).
- **Time decay / off-season regression** toward the mean between appearances.
- **Selection via Brier score**, i.e. every tweak (blend %, surface, injury
  layoffs) is kept only if it lowers Brier on held-out matches.

Elo, however good, still only yields **match-win probability**. It says nothing
about *how many games*. For that you need the point model.

### 2b. The point → game → set → match hierarchical model (the score engine)

This is the piece we are missing entirely and the reason the sharps can price
games spreads and totals that we cannot.

**Inputs:** two numbers — each player's probability of winning a point **on
their own serve** against this specific opponent, on this surface. Call them
`p_serve(A)` and `p_serve(B)`.

**The hierarchy (all closed-form given the two serve numbers):**

1. **Point → game.** Given `p` = server's point-win prob, the probability of
   holding a single game is a fixed closed-form expression (sum over 0/15/30/40
   paths plus the deuce geometric series). This is the Barnett–Clarke game
   formula; `g(p)` maps serve-point% → hold%.
2. **Game → set.** With each player's hold prob, a set is a Markov chain on game
   score (0-0 … 6-6) with an explicit **tiebreak** sub-model (a 7-point game on
   alternating serve). Yields P(set won) *and* the full distribution over set
   scores (6-0 … 7-6).
3. **Set → match.** Best-of-3 (tour) or best-of-5 (men's Slams) is another short
   Markov chain over sets, yielding P(match), the **sets distribution** (2-0 /
   2-1 …), and — by convolving the game-score distributions across sets — the
   **total-games distribution.**

Because every level is derived from the same two serve numbers, the match-win
prob, the sets line, the games handicap, and the total are **mutually
consistent** — the same property `models/soccer.py` gets from one scoreline grid.
Knottenbelt's *common-opponent* variant (estimate each player's serve/return vs
shared opponents, then run this hierarchy) is the canonical published example and
reported a positive ROI backtest; academic work (O'Malley closed forms; Barnett &
Clarke; Kovalchik's comparison studies) confirms the point model both prices
totals/spreads *and* is competitive with Elo on match-win, and that **blending
Elo with the point model beats either alone.**

### 2c. Where the serve numbers come from

The point model is only as good as `p_serve(A)` and `p_serve(B)`. Best practice:

- Start from each player's **serve-points-won% and return-points-won%**,
  surface-specific, from match logs (Sackmann's `tennis_atp` / `tennis_wta`
  GitHub repos carry per-match serve stats: aces, DFs, 1st-in, 1st/2nd-won,
  service/return points — the raw material for hold%/break%).
- **Opponent-adjust** them: combine player A's serve strength with player B's
  return strength relative to tour average (the tennis analogue of log5), so a
  big server vs a great returner lands correctly.
- **Surface-adjust** (serve dominance is much higher on grass than clay).
- **Regress to the mean** by sample size, and blend in an Elo-implied prior so
  new/thin players aren't mispriced.

### 2d. How the sharp market prices games handicaps & totals

Pinnacle-style pricing is effectively this point model: derive `p_serve` for both
sides, run point→game→set→match, read the games-handicap and total-games lines
straight off the resulting distribution, then apply margin. The handicap and
total are *not* independent guesses — they are two marginals of one simulated
match. Any system that only has an Elo match-win number (us) is locked out of
these markets by construction.

Sources: [An Introduction to Tennis Elo — Heavy Topspin](https://www.tennisabstract.com/blog/2019/12/03/an-introduction-to-tennis-elo/),
[Measuring the Performance of Tennis Prediction Models — Heavy Topspin](https://www.tennisabstract.com/blog/2017/01/15/measuring-the-performance-of-tennis-prediction-models/),
[Common-opponent stochastic model (Knottenbelt et al.)](https://www.sciencedirect.com/science/article/pii/S0898122112002106),
[Data-Driven Tennis Strategy Evaluation based on Hierarchical Markov Models](https://dl.acm.org/doi/fullHtml/10.1145/3696952.3696982),
[Boosting Markovian tennis prediction (Wang & Drekic, 2026)](https://journals.sagepub.com/doi/10.1177/22150218251412670),
[Sackmann tennis_atp match data (GitHub)](https://github.com/JeffSackmann/tennis_atp).

---

## 3. Gap analysis — ranked by impact ÷ effort

Data-readiness tags: **[HAVE]** = already in our objects; **[DERIVE]** =
computable from what we already ingest; **[NEW DATA]** = needs a feed we don't
pull today.

### Lever 1 — Serve/return point model → full games & sets distribution ★ top priority
- **Technique.** Estimate surface-specific, opponent-adjusted serve-point-win
  probabilities for both players, then run the point→game→set→match hierarchy
  (§2b) to emit P(match), the sets distribution, and the total-games
  distribution.
- **Impact:** **High.** This is the *only* way to go from match-win-only to a
  real score projection. It unlocks games spreads and totals entirely and, when
  blended, tends to sharpen match-win too. It is the single item that changes
  what class of market we can project.
- **Our gap.** Total. No serve/return anywhere; `models/tennis.py` has no
  point/game/set machinery; `pipeline.py:1044-1055` has no games/sets columns.
- **Data-readiness:** **[NEW DATA]** for the serve/return inputs — Sackmann's
  `tennis_atp`/`tennis_wta` GitHub CSVs (per-match `w_svpt`, `w_1stWon`,
  `w_2ndWon`, `w_SvGms`, etc.). The **engine** itself is **[DERIVE]** — pure math,
  no feed. ESPN's live feed (`clients/espn.py:354-364`) cannot supply serve
  stats, so this needs a new client analogous to the existing data clients.
- **Sketch.**
  1. New pure module `models/tennis_score.py` mirroring `models/soccer.py`:
     `hold_prob(p_serve)` (Barnett–Clarke), `set_dist(pa, pb)` (game Markov +
     tiebreak), `match_dist(pa, pb, best_of)` → `{p_match, sets_dist,
     games_dist, total_games_mean}`. `over_games_prob(pa, pb, line)` and
     `games_handicap_prob(...)` read off `games_dist` exactly as
     `soccer.over_prob` reads off `score_matrix` (`models/soccer.py:73-83`).
  2. New feed `clients/sackmann.py` to load/cache the match CSVs; a
     `ServeReturn` estimator producing surface-specific serve/return rates per
     player with shrinkage.
  3. Opponent-adjust: `p_serve(A vs B, surf) = tour_avg_serve(surf) +
     (A.serve_edge(surf)) − (B.return_edge(surf))`, clamped.
  4. In `project_tennis_matches` (`pipeline.py:1044-1055`), after the Elo prob,
     call `match_dist(...)` and add `total_games`, `p_over_<line>`,
     `sets_2_0`, `games_spread` columns.
- **Rank driver:** highest impact; medium-high effort (engine is small and
  well-specified; the work is the data client + estimation). **Do this first.**

### Lever 2 — Blend Elo match-win with the point-model match-win
- **Technique.** Take a weighted average (in log-odds) of the Elo match prob
  (`models/tennis.py:74-79`) and the point-model's `p_match` (Lever 1),
  weight tuned by Brier on held-out matches.
- **Impact:** **Med.** Published comparisons show the blend beats either model
  alone on match-win; also cross-checks the two engines so a bad serve estimate
  can't silently wreck a price.
- **Our gap.** We have only the Elo half.
- **Data-readiness:** **[DERIVE]** — needs Lever 1's output plus nothing new.
- **Sketch.** In `project_tennis_matches` (`pipeline.py:1047`) replace the single
  `p1_win = elo.match_prob(...)` with a blend of that and
  `tennis_score.match_dist(...)['p_match']`; expose weight as a module constant
  like `SURFACE_WEIGHT` (`models/tennis.py:22`).
- **Rank driver:** cheap once Lever 1 exists; do it immediately after.

### Lever 3 — Margin-of-victory / dominance-weighted Elo updates
- **Technique.** Scale the Elo update by how decisively the match was won (games
  or sets margin, or % of total games won) instead of flat W/L.
- **Impact:** **Med.** MOV weighting is the standard accuracy lever in every
  serious Elo (football, chess, tennis). A 6-0 6-1 win carries far more signal
  than 7-6 7-6; we treat them identically (`models/tennis.py:53-56`).
- **Our gap.** `update()` (`models/tennis.py:47-63`) takes only winner/loser.
- **Data-readiness:** **[NEW DATA]** — needs the score, which ESPN discards
  (`clients/espn.py:354-364`). *Free once we add the Sackmann feed for Lever 1*
  (its CSVs carry the score), so effort is largely shared with Lever 1.
- **Sketch.** Add `margin` param to `TennisElo.update` and a multiplier
  `mov = f(games_margin)` on the `k` term (`models/tennis.py:54-56`), mirroring
  the MOV multiplier the football Elo already uses (`models/elo.py`). Prime from
  Sackmann history in the same loop as `pipeline.py:1038-1041`.

### Lever 4 — Confidence/match-count-aware K (Glicko-style shrinkage)
- **Technique.** Make K decay with a player's match count so thin ratings move
  fast and settled ratings move slowly; equivalently carry a rating uncertainty.
- **Impact:** **Med-Low.** Improves early-season and new-player calibration and
  reduces overreaction; modest but real Brier gain.
- **Our gap.** Flat `K=24` (`models/tennis.py:21`); we track counts
  (`matches`, `seen()`, `:81`) but never use them in the update.
- **Data-readiness:** **[HAVE]** — `self.matches` already exists
  (`models/tennis.py:34,62`).
- **Sketch.** In `update()` set `k_eff = K * damp(self.matches.get(w,0))` and
  likewise for the loser; keep the current `K` as the asymptote. Pure, testable
  alongside the existing tests in `tests/test_tennis.py`.

### Lever 5 — Real surface field instead of tournament-name inference
- **Technique.** Attach an authoritative surface per match rather than
  keyword-matching the tournament name.
- **Impact:** **Med-Low.** Our surface blend is already correctly shaped
  (`models/tennis.py:71`), but its *input* is a brittle keyword list
  (`clients/espn.py:313-327`) that mislabels neutral names, indoor/outdoor
  variants, and new events → those matches get the wrong surface Elo. Fixing the
  input improves every surface-dependent number (and, once Lever 1 lands, every
  surface-adjusted serve rate).
- **Our gap.** Inference-only; no ground-truth surface.
- **Data-readiness:** **[NEW DATA]** but cheap — Sackmann's CSVs carry a
  `surface` column keyed by tournament, so it arrives *for free* with the Lever 1
  feed; join on tournament/date.
- **Sketch.** In `_parse_tennis` (`clients/espn.py:329-365`) prefer a
  surface looked up from the Sackmann tournament table, falling back to the
  existing `_tennis_surface()` keyword guess (`:320`) only when absent.

### Lever 6 — Off-season / inactivity regression toward the mean
- **Technique.** Pull ratings toward 1500 across long layoffs (injury, off-season)
  so stale ratings don't overstate a returning player.
- **Impact:** **Low.** Second-order but standard; matters most at season starts
  and post-injury.
- **Our gap.** No time component; a rating is whatever it was at last match.
- **Data-readiness:** **[HAVE]** — match dates already flow through
  (`clients/espn.py:356`, sorted at `pipeline.py:1038`).
- **Sketch.** Track `last_date` per player and regress `rating → 1500` by a
  factor of the gap before an update in `TennisElo.update`.

### Ranking summary (impact ÷ effort)

| # | Lever | Impact | Effort | Data | Why the rank |
|---|---|---|---|---|---|
| **1** | Serve/return point model → games/sets distribution | **High** | Med-High | [NEW DATA] Sackmann + [DERIVE] engine | Only path to a real score projection; unlocks spreads & totals |
| **2** | Blend Elo + point-model match-win | Med | Low | [DERIVE] | Free accuracy once #1 exists; cross-check |
| **3** | MOV-weighted Elo updates | Med | Low* | [NEW DATA]* (free w/ #1's feed) | Standard Elo lever; score arrives with #1 |
| **4** | Match-count-aware K | Med-Low | Low | [HAVE] | Data already in hand; small pure change |
| **5** | Authoritative surface field | Med-Low | Low | [NEW DATA] (free w/ #1's feed) | Fixes brittle input to an already-good blend |
| **6** | Inactivity regression | Low | Low | [HAVE] | Second-order polish |

\* Effort/data for #3 and #5 collapse once the Sackmann feed from #1 is in place —
they piggyback on the same CSVs.

---

## 4. The one-paragraph answer

Our **backbone is already best-in-class in shape** — a 50/50 surface/overall Elo
blend (`models/tennis.py:71`) matching Sackmann's design — but it emits **only
match-win probability** (`models/tennis.py:74-79`) and structurally *cannot*
produce a score, because it discards the scoreline at ingest
(`clients/espn.py:354-364`, `models/tennis.py:47`). Best-in-class systems add a
second engine we lack entirely: a **point→game→set→match hierarchical model**
driven by surface-specific, opponent-adjusted **serve/return** rates, which yields
the full **sets and total-games distribution** (and hence spreads and totals) as
mutually consistent marginals — exactly the way `models/soccer.py` already reads
match result *and* totals off one scoreline grid (`:57-83`). The single
highest-value move is to build that engine (`models/tennis_score.py`) and feed it
from Sackmann's ATP/WTA match CSVs; every other lever (Elo/point blend,
MOV-weighted updates, real surface field) is cheap and largely rides in on the
same new feed.
