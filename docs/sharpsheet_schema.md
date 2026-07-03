# Sharp Sheet — matchup data contract (MLB v2)

Every cell the sheet renders traces to one of these fields. `null`/absent → a
**DATA GAP** chip (never a fabricated value or a silent blank). The sheet is
"done" when an AI can place a sharp wager from it alone.

```
game:
  away_team, home_team        # full names        <- g.away_team / g.home_team
  away_record, home_record    # "33-31"           <- standings (GAP until wired)
  away_streak, home_streak    # "W4" / "L2"       <- standings (GAP until wired)
  game_time, venue, day_night <- g.game_time / matchup.venue / status

projection:
  away_exp, home_exp, proj_total                  <- g.away_exp_runs / home_exp_runs / proj_total
  home_win_prob                                    <- g.home_win_prob
  total_ci: [p10, p50, p90]                        <- derived from g.over_probs (sim CDF)
  confidence: {score 0-1, tier_cap, drivers:{lineups, edge, calibration, completeness}}
                                                    <- multi-factor (NEW, see below)

pitching (MLB):
  away_sp/home_sp: {name, id, hand, ip, k9, bb9, xfip, tto_flag}
                    <- probable pitcher / platoon.throws / starter_xfip / stat table / TTO
  away_bullpen/home_bullpen: {fatigue: rested|moderate|heavy, proj_ip, unavailable[]}
                    <- matchup.*_bullpen (fatigue); per-reliever unavailable = GAP

markets: for moneyline / total / run_line:
  fair_prob          # de-vigged                   <- odds.fair_two_way
  market_line, market_price, implied_prob          <- lines + odds.implied_prob
  edge_pct           # fair_prob - implied_prob (baseline labeled "de-vig consensus")
  clv_realized: {avg_clv, n}                        <- edge_gate.market_stats()[(sport,market)]

context:
  park_factor                                       <- parks.factor(home)
  weather: {temp, wind_mph, wind_dir_cf}            <- g.weather
  umpire: {name, k_index, runs_index, games}        <- g.umpire (validated table)

calibration: per market {pred, actual, n}           <- scorecard.reliability (OOF)

breakdown / trends / lineups / top_props            <- existing (teamstats.matchup / props)
```

**Confidence (multi-factor, replaces lineup-only):**
`score = w1·lineup_readiness + w2·edge_clarity + w3·calibration_quality + w4·data_completeness`,
clamped 0–1. A market whose **key driver** is a DATA GAP (e.g. Total with missing
SP) is capped at **LEAN** regardless of edge — the tier never outruns the data.

**Explicitly not faked:** forward "CLV projection" (we show *realized* CLV per
market instead — predicting the close is a separate model), and SIERA (we carry
xFIP).
