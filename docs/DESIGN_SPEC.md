# 360Five — design system & Edge Card spec

The visual half of the brand (`docs/BRAND.md` owns name, voice, pillars). This is
the reference the build follows: tokens, type, the three-act Edge Card anatomy,
and the per-sport data mapping with **MLB as the standard format**.

A living, self-contained reference render of the card lives at
`docs/design/edge-card.html` — open it in a browser to see the target.

---

## 1. Design tokens

One system, two surfaces. Both palettes carry **identical keys**, so every
`var(--…)` in the app re-skins by swapping one dict (`_PALETTES` in
`app/dashboard.py`). Brass (`--acc`) is the **only** decorative accent, which
frees `--good`/`--neg` to mean exactly one thing each: money on / money off.

### Dark — "Terminal" (go-live default)

| Token | Hex | Role |
|---|---|---|
| `--bg` | `#000000` | page ground (pure black) |
| `--card` | `#0a0a0a` | panel / card surface |
| `--card2` | `#141414` | raised / inset |
| `--line` | `#1f1f1f` | hairline borders |
| `--text` | `#EAEAEA` | primary text |
| `--muted` | `#8a8a8a` | secondary text |
| `--faint` | `#4d4d4d` | tertiary text |
| `--acc` | `#F5B841` | **copper** — brand accent, the play, "edge" |
| `--acc2` | `#3d9fff` | blue — links, section kickers |
| `--good` | `#3ddc84` | positive EV / over / win |
| `--neg` | `#ff5b4a` | negative / fade / loss |
| `--mid` / `--warn` | `#ffc93c` | caution — lean / watch (amber) |

Key numbers (L5-column stats, market odds) render pure `#FFFFFF`, weight 700, to
pop off the muted grid. `card2` is `#141414` (a raised surface), not the reference's
`#050505`, because existing call sites use it as a lifted inset on black.

### Light — "almanac paper"

| Token | Hex |
|---|---|
| `--bg` `#ece5d5` · `--card` `#faf6ec` · `--card2` `#f2ebda` · `--line` `#dbd1bb` |
| `--text` `#1a2226` · `--muted` `#586158` · `--faint` `#8a8472` |
| `--acc` `#8f6a1a` · `--acc2`/`--link` `#2f7d93` · `--good` `#2c8854` · `--neg` `#bd463d` · `--warn` `#a87d22` |

`.streamlit/config.toml` mirrors the dark values so Streamlit's own widgets match.

### Type

| Role | Family (`--var`) | Use |
|---|---|---|
| Display | Archivo (`--disp`) | headings, team names, section kickers |
| Body | Archivo (`--font`) | analysis prose, labels |
| Mono | Spline Sans Mono (`--mono`) | **every number** — odds, %, EV, units, stat cells. Always `tabular-nums`. |

Setting all data in monospace is what makes the sheet read as a **precision
instrument**, not a marketing card, and keeps odds columns aligned. When adding
numeric UI, reach for `font-family:var(--mono);font-variant-numeric:tabular-nums`.

---

## 2. The Edge Card — three acts

Order top → bottom is **answer → evidence**. Implemented in
`ui.matchup_card_html` / `_matchup_card_impl` (games) and the `_ss_*` /
`match_sheet_html` builders (Sharp Sheet, soccer/tennis).

### Act 1 — The Answer (bet ticket) · *leads the card*
Per market: **side + number · best price + book · ¼-Kelly stake (units + $) ·
grade/tier · gate badge.** Sourced from `_mc_market_calls` (pick, EV, conf,
gate, stake). The ticket now renders **first** (brass left-rail panel), not at
the bottom. Tier ladder caps by gate: gated → never above PASS, probation →
never above LEAN. A too-large edge (≥ STALE_EV) is a **VERIFY** warning, not a lock.

### Act 2 — The Receipts · *why you can trust the number*
The trust layer. Target fields (roadmap status in §4):
- **Calibration** — `raw → calibrated %`, `pred vs actual`, `Brier`, trailing n.
  The headline model% must be the *calibrated* number, so the receipt produces
  it rather than contradicting it.
