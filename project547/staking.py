"""Slate-level stake shaping — correlation haircut + total-exposure cap.

Independent quarter-Kelly (odds.kelly_stake) sizes each bet as if it were the
only one on the board. Two things break that assumption on a real slate:

  1. **Same-game correlation.** A team ML + that team's spread, a game total +
     a scorer's over, a favorite ML + the under — legs on one game move
     together. Betting each at full independent Kelly over-levers the joint
     position (you can lose them all at once), so the aggregate risk is larger
     than any single Kelly intended.
  2. **Slate pile-up.** Many small independent edges still add up; a heavy night
     can put an outsized fraction of bankroll at risk in total.

``adjust_stakes`` applies two corrections, **both identity at their default
values** so nothing changes until the owner opts in (config.SLATE_CORR = 0.0,
config.SLATE_MAX_EXPOSURE = None):

  1. Correlation haircut: within each group of ``k`` co-moving legs, scale every
     stake by ``1 / (1 + (k-1)*rho)`` — the equicorrelation Kelly shrink that
     keeps the group's aggregate risk near a single-leg Kelly. ``rho = 0`` -> 1.
     Grouping is by game, and we assume *positive* within-game correlation: a
     conservative simplification (it only ever reduces stakes; per-pair sign from
     sgp.CORRELATION_PRESETS is a future refinement).
  2. Exposure cap: if the slate's total suggested stake exceeds ``max_exposure``
     (a fraction of bankroll), scale every stake down proportionally so the total
     lands exactly at the cap. ``None`` -> no cap.
"""

from __future__ import annotations

from collections import Counter

from . import config


def haircut_factor(k: int, rho: float) -> float:
    """Equicorrelation Kelly shrink for one of ``k`` co-moving legs at common
    pairwise correlation ``rho``. 1.0 for a lone leg or ``rho <= 0``."""
    if k <= 1 or rho <= 0:
        return 1.0
    return 1.0 / (1.0 + (k - 1) * rho)


def adjust_stakes(groups: list, stakes: list,
                  corr: float | None = None,
                  max_exposure: float | None = None) -> list:
    """Return correlation-/exposure-adjusted copies of ``stakes``.

    ``groups[i]`` is the correlation-group key of bet ``i`` (e.g. its game);
    legs sharing a key are treated as positively correlated. ``stakes[i]`` is
    its independent Kelly fraction. Non-positive/None stakes pass through
    untouched and don't count toward group size or total exposure. Defaults
    (``corr`` from config.SLATE_CORR, ``max_exposure`` from
    config.SLATE_MAX_EXPOSURE) are identity when unset.
    """
    corr = config.SLATE_CORR if corr is None else corr
    if max_exposure is None:
        max_exposure = config.SLATE_MAX_EXPOSURE

    def _pos(s):
        return isinstance(s, (int, float)) and s == s and s > 0   # not None/NaN/<=0

    sizes = Counter(g for g, s in zip(groups, stakes) if _pos(s))

    out = []
    for g, s in zip(groups, stakes):
        if not _pos(s):
            out.append(s)
        elif corr and corr > 0:
            out.append(s * haircut_factor(sizes[g], corr))
        else:
            out.append(s)

    if max_exposure and max_exposure > 0:
        total = sum(x for x in out if _pos(x))
        if total > max_exposure:
            scale = max_exposure / total
            out = [x * scale if _pos(x) else x for x in out]

    return out
