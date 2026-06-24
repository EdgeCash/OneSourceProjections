"""Baseline projections for sports we don't model yet — and the hook to add our
own math on top, so we can start tracking a record from day one.

Two ideas the research already endorsed (docs/research/):
  1. The **de-vigged market line is the single best baseline projection.** We
     already surface odds on every sheet, so we can turn them into a baseline
     win probability for *any* sport today — no new model required.
  2. **BettingPros** is sport-parameterized and its premium fields include
     projections / EV / consensus, so for sports BP tracks we can pull a
     stronger baseline than a single book's line. ``bp_sport_for`` maps our
     league groups to BP's sport codes; the existing ``clients/bettingpros``
     does the fetching.

The flow we're building toward: **market/BP baseline → + our adjustment →
tracked** (logged like any pick so its CLV/accuracy is measured). ``apply_edge``
is where "a little of our own math" plugs in; until we have a model for a sport
the adjustment is 0 and we simply track the baseline itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from .calculators import no_vig

# Our league groups (clients/espn_extra.LEAGUES) -> BettingPros sport codes,
# for the sports BP is known to track. None = no BP coverage (market baseline
# only). Verified live on connect; unknowns degrade gracefully.
BP_SPORTS: dict[str, str | None] = {
    "Soccer": "SOCCER", "Tennis": "TENNIS", "Golf": "GOLF", "MMA": "UFC",
    "Motorsport": "NASCAR", "Football": "CFL", "Baseball": "MLB",
    "Basketball": "NCAAB", "Hockey": "NHL", "Aussie Rules": None,
    "Lacrosse": None,
}


def bp_sport_for(group: str) -> str | None:
    return BP_SPORTS.get(group)


@dataclass(frozen=True)
class Baseline:
    source: str                  # "market" | "bettingpros" | "blend"
    home_wp: float
    away_wp: float
    draw_wp: float | None = None  # set for 3-way markets (soccer)
    note: str = ""

    def as_pct(self) -> dict[str, str]:
        out = {"Home": f"{self.home_wp*100:.0f}%", "Away": f"{self.away_wp*100:.0f}%"}
        if self.draw_wp is not None:
            out = {"Home": out["Home"], "Draw": f"{self.draw_wp*100:.0f}%",
                   "Away": out["Away"]}
        return out


def market_baseline(home_ml, away_ml, draw_ml=None, *,
                    source: str = "market") -> Baseline | None:
    """De-vig American moneylines into a baseline win probability.

    Two-way (most sports) or three-way (soccer, when a draw price is present).
    Returns None when the moneylines aren't available."""
    if home_ml in (None, "") or away_ml in (None, ""):
        return None
    try:
        if draw_ml not in (None, ""):
            ph, pa, pd = no_vig(float(home_ml), float(away_ml), float(draw_ml))
            return Baseline(source, round(ph, 4), round(pa, 4), round(pd, 4),
                            "De-vigged 3-way market (home/away/draw).")
        ph, pa = no_vig(float(home_ml), float(away_ml))
        return Baseline(source, round(ph, 4), round(pa, 4), None,
                        "De-vigged 2-way market line.")
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def apply_edge(base: Baseline, home_delta: float) -> Baseline:
    """Our adjustment on top of the baseline: nudge the home win prob by
    ``home_delta`` (probability points, e.g. +0.03), take it from the away side,
    keep any draw fixed, clamp and renormalize. ``home_delta=0`` is a no-op, so
    we can ship tracking before we ship a model for a sport."""
    if not home_delta:
        return base
    h = min(max(base.home_wp + home_delta, 0.01), 0.99)
    if base.draw_wp is not None:
        rest = max(1.0 - h - base.draw_wp, 0.01)
        a = rest
        total = h + base.draw_wp + a
        return Baseline("blend", round(h / total, 4), round(a / total, 4),
                        round(base.draw_wp / total, 4),
                        f"Baseline {base.source} + our adjustment {home_delta:+.0%}.")
    a = 1.0 - h
    return Baseline("blend", round(h, 4), round(a, 4), None,
                    f"Baseline {base.source} + our adjustment {home_delta:+.0%}.")
