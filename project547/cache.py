"""Tiny disk cache with TTL so we don't hammer free APIs (and so the
dashboard stays fast). JSON for API payloads, parquet for dataframes."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .config import CACHE_DIR


def _path(key: str, ext: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return CACHE_DIR / f"{digest}.{ext}"


def _fresh(path: Path, ttl_seconds: int) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl_seconds


def cached_json(key: str, ttl_seconds: int, fetch: Callable[[], Any]) -> Any:
    path = _path(key, "json")
    if _fresh(path, ttl_seconds):
        return json.loads(path.read_text())
    data = fetch()
    path.write_text(json.dumps(data))
    return data


def cached_df(key: str, ttl_seconds: int, fetch: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    path = _path(key, "parquet")
    if _fresh(path, ttl_seconds):
        return pd.read_parquet(path)
    df = fetch()
    df.to_parquet(path)
    return df
