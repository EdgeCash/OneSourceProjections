"""Prove the NRFI model forward against the real market.

Joins the walk-forward (as-of, no-lookahead) model P(YRFI) to the historical
``run-in-first-inning`` BettingPros odds (open + close) and the actual
first-inning outcome, then reports — for the model AND for the market itself:

  - Brier / log-loss vs the de-vigged market fair probability (does the model
    add information over the price?),
  - betting record at the OPENING price when the model edge clears MIN_EDGE
    (ROI on results), and
  - CLV: how the de-vigged fair prob moved open->close on the side we took
    (the leading indicator of real edge — positive CLV means we beat the close).

This is the test the team-level NRFI backtest (scripts/backtest_nrfi.py) could
not run before 2026 outcomes were available: it answers whether surfacing NRFI
as a *bet* is justified, or whether it should stay informational.

    python scripts/backtest_nrfi_market.py [--season 2026] [--min-edge 0.03]
"""

import argparse
import gzip
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from project547 import config, odds, parks, teams, teamstats  # noqa: E402
from project547.config import REPO_ROOT  # noqa: E402
from project547.models import nrfi  # noqa: E402

H = REPO_ROOT / "data" / "history"


def load_market(season: int) -> dict:
    """{(date, frozenset(abbrs)): {yes_open, no_open, yes_close, no_close}}."""
    path = H / "bp_odds" / f"bp_first5_nrfi_{season}.jsonl.gz"
    out: dict = {}
    with gzip.open(path, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r.get("market_slug") != "run-in-first-inning":
                continue
            key = (r["date"], frozenset(r.get("abbrs", [])))
            slot = out.setdefault(key, {})
            lab = r.get("label")
            slot[f"{lab.lower()}_open"] = r.get("open_cost")
            slot[f"{lab.lower()}_close"] = r.get("close_consensus_cost")
    return out


def fair_yrfi(yes_american, no_american):
    """De-vig a Yes/No pair to the fair P(YRFI). None if either price missing."""
    if yes_american is None or no_american is None:
        return None
    py, _ = odds.devig_two_way(odds.implied_prob(yes_american),
                               odds.implied_prob(no_american))
    return py


def build(season: int):
    df = teamstats.team_games("MLB", (season - 1, season))
    market = load_market(season)
    # one row per game from the home rows, with actual YRFI
    home = df[df["is_home"] == True].copy()  # noqa: E712
    f1 = pd.to_numeric(home["f1"], errors="coerce")
    of1 = pd.to_numeric(home["opp_f1"], errors="coerce")
    home["yrfi"] = ((f1 > 0) | (of1 > 0)).astype(int)
    home = home.dropna(subset=["f1", "opp_f1"])
    home = home[home["season"] == season].sort_values("date")

    rate_cache: dict = {}
    rows = []
    for _, g in home.iterrows():
        date, h, a = g["date"], g["team"], g["opp"]
        key = (date, frozenset({teams.canon("MLB", h), teams.canon("MLB", a)}))
        mk = market.get(key)
        if not mk:
            continue
        if date not in rate_cache:
            rate_cache[date] = nrfi.team_first_inning_rates(df, asof=date)
        rates = rate_cache[date]
        rh, ra = rates.get(h), rates.get(a)
        if not rh or not ra:
            continue
        try:
            pf = parks.factor(h) or 1.0
        except Exception:
            pf = 1.0
        model_p = nrfi.yrfi_prob(off_away=ra["off"], allow_home=rh["allow"],
                                 off_home=rh["off"], allow_away=ra["allow"],
                                 park=pf)["p_yrfi"]
        fo = fair_yrfi(mk.get("yes_open"), mk.get("no_open"))
        fc = fair_yrfi(mk.get("yes_close"), mk.get("no_close"))
        if fo is None:
            continue
        rows.append({"date": date, "home": h, "away": a, "yrfi": int(g["yrfi"]),
                     "model_p": model_p, "fair_open": fo, "fair_close": fc,
                     "yes_open": mk.get("yes_open"), "no_open": mk.get("no_open")})
    return pd.DataFrame(rows)


def _brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def _logloss(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def report(df: pd.DataFrame, min_edge: float):
    y = df["yrfi"].to_numpy()
    print(f"games matched to market: {len(df)}   actual YRFI: {y.mean():.4f}")
    print("\n— Information (vs de-vigged market fair prob) —")
    print(f"  model  Brier {_brier(df['model_p'], y):.4f}   logloss {_logloss(df['model_p'], y):.4f}")
    print(f"  market Brier {_brier(df['fair_open'], y):.4f}   logloss {_logloss(df['fair_open'], y):.4f}")
    print(f"  (model better than the price only if its Brier is lower)")

    # betting at the open when the model edge clears min_edge
    bets, wins, pnl, clvs, clv_pos = 0, 0, 0.0, [], 0
    for _, r in df.iterrows():
        # EV of each side at the opening price using the model prob
        ev_yes = odds.expected_value(r["model_p"], r["yes_open"]) if r["yes_open"] else -1
        ev_no = odds.expected_value(1 - r["model_p"], r["no_open"]) if r["no_open"] else -1
        if max(ev_yes, ev_no) < min_edge:
            continue
        took_yes = ev_yes >= ev_no
        price = r["yes_open"] if took_yes else r["no_open"]
        won = (r["yrfi"] == 1) if took_yes else (r["yrfi"] == 0)
        bets += 1
        wins += int(won)
        pnl += (odds.american_to_decimal(price) - 1) if won else -1.0
        if r["fair_close"] is not None:
            # CLV: fair prob of the side we took, open -> close
            so = r["fair_open"] if took_yes else 1 - r["fair_open"]
            sc = r["fair_close"] if took_yes else 1 - r["fair_close"]
            clvs.append(sc - so)
            clv_pos += int(sc - so > 0)
    print("\n— Betting the model edge at the open (>= "
          f"{min_edge:.0%} EV) —")
    if bets:
        print(f"  bets {bets}   win {wins/bets:.3f}   units {pnl:+.2f}   "
              f"ROI {100*pnl/bets:+.2f}%")
        if clvs:
            print(f"  avg CLV {100*np.mean(clvs):+.2f}%   CLV-positive "
                  f"{clv_pos/len(clvs):.3f}  (n={len(clvs)})")
    else:
        print("  no bets cleared the edge threshold")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--min-edge", type=float, default=config.MIN_EDGE)
    args = ap.parse_args()
    df = build(args.season)
    if df.empty:
        print("no games matched the market — check odds/results presence")
        sys.exit(0)
    print(f"=== NRFI model vs market ({args.season}) ===")
    report(df, args.min_edge)
