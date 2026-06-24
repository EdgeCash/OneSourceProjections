"""Build Your Own Edge (BYOE) — a custom, user-built projection algorithm.

Ported and generalized from Edge Equation's `edge_scoring.py`
(edge-equation-v1, commit 599e6c8). The original was MLB-only (runs / Poisson);
this version is sport-agnostic and reuses OneSource's Normal game model so it
works for the NFL/NCAAF launch target as well as the existing sports.

The idea: the user weights a handful of team stats into a custom **edge**.
Every stat is restated as a league z-score, the weights blend them into one
**composite** per team (a weighted *average*, so the scale stays sane for two
stats or twelve), and the composite drives a pick:

  * model "weighted" — a transparent ranker: pick the stronger composite
    (moneyline / spread) or the above-average scoring environment (total). It
    states a side, not a probability.
  * model "normal"   — map the composite gap to a point margin and feed it
    through the sport's Normal model (the same machinery as models/generic.py)
    to get win / cover / over probabilities, which Kelly staking needs.

Staking turns a pick into units: flat (1u), or (half-)Kelly from the model's
probability and the price. Pure functions, no I/O — the Streamlit page and the
historical-data adapter (teamstats/history) are thin layers on top. See
docs/port/byoe-feature.md for the full feature + UI plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, pstdev

from .sports import Sport

# A 1.0 gap in composite (≈ one league SD of blended team strength) maps to this
# fraction of the sport's margin SD in points. Heuristic + tunable: keeps a
# normal team gap landing a believable spread rather than a blowout.
COMPOSITE_TO_MARGIN_SD = 0.55
# Same idea for totals: a positive combined composite nudges the total this many
# total-SDs above the league-average total.
COMPOSITE_TO_TOTAL_SD = 0.40

MARKETS = ("moneyline", "spread", "total")
MODELS = ("weighted", "normal")
STAKES = ("flat", "kelly", "half_kelly")

KELLY_STAKE_SCALE = 100.0  # Kelly fraction -> units (1u == 1% of bankroll)


@dataclass(frozen=True)
class EdgeInput:
    stat_key: str
    weight: float


@dataclass(frozen=True)
class Edge:
    name: str
    inputs: tuple[EdgeInput, ...]
    market: str = "moneyline"     # moneyline | spread | total
    model: str = "weighted"       # weighted | normal
    stake: str = "flat"           # flat | kelly | half_kelly
    max_stake: float = 3.0        # per-bet unit cap

    def __post_init__(self):
        if self.market not in MARKETS:
            raise ValueError(f"market must be one of {MARKETS}")
        if self.model not in MODELS:
            raise ValueError(f"model must be one of {MODELS}")
        if self.stake not in STAKES:
            raise ValueError(f"stake must be one of {STAKES}")


# --- ratings ---------------------------------------------------------------

def zscore_index(team_stats: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Restate every stat as a league z-score (value - mean) / stdev. A stat
    with zero spread contributes nothing (z = 0)."""
    keys = {k for s in team_stats.values() for k in s}
    moments = {}
    for k in keys:
        vals = [s[k] for s in team_stats.values() if k in s]
        if vals:
            moments[k] = (mean(vals), pstdev(vals))
    out = {}
    for code, stats in team_stats.items():
        z = {}
        for k, v in stats.items():
            mu, sd = moments.get(k, (0.0, 0.0))
            z[k] = (v - mu) / sd if sd > 0 else 0.0
        out[code] = z
    return out


def composite(z_index: dict[str, dict[str, float]], code: str, edge: Edge) -> float:
    """One team's composite: the edge's weights applied to its z-scores,
    normalized by total absolute weight (a weighted average of z-scores)."""
    z = z_index.get(code, {})
    raw = sum(i.weight * z.get(i.stat_key, 0.0) for i in edge.inputs)
    scale = sum(abs(i.weight) for i in edge.inputs) or 1.0
    return raw / scale


# --- scoring ---------------------------------------------------------------

def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class Pick:
    pick: str                 # team code, or "OVER"/"UNDER"
    prob: float | None        # win/cover/over probability (normal model only)
    margin: float | None      # projected home margin (normal, market != total)


