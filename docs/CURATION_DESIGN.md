# Curated-play redesign — from "good gate" to "best suggested plays"

Design doc, July 2026. Written after the audit fixes made the underlying
metrics honest (push-aware EV, `p_used` staking, symmetric grading, per-book
same-line CLV, variance-aware gate). This is the plan for the *selection* layer
that sits on top of them.

## Where we actually are (not greenfield)

The demonstrated-edge gate already does most of the structural work the "best
plays" thesis needs, and the audit just hardened it:

- `edge_gate.market_stats` rolls the graded ledger into per-`(sport, market)`
  `avg_clv`, `clv_lb` (one-sided lower bound), `clv_sd`, `clv_pos_rate`, `roi`,
  `win_rate` over a 180-day window — **the raw material for ranking by proven
  edge already exists and is computed every run.**
- `classify` CLEARs a market only when its lower-bound mean CLV clears the
  floor; GATEs it only on a bigger, clearly-negative sample; else PROBATION.
- `HELD_MARKETS` hard-holds NBA/NFL/NHL moneylines **and totals** (the T0.1
  instrument condemned both) to projections-only.
- `stake_mult` / `cap_tier` translate gate status into Kelly size and tier caps.
- `plays.game_play_candidates` drops GATED markets, and a play is only "sharp"
  (headline-pushed) when its EV is in-band **and** the market is CLEARED.

So selection is *already* CLV-governed at the market level. What it is **not**
yet is CLV-*ranked* at the play level, and its EV bands are global guesses. The
four components below are the delta.

## The core problem the redesign solves

Today a play's prominence is driven by `plays._tier(ev)` — a global EV band
(`SHARP_EV_MIN=0.02`, `SHARP_EV_MAX=0.06`, `STALE_EV=0.08`). Two consequences:

1. **We rank by disagreement size, not by proven edge.** A 3% in-band EV in a
   market whose rolling `avg_clv` is +4% is treated the same as a 3% EV in a
   market whose `avg_clv` is +0.3%. The audit's central finding is that raw
   model disagreement is *not* edge — CLV is. The board should lead with the
   plays in the markets where we most reliably beat the close, not the plays
   where the model most disagrees with the price.
2. **The EV band is one-size-fits-all.** The 2–6% "validated sweet spot" is a
   global constant applied to every market. Where CLV actually turns positive
   differs by market — efficient markets may have no positive band at all
   (consistent with the hold), soft props may have a higher/wider one.

## Component 1 — Conviction ranking (highest leverage, purely additive)

Attach two fields to every candidate in `plays._game_play` (and the prop path),
computed from the gate table that's already loaded:

- `expected_clv`: the market's rolling `avg_clv` (or, better, a per-market
  EV→CLV slope — see Component 2 — evaluated at this play's EV). This is the
  system's own estimate of how much this play beats the close.
- `conviction`: the ranking key for "best plays." Combine three things already
  in hand:
  - **proven edge** — the market's `clv_lb` (lower-bound mean CLV; already the
    CLEAR criterion), so a market can't rank high on a lucky point estimate;
  - **in-band fit** — a 0/1 (or smooth) factor for whether this EV sits in the
    market's positive-CLV band (Component 2), which zeroes out the stale-line
    tail the way `verify` already flags it;
  - **sample confidence** — `min(1, clv_n / GATE_CLEAR_MIN)`, so a thin market
    is discounted, not trusted.

  `conviction = clv_lb × in_band × sample_confidence`. CLEARED markets with a
  strong, well-sampled CLV and an in-band EV float to the top; a big-EV stale
  line in a barely-sampled market sinks. The headline "Top Plays" surface is
  then **ranked by conviction, not EV** — that is the direct answer to "best
  suggested plays."

This is additive: no existing behavior changes, the numbers come from data the
gate already produces, and it degrades gracefully (no history → conviction 0 →
ranked below any proven play, same as PROBATION today).

## Component 2 — Per-market EV band fit from the ledger

Replace the global `SHARP_EV_MIN/MAX/STALE_EV` with a per-market band derived
from the ledger: bin graded bets by EV, compute realized CLV (and ROI) per bin,
and take the EV range where CLV is reliably positive as *that market's* sharp
band. New `edge_gate.ev_band(sport, market)` returning `(lo, hi)` with the
global constants as the under-sample fallback. Feeds both `_tier` and the
`in_band` factor in Component 1. Effect: the "sweet spot" becomes market-specific
evidence instead of a single hand-set guess, and markets with no positive band
simply produce no sharp plays.

## Component 3 — Slate-level correlation-aware staking

Today each play is sized independently at quarter-Kelly × `stake_mult`.
Correlated exposures — same game across markets, same team across props, SGP
legs — overbet the joint position under independent Kelly. Add a slate-level
sizing pass after selection: group correlated plays (reuse the correlation
priors already in `sgp.py`), apply a joint/simultaneous-Kelly haircut within a
group, and cap total slate exposure. Lowers drawdown without touching which
plays are chosen. Independent of Components 1–2.

