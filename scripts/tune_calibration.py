"""Per-sport calibration & MARKET_SHRINK drift monitor.

Re-runs the walk-forward backtest for every configured sport, measures
calibration (Brier, totals bias/MAE) from the full recent game history, sweeps
MARKET_SHRINK against the matched closing lines, and recommends a shrink only
when the matched-bet sample is large enough to trust. Writes a dated markdown +
json report to reports/ and prints a summary. Designed to run unattended (weekly
CI): it never raises on a thin/empty sport, it just reports "hold".

    python scripts/tune_calibration.py                # last 4 seasons/sport
    python scripts/tune_calibration.py --seasons 5 --draws 1500
    python scripts/tune_calibration.py --sports MLB,WNBA

The recommendation maximizes total units won (ML+total+spread) across the
matched closing lines, subject to a minimum bet count (MIN_BETS). Below that it
holds the configured value — closing-line odds only exist for the current season
so out-of-season sports simply accumulate sample until they cross the bar.
"""

import argparse
import json
import statistics as st
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547 import backtest  # noqa: E402
from project547.config import REPO_ROOT  # noqa: E402
from project547.sports import SPORTS  # noqa: E402

SHRINK_GRID = [0.3, 0.5, 0.65, 0.8]
MIN_BETS = 150          # matched bets required before recommending a change
DRIFT_EPS = 0.10        # |best - current| this far apart flags drift
BACKFILL = REPO_ROOT / "data" / "history" / "backfill"


def _available_seasons(sport_key: str, n: int) -> list[int]:
    d = BACKFILL / sport_key.lower()
    if not d.exists():
        return []
    yrs = sorted(int(p.name) for p in d.iterdir() if p.name.isdigit())
    return yrs[-n:] if yrs else []


def _units(summary: dict) -> float:
    return float(summary.get("units") or 0.0)


def _bets(summary: dict) -> int:
    return int(summary.get("bets") or 0)


def tune_sport(sport_key: str, seasons: list[int], draws: int) -> dict | None:
    """Sweep shrink for one sport. Returns a result dict, or None if the sport
    produced no graded games (no data)."""
    sport = SPORTS[sport_key]
    base = None
    sweep = []
    for s in SHRINK_GRID:
        r = backtest.run_game_backtest(
            sport_key, seasons, draws=draws, shrink=s,
            use_starters=(sport_key == "MLB"),
            use_bullpen=(sport_key == "MLB"),
            use_park=(sport_key == "MLB"),
            detail=(base is None))
        if r["n_games_graded"] == 0:
            return None
        cl = r["closing_line"]
        ml, tot, spr = cl["moneyline_bets"], cl["total_bets"], cl["spread_bets"]
        total_units = _units(ml) + _units(tot) + _units(spr)
        total_bets = _bets(ml) + _bets(tot) + _bets(spr)
        sweep.append({"shrink": s, "matched": cl["games_matched"],
                      "total_units": round(total_units, 2), "total_bets": total_bets,
                      "ml": ml, "total": tot, "spread": spr,
                      "avg_clv": cl.get("avg_clv_vs_fair")})
        if base is None:                       # first pass carries detail + accuracy
            diffs = [g["proj_total"] - (g["home_score"] + g["away_score"])
                     for g in r.get("games", []) if g.get("proj_total") is not None]
            base = {
                "n_games": r["n_games_graded"],
                "brier": r["moneyline"]["brier"],
                "favorite_hit_rate": r["moneyline"]["favorite_hit_rate"],
                "total_mae": r["total"]["mae"],
                "total_bias": round(st.mean(diffs), 3) if diffs else None,
            }

    current = sport.market_shrink
    # candidates with enough matched volume to trust
    usable = [w for w in sweep if w["total_bets"] >= MIN_BETS]
    if usable:
        best = max(usable, key=lambda w: w["total_units"])
        cur_row = min(sweep, key=lambda w: abs(w["shrink"] - current))
        drift = abs(best["shrink"] - current) >= DRIFT_EPS
        rec = (f"move {current} -> {best['shrink']}" if drift else f"hold {current}")
    else:
        best, cur_row, drift, rec = None, None, False, f"hold {current} (sample<{MIN_BETS})"

    return {"sport": sport_key, "seasons": seasons, "current_shrink": current,
            "recommended_shrink": best["shrink"] if best else current,
            "drift": drift, "recommendation": rec, "matched_bets": max(
                (w["total_bets"] for w in sweep), default=0),
            **base, "sweep": sweep,
            "best": best, "current_row": cur_row}


