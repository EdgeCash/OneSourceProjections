# Sharp Sheet spec — everything needed to place a wager, on one sheet

Research synthesis (five parallel audits: MLB, basketball, football, hockey, and
the cross-cutting wager layer). The layout is already good; the gap is *coverage*
— the sheet tells you **what** the model likes and **why**, but often not the
few things that turn a projection into a correctly-sized, placed bet, and several
sports' stat blocks render empty.

Readiness tags used throughout:
- **[HAVE]** — computed already, just surface it.
- **[DERIVE]** — computable now from data we already commit; no new feed.
- **[NEW DATA]** — needs a source/feed we don't hold.

The headline: **almost everything high-value is [HAVE] or [DERIVE].** The
premium additions need arithmetic and wiring, not new data. The genuine
[NEW DATA] gaps are a short, honest list at the end.

---

## 0. The universal "bet ticket" — highest value, all sports, all [HAVE-DATA]

Every audit independently landed on the same #1 gap: the Sharp Sheet
(`ui.matchup_card_html`) never shows **how much to bet** or **whether we've
proven we beat this market.** Both are already computed. Add a per-market bet
ticket to the "Model read" block:

| Field | Status | Source |
|---|---|---|
| Recommended side + number | [HAVE] | play row |
| Best available price **+ book** | [HAVE-DATA] | `lineshop.best_lines` (shown in full table, not the sheet) |
| EV% + de-vigged **fair% vs model%** | [HAVE-DATA] | `calculators.no_vig` / `baseline` |
| **¼-Kelly stake (units + $)**, gate-scaled | [HAVE-DATA] | `build_best_bets.kelly` (full table only today) |
| **Gate badge 🟢/🟡/🔴** + why | [HAVE-DATA] | `edge_gate.status_for` (Ledger only today) |
| Realized CLV chip (avg CLV, n) | [HAVE-DATA] | `edge_gate.market_stats` |
| Confirmation badge + timestamp (lineup/goalie/QB) | [HAVE-DATA] | `lineup_status`/`_status_badge` (only on fallback card) |
| VERIFY / stale-line / implausible-edge flag | [HAVE-DATA] | `build_best_bets.flag`, `play_tier` |

Note a real correctness bug found: the Sharp Sheet's "Model read" and the
full-table tier call `play_tier()` **without** passing `gate=`, so the gate's
cap on the tier is invisible exactly where a bettor acts. Wire `gate=` through.

**This one block is the single biggest win and needs zero new data.**

---

## 1. Structural gaps — sheets that render incomplete *today* (near-bugs)

These make the current sheet look broken for some sports/markets; all fixable
from committed data:

- **NBA stat blocks are empty.** `teamstats.STAT_SPECS` has no `"NBA"` key and
  `team_games()` falls through to the MLB builder, so `_matchup` swallows a
  KeyError and the NBA sheet's two stat blocks render blank. → add NBA to
  `STAT_SPECS` + an NBA branch in `team_games`. **[DERIVE]**
- **NHL has no teamstats entry at all** → the matchup stat table KeyErrors. Needs
  `NHL_PAIRS` (goals, SOG for/against, blocks, save%…) + `_nhl_team_games`
  aggregating the committed skater/goalie logs + routing. **[DERIVE]**
- **MLB Run Line & Total gauges are dead** ("—"): the pipeline never attaches RL
  or total odds/EV to game rows (only model probs), so `market_convictions` has
  nothing to price. → join `bp_game_odds`/consensus in the MLB path. **[DERIVE]**
- **NCAAF stat rows show "—"**: results-only backfill (no player logs), so
  yardage rows are blank; there is no NCAAF PBP/PPA cached. **[NEW DATA]** (CFBD).
- **Weather & park factor computed but not shown**: `matchup_card_html` never
  renders them (only the docket card shows weather); park factor feeds the model
  but has no display. → surface on the sheet. **[HAVE]**

---

## 2. Per-sport stat additions (ranked, value ÷ effort)

### MLB
1. Attach RL + total odds/EV to game rows — revives dead gauges + unlocks CLV. **[DERIVE]**
2. Surface weather (wind dir/speed, precip) + park factor on the sheet. **[HAVE]**
3. Add pitcher H/9, BB/9, ER/9, BB% to `pitcher_table` (props use real rates, not league fallbacks). **[DERIVE]**
4. Capture handedness via `hydrate=person` (one param) — unlocks all platoon analysis. **[NEW DATA, cheap]**
5. Load the committed `splits.json.gz` (batter/pitcher vs LHP/RHP, 2023–25) — a large dataset sitting dark. **[DERIVE]** (+live pull for current yr)
6. HR-allowed opponent column (fills the blank half of the HR/G row). **[DERIVE]**
7. Bullpen fatigue flag (back-to-back / 3-in-4 from reliever appearance dates). **[DERIVE]**
8. Batter ISO/BB%/K% + true wOBA/xwOBA (replace AVG/SLG proxy). **[DERIVE]**
9. Umpire K/run tendency table (from `game_context` 2016–25); live plate-ump assignment is **[NEW DATA]**.