def score_game(edge: Edge, sport: Sport, z_index: dict[str, dict[str, float]],
               home: str, away: str, *, total_line: float | None = None) -> Pick | None:
    """Apply an edge to one game. None if either team is missing."""
    if home not in z_index or away not in z_index:
        return None
    h, a = composite(z_index, home, edge), composite(z_index, away, edge)
    normal = edge.model == "normal"

    if edge.market == "total":
        ref = sport.league_ppg * 2
        if normal:
            mean_total = ref + COMPOSITE_TO_TOTAL_SD * sport.sigma_total * (h + a)
            line = total_line if total_line is not None else ref
            p_over = _phi((mean_total - line) / sport.sigma_total)
            return Pick("OVER" if p_over >= 0.5 else "UNDER",
                        round(max(p_over, 1 - p_over), 4), None)
        return Pick("OVER" if (h + a) >= 0 else "UNDER", None, None)

    # moneyline / spread: composite gap -> home margin
    margin = COMPOSITE_TO_MARGIN_SD * sport.sigma_margin * (h - a) + sport.hfa
    if edge.market == "moneyline":
        pick = home if margin >= 0 else away
        if normal:
            p_home = _phi(margin / sport.sigma_margin)
            return Pick(pick, round(p_home if pick == home else 1 - p_home, 4),
                        round(margin, 2))
        return Pick(pick, None, round(margin, 2))
    # spread: pick the side the model favors against a pick'em by default
    pick = home if margin >= 0 else away
    if normal:
        # P(home covers 0) etc. — caller can re-evaluate vs a real spread.
        p_home_cover = _phi(margin / sport.sigma_margin)
        return Pick(pick, round(p_home_cover if pick == home else 1 - p_home_cover, 4),
                    round(margin, 2))
    return Pick(pick, None, round(margin, 2))


# --- staking ---------------------------------------------------------------

def _decimal(odds: float | int | None) -> float:
    if not isinstance(odds, (int, float)) or odds == 0:
        return 2.0
    return 1.0 + odds / 100.0 if odds > 0 else 1.0 + 100.0 / abs(odds)


def kelly_fraction(prob: float, odds: float | int | None) -> float:
    b = _decimal(odds) - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * prob - (1.0 - prob)) / b)


def stake_units(pick: Pick, odds: float | int | None, edge: Edge) -> float:
    """Units to stake. flat = 1u; kelly/half_kelly sized from prob+price (capped
    at edge.max_stake). With no probability (weighted model) Kelly falls back to
    flat — there's nothing to size from."""
    if edge.stake == "flat" or pick.prob is None:
        return min(1.0, edge.max_stake)
    f = kelly_fraction(pick.prob, odds)
    if edge.stake == "half_kelly":
        f *= 0.5
    return round(min(f * KELLY_STAKE_SCALE, edge.max_stake), 3)


# --- backtest --------------------------------------------------------------

@dataclass
class BacktestResult:
    n: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    units: float = 0.0
    staked: float = 0.0
    equity: list = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        decided = self.wins + self.losses
        return self.wins / decided if decided else 0.0

    @property
    def roi(self) -> float:
        return self.units / self.staked if self.staked else 0.0

    def summary(self) -> dict:
        return {"n": self.n, "record": f"{self.wins}-{self.losses}-{self.pushes}",
                "units": round(self.units, 2), "roi": round(self.roi, 4),
                "hit_rate": round(self.hit_rate, 4)}


def backtest(edge: Edge, sport: Sport, games: list[dict], z_index_fn) -> BacktestResult:
    """Walk-forward grade an edge over historical games.

    Each game dict: {date, home, away, home_score, away_score, and optionally
    home_ml/away_ml (moneyline odds), spread (home), total}. ``z_index_fn(date)``
    returns the league z-index built only from games *before* that date (the
    caller enforces point-in-time; in OSP this comes from teamstats/history)."""
    res = BacktestResult()
    bank = 0.0
    for g in games:
        z = z_index_fn(g["date"])
        pick = score_game(edge, sport, z, g["home"], g["away"], total_line=g.get("total"))
        if pick is None:
            continue
        hs, as_ = g.get("home_score"), g.get("away_score")
        if hs is None or as_ is None:
            continue
        odds = (g.get("home_ml") if pick.pick == g["home"]
                else g.get("away_ml") if pick.pick == g["away"] else -110)
        units = stake_units(pick, odds, edge)
        won = _graded(pick, g, hs, as_)
        res.n += 1
        res.staked += units
        if won is None:
            res.pushes += 1
        elif won:
            res.wins += 1
            res.units += units * (_decimal(odds) - 1.0)
        else:
            res.losses += 1
            res.units -= units
        bank = round(res.units, 3)
        res.equity.append(bank)
    return res


def _graded(pick: Pick, g: dict, hs: float, as_: float) -> bool | None:
    """True win / False loss / None push for the pick on a final score."""
    if pick.pick in ("OVER", "UNDER"):
        line = g.get("total")
        if line is None:
            return None
        tot = hs + as_
        if tot == line:
            return None
        return (tot > line) == (pick.pick == "OVER")
    margin = hs - as_  # home margin
    if pick.pick == g["home"]:
        side_margin = margin
    else:
        side_margin = -margin
    spread = g.get("spread")  # home spread; flip for away pick
    if spread is not None:
        handicap = spread if pick.pick == g["home"] else -spread
        adj = side_margin + handicap
        if adj == 0:
            return None
        return adj > 0
    if side_margin == 0:
        return None
    return side_margin > 0
