# The wagering system — projection first, wager only where proven

Project 54.7 is a **projection engine first**. The model projects every game
(team A x.x, team B y.y, total z.z, win %); the spread / moneyline / total edges
fall out of that projection. Wagering is a **narrow, gated action taken on top of
the projection — not the product.** "Here's the number" is always on offer;
"here's a bet we'd actually make" is rare and has to be earned.

## Why the process was reworked

The old curated process treated model **EV** (our probability vs the market's)
as *edge*, uniformly across every market and sport: anything clearing the 2–6%
band got pushed. But our edge is wildly uneven by market. A 2–6% disagreement on
an **efficient** market (NBA/NFL moneylines, MLB sides at the close) is **noise,
not edge** — and those were exactly the "curated plays" that lost. The EV band
answers *which disagreements to consider*; it never answered *which markets we
actually beat*.

## The two-part gate

A play is curated only if it clears **both**:

1. **EV band** (`config.SHARP_EV_MIN..SHARP_EV_MAX`, the 2–6% sweet spot) — the
   disagreement is big enough to matter but not so big it signals a stale line.
   Above `STALE_EV` a play is flagged **VERIFY** (the market likely knows
   something), never chased.
2. **Demonstrated-edge gate** (`project547/edge_gate.py`) — the *market* has
   proven a real edge, measured by realized **closing-line value (CLV)** on a
   rolling window of the graded ledger. CLV is the leading indicator: beating the
   de-vigged close means our number was closer to the truth than the market's,
   and it converges long before noisy win/loss ROI does.

### Gate statuses (per sport × market)

| Status | When | Effect |
|---|---|---|
| 🟢 **Cleared** | ≥ `GATE_CLEAR_MIN` CLV-graded bets and avg CLV ≥ `GATE_CLV_FLOOR` | Full curation (can be a CORE PLAY / pushed) · full stake |
| 🟡 **Probation** | not enough sample yet to judge | Surfaced + tracked, **capped at LEAN**, **½ stake** — bet small to gather CLV |
| 🔴 **Gated off** | ≥ `GATE_OFF_MIN` bets and avg CLV ≤ `GATE_OFF_CLV` | Not curated, **zero stake**, tier forced to PASS |

The thresholds are **asymmetric on purpose**: a market clears on a modest
positive-CLV sample, but gating one *off* requires more, clearly-negative
evidence — so a market isn't killed on a cold streak. The window is **rolling**,
so every market continuously re-earns (or loses) its status as results accrue.
Unknown / brand-new markets default to **probation** — never a headline play
until proven.

## How it plugs in

- **Curation / push** (`plays.game_play_candidates`, `scripts/hourly_update.py`):
  each candidate carries its market's `gate` status. GATED plays are dropped;
  only a **CLEARED** market's in-band play is `sharp` (the only ones texted).
- **Confidence tier** (`ui.play_tier(ev, gate=…)`): the gate **caps** the ladder
  — GATED → PASS, PROBATION → at most LEAN. A too-large-edge VERIFY warning
  survives regardless (it's a caution, not a recommendation).
- **Staking** (`ui.build_best_bets`): ¼-Kelly is scaled by the gate —
  full / half / zero. Kelly on a market we don't beat just sizes losers.
- **Transparency** (Ledger → "Edge gate" panel): the app shows exactly which
  markets are 🟢 / 🟡 / 🔴 and why. Saying out loud where we *don't* bet is the
  anti-guru brand.

## Tuning knobs (`project547/config.py`)

`GATE_WINDOW_DAYS`, `GATE_CLEAR_MIN`, `GATE_CLV_FLOOR`, `GATE_OFF_MIN`,
`GATE_OFF_CLV`, `GATE_PROBATION_STAKE`. Start conservative and loosen as the
per-market samples grow; the calibration monitor (`scripts/tune_calibration.py`)
tracks the same CLV/ROI the gate reads.

## What to expect early

With a short ledger, most markets sit in **probation** (bet small, gather CLV)
and only the highest-volume proven market clears — currently **MLB moneyline**
(positive CLV over a real sample). That's honest: we only headline a market once
we've shown we beat it. On the ledger to date, gate-weighting the book lifted
average CLV ~+31% vs betting everything equally — it concentrates stakes on the
markets whose numbers actually beat the close, which is the whole point.