### Basketball (NBA + WNBA)
1. Wire NBA into teamstats (see §1) — unblocks the whole NBA card. **[DERIVE]**
2. **Pace / possessions** per team-game (`FGA−OREB+TOV+0.44·FTA`) — the most-missed number; unlocks ratings & usage. **[DERIVE]**
3. Off/Def rating (pts per 100) + net rating — better than raw PPG. **[DERIVE]**
4. Four factors + eFG%/TS% rows (upgrade the existing blocks). **[DERIVE]**
5. Props: minutes model + usage rate (minutes is the #1 prop driver; already stored). **[DERIVE]**
6. B2B / rest-advantage chips (from existing `days_rest`). **[DERIVE]**
7. Opp positional defense for props — NBA **[DERIVE]** (position filled), WNBA **[NEW DATA]** (position empty).
8. Backfill NBA player logs 2018–24 → then wire an NBA per-player prop model (reuse `wnba_props`). **[NEW DATA]** then [DERIVE]

### Football (NFL + NCAAF)
1. **Wire weather into the football path** (dome-aware module exists, MLB-only today) — wind is the top totals signal; a genuine betting-edge item. **[HAVE-wiring]**
2. **Key-number (3/7/10) overlay** on spreads — no key-number logic exists; classic edge. **[DERIVE-logic]**
3. Cache NFL 2025 PBP (current stops at 2024) — keeps every derivation live. **[NEW DATA, trivial]**
4. EPA/success + pass/rush-EPA rows on the card (display/context — no standalone CLV edge). **[DERIVE]**
5. Pace/plays + seconds/play (totals variance). **[DERIVE]**
6. Red-zone TD%, 3rd-down, PROE, explosive-play, pressure/sack (matchup story). **[DERIVE]**
7. **NCAAF: CFBD caching** (PPA + SP+/FPI + returning production + talent + lines) — turns NCAAF from results-only into a real model. Highest *absolute* value, highest effort. **[NEW DATA]**

> Betting-edge vs display: our NFL QB-EPA validation showed **no CLV edge**, so
> treat EPA-family rows as *read/context* until a fresh `validate_epa` pass clears
> the gate. The clean betting-edge items are weather, key numbers, and NCAAF data.

### NHL
1. NHL teamstats block (see §1) — unblocks the card. **[DERIVE]**
2. **Goalie quality block** (SV%, GSAx-lite, form, rest, workload) from committed logs — surfaces the #1 factor with no new data. **[DERIVE]**
3. **Confirmed starting goalie** live feed (DailyFaceoff/Rotowire on the hourly job) — highest absolute value; until then tag the card "goalie unconfirmed." **[NEW DATA]**
4. Goalie term in the game model (starter-quality delta on opponent xG, MLB-starter analogy) — improves ML/total/PL, not just display. **[DERIVE+code]**
5. Saves prop v2 = `E[opp SOG] × SV%` (fixes the ECE 0.032 weakness the model's own docstring flags). **[DERIVE]**
6. Add explicit under-EV for NHL totals (only over is priced today). **[HAVE-adjacent]**
7. Special teams PP%/PK% — **[NEW DATA]** (logs have PIM, not PP goals/opp).
8. xG / Corsi / high-danger (MoneyPuck/NST) — gold-standard shot quality; real ingestion project. **[NEW DATA]**

---

## 3. The honest [NEW DATA] list (what we genuinely can't do yet)

Cross-sport, in rough value order:
- **Confirmed lineups / starting goalie / starting QB** live feeds (+ timestamp). Biggest cross-sport gap; the model is blind to who's actually playing.
- **NCAAF CFBD cache** (PPA/SP+/FPI/returning production/talent/lines) + a key.
- **NBA historical player logs** (2018–24) for an NBA prop model.
- **Injuries** beyond NFL + the props page (no NCAAF injuries; not on game sheets).
- **MLB handedness + current-season platoon splits**; pitch counts / times-through-order.
- **NHL xG/Corsi/high-danger, special teams, faceoffs, PP1 deployment**; historical puck-line/closing lines (`spread_home` null in committed games).
- **Bet-percentage / ticket data** for steam & reverse-line-move detection.
- **Travel miles / altitude / time-zone** (needs venue geocoding).

---

## 4. Suggested build order

**Phase 1 — no new data, biggest perceived jump (do first):**
the universal **bet ticket** (§0) + the structural fixes (§1: NBA & NHL teamstats,
MLB RL/total gauges, weather/park on the sheet, `gate=` into `play_tier`).
This alone makes every sheet feel complete and actionable.

**Phase 2 — [DERIVE] stat enrichments per sport:** basketball pace/ratings/four
factors + minutes/usage; MLB pitcher rates + platoon splits + bullpen fatigue;
football EPA/pace/efficiency rows + key numbers; NHL goalie-quality block + saves v2.

**Phase 3 — [NEW DATA] acquisitions:** confirmed-lineup/goalie/QB feed, NCAAF
CFBD cache, NBA player-log backfill, then the shot-quality / bet-% layers.

Every new market/stat still ships behind the demonstrated-edge gate — surfaced
and tracked in probation until it proves CLV.