## Component 4 — Market-anchored deviation selection (T3.1 substrate)

The cleanest long-run form: publish the market-anchored projection (accurate
now, per the T0.1 table) but select and rank on the model's *deviation from the
anchor*, weighted only where that deviation has proven CLV. This is Component 1
with the anchor as the reference point instead of the raw price. Bigger, gated
on building T3.1 and validating it against open→close CLV, and it builds on the
now-consistent published projections (the T1.3 reconciliation shipped in the
audit). Sequenced last.

## Sequencing caveat (blocks empirical fitting)

Components 1 and 2 fit on the ledger's CLV and grading, and the audit **changed
what those mean** (symmetric under/spread grading, same-line per-book CLV, push
refunds). The historical ledger is now a mix of old- and new-definition rows.
Before fitting per-market bands or trusting `avg_clv` for conviction, either
re-grade the existing ledger under the new definitions or fit only on
post-fix data. Otherwise the redesign reintroduces exactly the dishonesty the
audit removed. Component 1's *plumbing* (attaching conviction) can land
immediately; its *rankings* only become trustworthy once the CLV history is
consistent.

## Recommended build order

1. **Component 1** — conviction fields + rank the headline board by conviction.
   Additive, uses existing data, directly answers "best plays."
2. **Component 2** — per-market EV bands; upgrades both `_tier` and Component 1's
   `in_band` factor.
3. **Component 3** — slate-level correlation-aware staking.
4. **Component 4** — market-anchored deviation selection, after T3.1 is built
   and validated.

Discipline unchanged from the rest of the repo: each component ships behind the
gate, and any threshold it introduces is fit on consistent post-fix CLV, not a
hand-set guess.

## Status — July 2026

- **Component 1 (conviction ranking):** shipped (PR #74).
- **Component 2 (per-market EV bands):** shipped (PR #74). Already fits MLB
  moneyline to (0.02, 0.03) on the live ledger.
- **Step 2 (synthetic clean-CLV seed):** shipped. `scripts/seed_curation_history.py`
  runs the current model production-mode over the backfill and writes
  `data/history/curation_seed.json`; `edge_gate.conviction_prior` /
  `blend_conviction` fold it into conviction **ranking only** (never stake
  sizing), and only while a market's live CLV is thin — self-retiring. OFF by
  default (`config.CURATION_SEED_ENABLED`), pending owner review of the numbers.
  Generating it surfaced and fixed a real bug: the backtest's per-bet CLV metric
  (`avg_bet_clv`) was inflated by stale-line best-of-book longshots, inconsistent
  with how the live gate scores CLV; both now use the curated band (`_band_clv`).
  The seed empirically confirms the T0.1 finding per-market — NBA/NFL/NHL
  moneylines and totals run −3% to −8% CLV (the `HELD_MARKETS` set), while only
  MLB moneyline is marginally positive (+1.5%, lower bound ~0).
- **Component 3 (correlation-aware slate staking):** next; a real-money sizing
  change, to land behind a 0-default knob with a before/after on a real slate.
- **Component 4 (T3.1 market-anchored deviation selection):** after Component 3;
  needs the open→close CLV validation cycle before it earns weight.

## Status update — Component 3 & 4 (July 2026)

- **Component 3 (correlation-aware slate staking):** shipped (PR #77).
  `project547/staking.py`; OFF by default (`SLATE_CORR`, `SLATE_MAX_EXPOSURE`).
- **Component 4 (T3.1 market-anchored published projection):** machinery shipped,
  weights OFF by default (`config.PROJECTION_ANCHOR = {}`). The pipeline stores
  the de-vigged market moneyline fair per game and `_attach_anchored_projection`
  publishes `*_pub` columns (`home_win_prob_pub`, `proj_total_pub`, `margin_pub`,
  `home_exp_pub`, `away_exp_pub`) blended toward the market; the RAW columns are
  left untouched so edge detection is unaffected. The card shows the anchored
  number as headline with the raw model number alongside ("… · model NN%"), and
  is byte-for-byte unchanged at weight 0. `scripts/validate_anchor.py` sweeps the
  weight per (sport, market): accuracy improves **monotonically** toward the
  market on every one (e.g. NBA moneyline Brier 0.210→0.190, n=4435; NHL total
  MAE 1.865→1.784, n=3641), confirming T0.1. Because the backtest proxies the
  market with the *closing* line, that gain is an upper bound, so the conservative
  recommendation (half the gain, keep model deviation for edges) is α≈0.5 across
  the board — left OFF pending review. Known gap: MLB margin/side-score anchoring
  is inert (MLB rows use `home_exp_runs`, not the generic `home_exp`/`margin_mean`);
  MLB moneyline and total anchor correctly.

All four curation components + step 2 are now in; every real-money/behavioral
lever ships behind a default-off knob validated against the market baseline.
