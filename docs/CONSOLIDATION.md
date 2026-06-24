# Consolidation & provenance

How EdgeCash's sports-projection work was consolidated, what was brought into
this repo, and where the archival (paid, never-regenerated) data lives.

## Decision

The original plan (`BestBets/MIGRATION_PLAN.md`) proposed a brand-new `BestBets`
repo. After a file-by-file inventory of all five source repos, **OneSourceProjections
was found to already be the canonical engine** (newest, cleanest, best-tested,
NFL+NCAAF configured, and still running an hourly bot). The decision was therefore
to **consolidate into OneSourceProjections in place** rather than reassemble a
working system in a new repo — keeping full git history, the live cron, and all
data, and avoiding an unnecessary Git-LFS migration of bulk paid data.

Consolidation is **additive**: best-of-breed modules the other repos had and OSP
lacked were grafted in; nothing working was torn out.

## Secret sweep (done first, per plan)

Scanned all six repos (working tree + git history) for committed credentials.
**Result: clean.** No committed `.env` files (current or deleted-in-history), and
no live provider keys. Every match was an env-var reference (`os.environ.get` /
`config.secret`), an env-var *name* constant, a test fixture, or a `game_pk`/
`game_id` hash in data files. No rotation needed. All keys resolve via
`config.secret()` (env → Streamlit secrets); `.env.example` documents them.

## What was brought in (provenance)

| Added to OSP | From | Why |
|---|---|---|
| `onesource/epa.py` | new (informed by `edge-equation-v1` Elo/composites + `Sports-projections` football model + research) | Opponent-adjusted EPA/play ratings — the #1 accuracy lever OSP lacked |
| `onesource/clients/nflverse.py` | new (extends OSP's existing nflverse game-results ingestion) | NFL play-by-play → team EPA (free, no key) |
| `onesource/clients/cfbd.py` | new (CFBD source flagged by research) | NCAAF advanced data: PPA/EPA, SP+/FPI, returning production, talent, lines |
| `tests/test_epa.py` | new | Offline tests for the above |
| `docs/research/*`, `docs/inventory/*` | research agents + repo readers | SOTA methodology, data/feature, modeling research + per-repo manifests |
| `docs/ACCURACY_ROADMAP.md`, `docs/research/00-synthesis.md` | this consolidation | The updated build outline driven by the research |
| `CFBD_API_KEY`, fuller `.env.example` | — | Wire the new free NCAAF source |

OSP already had (confirmed by audit, not re-added): devig, fractional Kelly,
market-shrink blending, CLV tracking, calibrated EV bands, MOV+regression Elo,
walk-forward backtest, nflverse game-results ingestion.

## Archival data — the source repos remain the archive of record

Per the plan, **paid/irreplaceable data is never regenerated**. The bulk raw
paid feeds were **not** copied here (they'd blow GitHub's free LFS quota and add
nothing the engine reads — OSP's `data/history/` is the operational store). They
remain preserved in their source repos at the commits below. Pull from these if a
dataset is ever needed; do not re-call the APIs.

| Dataset (approx size) | Source repo | Path | Commit |
|---|---|---|---|
| Rotowire player feed (907 MB) | Sports-projections | `data/raw/rotowire/` | `71a22fc` |
| Historical box-score backfill incl. NCAAF 2004–2025 (235 MB) | Sports-projections | `data/backfill/` | `71a22fc` |
| Odds-API / OpticOdds / Rundown history, closing lines (~60 MB) | Sports-projections | `data/odds_api_historical/`, `data/optic_odds/`, `data/closing_lines/` | `71a22fc` |
| Vendor card images (135 MB) | Sports-projections | `data/vendor_cards/` | `71a22fc` |
| NFL/NCAAF backfill 2021–2025 (67 MB) | edge-equation-v1 | `data/backfill/{nfl,ncaaf}/` | `04ae894` |
| MLB/NBA/NHL/WNBA closing lines (45 MB) | edge-equation-v1 | `data/closing_lines/` | `04ae894` |
| Per-team metric warehouse (NFL 32, NCAAF 755 teams), odds.db, Statcast | Sports-stats-data | `data/` | `da46589` |
| Paid BettingPros odds/props, de-vigged closing lines, 4k+ CLV pick ledger | profit-hunt | `data/research/`, `data/closing/` | `a23ed56` |

Operational data **migrated into / already in** this repo: `data/history/`
(curated closing lines, results, backfill incl. NCAAF back to 2004, BP odds,
calibration, Elo, player logs, Statcast, snapshots) — the store the engine reads.
Integrity verified (identical md5 manifest on copy).

## Salvage candidates noted but not yet ported (see roadmap)
- `profit-hunt tenths/math/` (isotonic, ensemble, adaptive Kelly) — useful for
  Stage 3 calibration if OSP's existing calibration proves insufficient.
- `edge-equation-v1 engines/nfl/features/{team_elo,composites}.py` — reference
  for the Stage 4 QB/composite signals.
- `Sports-projections src/engine/football/` — leakage-aware power model; its
  approach is already subsumed by `models/generic.py`.

Old repos remain untouched as the archive of record.