def _md(results: list[dict]) -> str:
    today = date.today().isoformat()
    out = [f"# Calibration & MARKET_SHRINK drift — {today}", "",
           "Walk-forward backtest per sport. Calibration metrics use the full "
           "recent game history; the shrink recommendation maximizes total units "
           f"(ML+total+spread) on the matched closing lines and only fires when "
           f"≥ {MIN_BETS} matched bets exist (closing-line odds are current-season "
           "only, so out-of-season sports accumulate sample until they qualify).",
           "",
           "| Sport | Szns | Games | Brier | favhit | tot_bias | tot_MAE | "
           "cur→rec shrink | matched | recommendation |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    alerts = []
    for r in results:
        szn = f"{r['seasons'][0]}–{r['seasons'][-1]}" if r["seasons"] else "—"
        rec_sh = (f"{r['current_shrink']}→{r['recommended_shrink']}"
                  if r["drift"] else f"{r['current_shrink']}")
        out.append(
            f"| {r['sport']} | {szn} | {r['n_games']} | {r['brier']} | "
            f"{r['favorite_hit_rate']} | {r['total_bias']} | {r['total_mae']} | "
            f"{rec_sh} | {r['matched_bets']} | {r['recommendation']} |")
        if r["drift"]:
            b, c = r["best"], r["current_row"]
            alerts.append(
                f"- **{r['sport']}**: shrink {r['current_shrink']} → "
                f"**{r['recommended_shrink']}** — total units "
                f"{c['total_units']:+} → {b['total_units']:+} over {b['total_bets']} "
                f"bets (ML {c['ml']['roi_pct']}%→{b['ml']['roi_pct']}%, "
                f"tot {c['total']['roi_pct']}%→{b['total']['roi_pct']}%).")
    out += ["", "## Drift alerts", ""]
    out += alerts or ["_No sport crossed the change threshold this run._"]
    out += ["", "## Per-sport shrink sweeps", ""]
    for r in results:
        out.append(f"### {r['sport']} (n={r['n_games']}, matched={r['matched_bets']})")
        out.append("| shrink | total units | bets | ML roi | tot roi | spread roi |")
        out.append("|---|---|---|---|---|---|")
        for w in r["sweep"]:
            out.append(f"| {w['shrink']} | {w['total_units']:+} | {w['total_bets']} "
                       f"| {w['ml']['roi_pct']}% | {w['total']['roi_pct']}% "
                       f"| {w['spread']['roi_pct']}% |")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", default=",".join(SPORTS))
    ap.add_argument("--seasons", type=int, default=4, help="recent seasons/sport")
    ap.add_argument("--draws", type=int, default=1000)
    args = ap.parse_args()

    results = []
    for sk in [s.strip() for s in args.sports.split(",") if s.strip()]:
        if sk not in SPORTS:
            print(f"skip unknown sport {sk}")
            continue
        seasons = _available_seasons(sk, args.seasons)
        if not seasons:
            print(f"{sk}: no backfill seasons — skipping")
            continue
        print(f"{sk}: backtesting {seasons} (shrink sweep {SHRINK_GRID})…")
        res = tune_sport(sk, seasons, args.draws)
        if res is None:
            print(f"  {sk}: no graded games")
            continue
        results.append(res)
        print(f"  Brier {res['brier']} tot_bias {res['total_bias']} "
              f"matched {res['matched_bets']} -> {res['recommendation']}")

    if not results:
        print("no results")
        return
    today = date.today().isoformat()
    reports = REPO_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"calibration_{today}.json").write_text(
        json.dumps(results, indent=1, default=str))
    (reports / f"calibration_{today}.md").write_text(_md(results))
    print(f"\nWrote reports/calibration_{today}.md")
    drifts = [r["sport"] for r in results if r["drift"]]
    print(f"DRIFT: {', '.join(drifts) if drifts else 'none'}")


if __name__ == "__main__":
    main()
