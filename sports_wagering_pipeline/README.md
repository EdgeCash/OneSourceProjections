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

# Reuse the main engine's data (default) — zero extra API calls
python -m src.app --mode both --sport MLB                 # --source shared

# Offline demo slate (also the only way to see the salary-cap DFS optimizer)
python -m src.app --mode both --sport MLB   --source sample
python -m src.app --mode dfs  --sport WNBA  --source sample --budget 50000

# PrizePicks slip only
python -m src.app --mode pickem --sport MLB --platform PrizePicks
```

Run **from the `sports_wagering_pipeline/` directory** so `python -m src.app`
resolves the package. Add the repo root to `PYTHONPATH` (`PYTHONPATH=..`) so the
shared source can import `project547`.

## Running alongside the main engine — no double API usage

Both models run on the **existing hourly schedule with one set of API calls**.
The `--source shared` path (default) does **not** call FantasyPros itself — it
calls the *same* `project547.clients.fantasypros` functions the main engine's
hourly pull already used, with the same arguments, so it lands on the same
1-hour disk cache as a **warm hit**.

The guarantee is structural, not best-effort: the FantasyPros client only reads
`FANTASYPROS_API_KEY` on a cache **miss**. The workflow step runs **with no API
secrets at all**, so it *cannot* issue a billable request — it can only consume
the cache. If the cache is cold (no key, offseason), it falls back to the sample
slate and logs `req_count=0`. It is wired into `.github/workflows/hourly.yml`
right after the pull, with `continue-on-error` so it can never break the main
pipeline.

To keep the shared cache key aligned, the pipeline resolves the slate date from
`data/output/latest.json` (`primary_date`) — the exact date the main engine just
pulled — falling back to today ET.

### What is real vs. sample in shared mode

| feature | shared source | note |
| --- | --- | --- |
| Pick'em **lines** (PrizePicks / Underdog) | **real** | pulled from the warm BettingPros cache via `bettingpros.prop_offer_lines` → `dfs_offer_lines`, filtered to the book id (37 / 36) |
| Pick'em **projections** (per stat) | **real** | each line is paired with the matching per-stat FantasyPros projection (Hits, Home Runs, Total Bases, Strikeouts, Outs, Hits/Earned-Runs/Walks Allowed; WNBA/NBA Points, Rebounds, Assists, 3PM) |
| Salary-cap DFS | **sample only** | FantasyPros projections carry no position or DK salary, and this repo ingests no DK salary feed, so `--source shared` DFS is empty by design — use `--source sample`, or add a salaries source to `player_projections` |

When the warm BettingPros cache has no lines for a book/sport (cold cache, or a
sport the main engine didn't pull DFS offers for), the Pick'em side falls back to
lines **derived** from the projection (`Fantasy Points` stat) so it still runs.
MLB is the fully-wired real-data sport (the hourly job pulls MLB prop offers);
other sports degrade to real projections + derived lines, then to the sample
slate, logging the source in `api_log` each step.

### Pick'em edge on a real line

For a real line like *Strikeouts O/U 6.5*, the engine takes the matching per-stat
projection — e.g. FantasyPros projects the pitcher at 7.8 K, modeled as
`Normal(7.8, √7.8)` — and reads the CDF at 6.5: `P(over) = 67.9%`, which clears
the 54.3% break-even, so it becomes an OVER play. A 0.5-HR line projected at 0.45
comes back 52.8% and is correctly dropped as non-viable. The per-stat spread
models (Poisson-ish counts, over-dispersed total bases, basketball scaling) live
in `_stat_std` in `api_client.py` and are documented heuristics — swap them for a
fitted spread when you have one. The real book odds (`over_odds` / `under_odds`)
are stored on each line for de-vig / EV work.

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
| `market_lines` | `line_id` PK, `master_player_id`, `stat_type`, `bookmaker`, `line_value`, `over_odds`, `under_odds`, `proj_mean`, `proj_std`, `last_updated` — `proj_mean`/`proj_std` hold the per-stat projection that pairs with this line's stat (NULL for derived lines, which fall back to the player's fantasy-point projection) |
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
