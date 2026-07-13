# Sports Wagering, DFS & Pick'em Automation Pipeline

A flat, self-contained modeling pipeline: a **SQLite-cached ingestion layer**, a
**PuLP salary-cap DFS optimizer**, and a **CDF-based Pick'em +EV engine**.

It lives in its own subtree so it can be run **side by side with the main
`project547` engine** as a direct A/B comparison — two different architectures
(mature scipy/pandas package vs. flat stdlib+PuLP pipeline) scoring the same
kind of slate, so you can judge which is faster and smoother.

> **Placement note.** The original spec asks for this exact tree at a repo root
> (`src/`, `data/pipeline.db`, `requirements.txt`, `README.md`). Since the repo
> root already holds the live 360Five engine's `README.md`/`requirements.txt`,
> the spec's tree is reproduced **verbatim inside `sports_wagering_pipeline/`**
> rather than overwriting the existing engine.

## Layout

```
sports_wagering_pipeline/
├── data/
│   └── pipeline.db        # persistent SQLite cache (git-ignored, auto-created)
├── src/
│   ├── __init__.py
│   ├── db_manager.py      # schema + local reads/writes + ID normalization
│   ├── api_client.py      # ingestion with 30-min cache enforcement & logging
│   ├── engine.py          # Pick'em CDF math + PuLP DFS optimizer + slip ranker
│   └── app.py             # CLI entrypoint + terminal view
├── requirements.txt
└── README.md
```

## Install & run

```bash
cd sports_wagering_pipeline
pip install -r requirements.txt

# DFS lineup + Pick'em slip for an MLB slate
python -m src.app --mode both --sport MLB

# WNBA DraftKings lineup only
python -m src.app --mode dfs --sport WNBA --budget 50000

# PrizePicks slip only
python -m src.app --mode pickem --sport MLB --platform PrizePicks
```

Run **from the `sports_wagering_pipeline/` directory** so `python -m src.app`
resolves the package.

### Data sources

- **Projections** come live from the **FantasyPros public API** when
  `FANTASYPROS_API_KEY` is set in the environment (same key the main engine
  uses). MLB / NBA / WNBA are wired.
- **Pick'em prop lines** (PrizePicks / Underdog / DraftKings Pick6) and any
  offline run fall back to a **deterministic sample slate** baked into
  `api_client.py`, so the whole pipeline runs end to end with no credentials —
  which is what you want for a clean comparison harness. Swap the sample
  functions for real book pulls when you wire live Pick'em odds.

## Design principles

- **Anti-bloat.** Standard library first. Third-party libs limited to
  `requests`, `pandas`, `pulp`, and `sqlite3` (stdlib). The Pick'em CDF uses
  `statistics.NormalDist` — no numpy/scipy.
- **Token budgeting.** External limit is 5,000 requests/day. Every ingestion
  call checks the SQLite cache first; a row younger than **30 minutes** is
  served from the DB, and calls are logged to `api_log` with a hard daily-budget
  guard.
- **ID normalization.** FantasyPros projections and BettingPros-style prop lines
  are matched on a deterministic `Name + Team + Sport` slug that resolves to a
  unified `master_player_id`.

## Database schema (`src/db_manager.py`)

Four tables:

| table | purpose |
| --- | --- |
| `player_projections` | `master_player_id` PK, name/sport/position, `projected_points`, `std_dev`, `salary_dk`, `last_updated` |
| `market_lines` | `line_id` PK, `master_player_id`, `stat_type`, `bookmaker`, `line_value`, `over_odds`, `under_odds`, `last_updated` |
| `player_id_map` | normalization slug → `master_player_id` (backs ID normalization) |
| `api_log` | `id` PK, `timestamp`, `endpoint`, `request_count` — the token-budget ledger |

> The spec header calls for "exactly four tables" but lists three; `player_id_map`
> is the fourth, added to persist the deterministic `Name+Team+Sport` mapping the
> ID-normalization principle requires.

## Engine functions (`src/engine.py`)

- **`calculate_pickem_edge(proj_mean, proj_std, line_value) -> dict`** — models
  the stat as `Normal(mean, std)`, reads the CDF at the line, and returns
  `over_win_rate`, `under_win_rate`, and `is_viable` (better side > **0.543**).
- **`optimize_salary_cap_dfs(sport, budget=50000) -> list`** — reads
  `player_projections` and solves a PuLP integer program that maximizes total
  `projected_points` under the DK salary cap and the sport's roster slots
  (`ROSTER_RULES`; MLB & WNBA templates included).
- **`generate_optimal_pickem_slips(sport, platform="PrizePicks") -> list`** —
  scores every `platform` line, keeps viable plays, and ranks the top **2–6** by
  distance from the 54.3% break-even.

## Comparing against the main engine

Both stacks read the same public projection source (FantasyPros). To compare:
run the main engine's slate output, run `python -m src.app --mode both` here, and
diff the lineups / plays and wall-clock. This pipeline's edge is a smaller
dependency surface and a single SQLite cache file; the main engine's edge is
deeper sport-specific modeling. **Personal research. Not financial advice.**
