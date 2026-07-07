"""Post-hoc probability calibration — apply side.

The model's raw win/over/cover probabilities are systematically miscalibrated
(see docs/MODEL_REPAIR.md): high favorite-hit-rate but the *bets* land on the
wrong side, because the model's probabilities are compressed relative to the
market. This maps a raw model probability to a calibrated one using an isotonic
fit produced offline by ``scripts/fit_calibration.py``.

Runtime is dependency-light: the fit is stored as (x_knots, y_knots) and applied
with ``numpy.interp`` (which clips to the fitted range at the tails). No fit
happens here.

Gated by ``config.APPLY_CALIBRATION`` (default off) so wiring it in is inert
until the maps are validated and the flag is flipped.
"""
from __future__ import annotations

import functools
import json

import numpy as np

from .config import REPO_ROOT

_PATH = REPO_ROOT / "data" / "history" / "calibration" / "model_calibration.json"


@functools.lru_cache(maxsize=1)
def _maps() -> dict:
    try:
        return json.loads(_PATH.read_text()).get("markets", {})
    except Exception:
        return {}


def _key(sport: str, market: str) -> str:
    return f"{str(sport).lower()}_{str(market).lower()}"


def has_map(sport: str, market: str) -> bool:
    m = _maps().get(_key(sport, market))
    return bool(m and m.get("x_knots"))


def calibrate(p, sport: str, market: str):
    """Map a raw model probability to its calibrated value for (sport, market).

    Returns ``p`` unchanged when it isn't a usable probability or no fitted map
    exists — so an unmapped market (or a missing file) is a safe no-op identity.
    """
    try:
        p = float(p)
    except (TypeError, ValueError):
        return p
    if not (0.0 <= p <= 1.0):
        return p
    m = _maps().get(_key(sport, market))
    if not m or not m.get("x_knots"):
        return p
    return float(np.interp(p, m["x_knots"], m["y_knots"]))
