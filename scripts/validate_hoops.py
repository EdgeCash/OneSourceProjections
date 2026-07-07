"""Walk-forward A/B of the pace-and-efficiency engine vs the raw-PPG model (T2.1).

Basketball only. Rebuilds as-of team ratings from box scores (teamstats.team_games)
and, for each game once both sides have enough history, projects each side's
points two ways — raw recent PPG (what generic.py ships) and pace × efficiency
(models/hoops) — then grades totals MAE and moneyline Brier against the actual
result on identical games. WNBA has box data in-repo and is in season; NBA has no
box data here yet.

Usage: python scripts/validate_hoops.py [LEAGUE] [seasons...]
"""
import sys
from collections import defaultdict, deque

import numpy as np
from scipy import stats

from project547 import teamstats
from project547.models import hoops
from project547.sports import SPORTS

LEAGUE = sys.argv[1] if len(sys.argv) > 1 else "WNBA"
SEASONS = tuple(int(x) for x in sys.argv[2:]) or (2023, 2024, 2025)
WINDOW = 12
MIN_GAMES = 6
SPORT = SPORTS[LEAGUE]


def main():
    df = teamstats.team_games(LEAGUE, SEASONS)
    df = df[df["poss"].notna() & df["off_rtg"].notna() & df["opp_off_rtg"].notna()]
    lg_pace = float(df["pace"].mean())
    lg_rtg = float(df["off_rtg"].mean())
    lg_ppg = float(df["pts"].mean())
    sigma = SPORT.sigma_margin or 12.0
    hfa = SPORT.hfa

    # pair rows into games (home row + away row), keep date order
    games = []
    for gid, g in df.groupby("game_id"):
        h = g[g["is_home"]]
        a = g[~g["is_home"]]
        if len(h) == 1 and len(a) == 1:
            games.append((h.iloc[0]["date"], h.iloc[0], a.iloc[0]))
    games.sort(key=lambda x: x[0])

    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))  # team -> recent game dicts
    pe_tot, ppg_tot, pe_br, ppg_br, n = [], [], [], [], 0

    def rating(team):
        h = hist[team]
        if len(h) < MIN_GAMES:
            return None
        n_ = len(h)
        pace = hoops.shrink(np.mean([r["pace"] for r in h]), n_, lg_pace)
        off = hoops.shrink(np.mean([r["off"] for r in h]), n_, lg_rtg)
        deff = hoops.shrink(np.mean([r["deff"] for r in h]), n_, lg_rtg)
        ppg_off = hoops.shrink(np.mean([r["pts"] for r in h]), n_, lg_ppg)
        ppg_def = hoops.shrink(np.mean([r["opp_pts"] for r in h]), n_, lg_ppg)
        return hoops.HoopsRating(n_, pace, off, deff), ppg_off, ppg_def

    for date, h, a in games:
        rh, ra = rating(h["team"]), rating(a["team"])
        actual_total = h["pts"] + a["pts"]
        home_won = 1 if h["pts"] > a["pts"] else 0
        if rh and ra:
            # pace-efficiency
            hp, ap = hoops.project_points(rh[0], ra[0], lg_pace, lg_rtg, hfa)
            pe_tot.append(abs((hp + ap) - actual_total))
            pe_p = float(1 - stats.norm.cdf(0, hp - ap, sigma))
            pe_br.append((min(max(pe_p, 1e-6), 1 - 1e-6) - home_won) ** 2)
            # raw PPG midpoint (what generic ships for normal-model sports)
            hp2 = 0.5 * (rh[1] + ra[2]) + hfa / 2
            ap2 = 0.5 * (ra[1] + rh[2]) - hfa / 2
            ppg_tot.append(abs((hp2 + ap2) - actual_total))
            ppg_p = float(1 - stats.norm.cdf(0, hp2 - ap2, sigma))
            ppg_br.append((min(max(ppg_p, 1e-6), 1 - 1e-6) - home_won) ** 2)
            n += 1
        for row, opp in ((h, a), (a, h)):
            hist[row["team"]].append({
                "pace": row["pace"], "off": row["off_rtg"],
                "deff": row["opp_off_rtg"], "pts": row["pts"], "opp_pts": opp["pts"]})

    print(f"{LEAGUE} {SEASONS}  graded {n} games  "
          f"(league pace {lg_pace:.1f}, rtg {lg_rtg:.1f})")
    print(f"{'model':>14} {'totMAE':>8} {'MLBrier':>8}")
    print(f"{'raw PPG':>14} {np.mean(ppg_tot):>8.3f} {np.mean(ppg_br):>8.4f}")
    print(f"{'pace×eff':>14} {np.mean(pe_tot):>8.3f} {np.mean(pe_br):>8.4f}")


if __name__ == "__main__":
    main()