- **Closing value** — signed vs open (`8.5 → 9.0 · +0.5`), reads negative when late.
- **Stress test** — strip the biggest single lever (wind, a starter) and show the
  residual edge (`+3.4% → +1.1%`), so fragility is a glance.
- **Gate / CLV** — market-specific, labelled (`MLB totals · 9.0±0.5 · n=210`),
  never a blended average.

### Act 3 — The Proof · *see it for yourself*
Matchup evidence, subordinate to acts 1–2 but never hidden:
- **Who** — team panels, records, form; confirmed starters; **clickable lineups
  → player prop cards**; injuries.
- **Where** — conditions strip: park factor, weather/wind, plate-ump, rest.
- **Why (depth)** — the curated **mirrored stat tables** (offense vs. the defense
  it faces, advantage down the middle). *Fewer columns, better rows.*

### Confidence — one ladder, not four
Retire the competing dials / gauges / rings / letter-grades in favour of a single
EV-tied ladder (`play_tier`): **PASS · LEAN · CORE · WATCH · VERIFY**, centered on
the +2–6% band where a **54.7%** win rate lives. This is the source of truth for
every confidence read on every surface.

### Banned
5-star advantage ratings (touty). Show advantage as a measured edge (rank delta /
chevrons), never stars. Any "lock/guaranteed" language. Hype emoji walls.

---

## 3. Per-sport data mapping — MLB is the standard

Every sport renders through the same three-act card and **degrades gracefully**
(missing fields read `—`, never crash — `matchup_card_html` is a never-raises
wrapper). MLB is the reference because it exercises the most zones.

| Zone | MLB (standard) | NBA / WNBA | NHL | NFL / NCAAF | Soccer | Tennis |
|---|---|---|---|---|---|---|
| Who / starters | pitchers (ERA/WHIP, CSW) | — | **goalie** (SV%, workload) | QB | — | player form |
| Lineups → props | batting order → TB/HR/K props | starters → pts/reb/ast | lines → SOG/pts | — | XI | — |
| Where | park factor, weather/wind, ump | rest, B2B | rest, travel | weather (wind), dome | — | surface |
| Why (rows) | runs/G, wOBA, K%, bullpen rest | pace, off/def rtg, eFG% | xG, SOG, goalie | EPA, pace, key numbers | xG, 1X2, O/U 2.5 | serve/return splits |
| Markets | ML, RL, total, NRFI, props | ML, spread, total, props | ML, PL, total, props | ML, spread, total | 1X2, O/U, BTTS | match-win, sets, games |

Curation rule (from the competitor teardown): **cut duplicate per-game columns**;
keep the rows that move *the market being bet* (run-environment rows for a total,
not barrel% which is a prop signal). See `docs/SHARPSHEET_SPEC.md` for the full
per-sport stat backlog (what's `[HAVE]` / `[DERIVE]` / `[NEW DATA]`).

---

## 4. Build status & roadmap

**Shipped in this refresh:**
- Unified palette to one 360Five system (Terminal black + almanac paper, copper
  accent); `config.toml` + `_PALETTES` + `--mono` token. Rebrands all sports.
- Bet ticket **reordered to lead** the matchup card (answer-first).
- Brand + design locked in `docs/BRAND.md` + this file + the reference render.

**Next (Act 2 data wiring — the highest-value follow-up):**
- Surface a **calibration strip** on the card from the CLV/Brier ledger
  (`calibration_curve` / `edge_gate.market_stats`) — reconcile the headline
  model% with `raw → calibrated`.
- Promote **line movement** (opened → now, signed) onto the ticket.
- Compute a **stress-test residual** for the dominant lever (weather for MLB/NFL
  totals; starter/goalie elsewhere).
- Label gate/CLV chips with the **specific market subset**, not a blend.

**Then:** per-sport stat curation pass (NBA/soccer/tennis row sets), the
lineup → prop-card interaction, and collapse the confidence viz to the single
ladder everywhere.
