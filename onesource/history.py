"""Loaders for the curated historical data in data/history/ (see its
README for provenance and coverage). All readers return DataFrames and
handle the .gz compression transparently."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd

from .config import REPO_ROOT

HISTORY_DIR = REPO_ROOT / "data" / "history"


def _jsonl(path: Path) -> pd.DataFrame:
    return pd.read_json(path, lines=True) if path.exists() else pd.DataFrame()


def closing_lines(sport: str, season: int = 2026) -> pd.DataFrame:
    """One row per event/market/side/book at close."""
    return _jsonl(HISTORY_DIR / "closing_lines" / sport.lower() / f"{season}.jsonl.gz")


def results(sport: str, season: int = 2026) -> pd.DataFrame:
    frames = [
        _jsonl(p) for p in sorted(
            (HISTORY_DIR / "results" / sport.lower()).glob("*.jsonl.gz"))
    ] if (HISTORY_DIR / "results" / sport.lower()).exists() else []
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty and "date" in df.columns and season:
        df = df[df["date"].astype(str).str.startswith(str(season))]
    return df


def backfill_games(sport: str, seasons: list[int] | None = None) -> pd.DataFrame:
    """Historical game results (+ line context where available)."""
    base = HISTORY_DIR / "backfill" / sport.lower()
    frames = []
    for ydir in sorted(base.iterdir()) if base.exists() else []:
        if not ydir.is_dir():
            continue
        if seasons and int(ydir.name) not in seasons:
            continue
        path = ydir / "games.json.gz"
        if path.exists():
            df = pd.read_json(path)
            df["season"] = int(ydir.name)
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def player_games(sport: str, seasons: list[int] | None = None) -> pd.DataFrame:
    """Player box-score lines (MLB 2021+, WNBA 2018+, NBA/NFL recent)."""
    base = HISTORY_DIR / "backfill" / sport.lower()
    frames = []
    for ydir in sorted(base.iterdir()) if base.exists() else []:
        if not ydir.is_dir():
            continue
        if seasons and int(ydir.name) not in seasons:
            continue
        path = ydir / "player_games.jsonl.gz"
        if path.exists():
            frames.append(_jsonl(path))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def statcast_xstats(season: int) -> dict:
    """Per-player Statcast expected stats for an MLB season:
    {'batting': {mlbam_id: {...}}, 'pitching': {...}}."""
    path = HISTORY_DIR / "backfill" / "mlb" / str(season) / "statcast_xstats.json.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as f:
        return json.load(f)


def wnba_elo() -> pd.DataFrame:
    path = HISTORY_DIR / "elo" / "wnba_elo_pregame.json.gz"
    if not path.exists():
        return pd.DataFrame()
    with gzip.open(path, "rt") as f:
        data = json.load(f)
    return pd.DataFrame.from_dict(data, orient="index").rename_axis("game_id")


def legacy_backtest(name: str) -> pd.DataFrame:
    """Graded history from prior models. name e.g. 'props_detail',
    'games_detail', 'history_nba_multi'."""
    base = HISTORY_DIR / "backtest" / "legacy"
    for ext in (".csv.gz", ".jsonl.gz"):
        path = base / f"{name}{ext}"
        if path.exists():
            return pd.read_csv(path) if ext == ".csv.gz" else _jsonl(path)
    return pd.DataFrame()


def legacy_calibration(kind: str) -> dict:
    """Fitted calibration params from the prior model ('game' or 'props')."""
    path = HISTORY_DIR / "calibration" / f"{kind}_calibration.json"
    return json.loads(path.read_text()) if path.exists() else {}


def graded_props_2026() -> pd.DataFrame:
    """Graded 2026 props with odds and over/under results."""
    path = HISTORY_DIR / "misc" / "Sports_2026_YTD_Historical_Props.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


# ---------------------------------------------------------------------------
# BettingPros captured odds history (open + close) — from profit-hunt.
# Live BettingPros can't be re-pulled historically, so this is the only
# source of opening prices for past games. Same source as the live pipeline.
# ---------------------------------------------------------------------------

def bp_game_odds(season: int = 2026) -> pd.DataFrame:
    """BettingPros game odds with opening and closing prices per
    event/market/side: moneyline, run-line, total, team-total.
    Columns include open_cost, close_cost, close_best, close_median,
    n_books, projection, cover_proba. 2026-03-25 onward."""
    return _jsonl(HISTORY_DIR / "bp_odds" / f"bp_game_odds_{season}.jsonl.gz")


def bp_first_five_odds(season: int = 2026) -> pd.DataFrame:
    """BettingPros first-inning / first-five derived markets with open and
    close (NRFI, F5 run-line/total, first-inning total)."""
    return _jsonl(HISTORY_DIR / "bp_odds" / f"bp_first5_nrfi_{season}.jsonl.gz")


def closing_consensus_lines(season: int = 2026) -> pd.DataFrame:
    """Per-game consensus open/close fair probabilities (moneyline)."""
    return _jsonl(HISTORY_DIR / "bp_odds" / f"closing_consensus_{season}.jsonl.gz")


def mlb_starters(season: int = 2026) -> dict:
    """2026 game->starter map and per-pitcher stats (MLBAM ids):
    {'games': [{date, away, home, away_sp_id, home_sp_id}, ...],
     'pitchers': {mlbam_id: {pit_era, pit_whip, pit_k9, ...}}}.
    Complements the Retrosheet visitor_sp_id/home_sp_id in
    backfill/mlb/<year>/game_context.jsonl for 2016-2025."""
    path = HISTORY_DIR / "backfill" / "mlb" / str(season) / "starters.json.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as f:
        return json.load(f)


def pick_ledger(kind: str = "picks") -> pd.DataFrame:
    """Realized pick history. kind: 'picks' or 'tenths_totals'."""
    name = "picks_ledger_2026" if kind == "picks" else "tenths_totals_ledger_2026"
    return _jsonl(HISTORY_DIR / "track" / f"{name}.jsonl.gz")
