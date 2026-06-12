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
