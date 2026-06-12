"""Run game backtests (MLB, WNBA) and a WNBA prop-distribution check,
print a summary, and write a markdown report to reports/.

Usage:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --mlb-seasons 2023,2024,2025,2026 --quick
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import backtest  # noqa: E402
from onesource.config import REPO_ROOT  # noqa: E402


def _fmt_clv(c: dict) -> str:
    ml, tot = c["moneyline_bets"], c["total_bets"]
    return (f"games matched to closing lines: {c['games_matched']}, "
            f"avg ML CLV vs fair: {c['avg_clv_vs_fair']}\n"
            f"  - moneyline bets: {ml['bets']}, win {ml['win_rate']}, "
            f"{ml['units']}u, ROI {ml['roi_pct']}%\n"
            f"  - total bets: {tot['bets']}, win {tot['win_rate']}, "
            f"{tot['units']}u, ROI {tot['roi_pct']}%")


def _md_game(r: dict) -> str:
    lines = [f"### {r['sport']} — seasons {', '.join(map(str, r['seasons']))}",
             "",
             f"- Games graded (walk-forward, ≥ warmup): **{r['n_games_graded']}**",
             f"- Moneyline: Brier **{r['moneyline']['brier']}**, log-loss "
             f"{r['moneyline']['log_loss']}, favorite hit-rate "
             f"**{r['moneyline']['favorite_hit_rate']}**",
             f"- Total: MAE **{r['total']['mae']}**, RMSE {r['total']['rmse']}",
             ""]
    if r["calibration"]:
        lines += ["| Predicted home-win | Games | Empirical |", "|---|---|---|"]
        for c in r["calibration"]:
            lines.append(f"| {c['predicted']:.1f} | {c['n']} | {c['empirical']:.3f} |")
        lines.append("")
    cl = r["closing_line"]
    lines += ["**Vs. closing lines** (bets graded at closing prices — "
              "profit here = beating the close):", "",
              f"- Games matched: {cl['games_matched']}; avg moneyline CLV vs "
              f"fair prob: {cl['avg_clv_vs_fair']}",
              f"- Moneyline bets: {cl['moneyline_bets']['bets']}, win-rate "
              f"{cl['moneyline_bets']['win_rate']}, {cl['moneyline_bets']['units']}u, "
              f"**ROI {cl['moneyline_bets']['roi_pct']}%**",
              f"- Total bets: {cl['total_bets']['bets']}, win-rate "
              f"{cl['total_bets']['win_rate']}, {cl['total_bets']['units']}u, "
              f"**ROI {cl['total_bets']['roi_pct']}%**", ""]
    return "\n".join(lines)


def _md_props(p: dict) -> str:
    out = ["### WNBA props — distribution calibration", "",
           "Trailing-average projection through the production distribution "
           "layer, graded vs actuals. Tests distribution shape, not edge.", ""]
    for stat, d in p.items():
        out.append(f"**{stat}** — n={d['n']}, projection MAE {d['projection_mae']}")
        if d["calibration"]:
            out += ["", "| Predicted P(over) | n | Empirical |", "|---|---|---|"]
            for c in d["calibration"]:
                out.append(f"| {c['predicted']:.1f} | {c['n']} | {c['empirical']:.3f} |")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mlb-seasons", default="2022,2023,2024,2025,2026")
    ap.add_argument("--wnba-seasons", default="2021,2022,2023,2024,2025,2026")
    ap.add_argument("--wnba-prop-seasons", default="2023,2024,2025")
    ap.add_argument("--draws", type=int, default=4000)
    ap.add_argument("--quick", action="store_true", help="fewer MLB draws/seasons")
    args = ap.parse_args()

    mlb_seasons = [int(s) for s in args.mlb_seasons.split(",")]
    wnba_seasons = [int(s) for s in args.wnba_seasons.split(",")]
    prop_seasons = [int(s) for s in args.wnba_prop_seasons.split(",")]
    draws = 1500 if args.quick else args.draws

    print("Running MLB game backtest...")
    mlb = backtest.run_game_backtest("MLB", mlb_seasons, draws=draws)
    print(f"  graded {mlb['n_games_graded']} games; "
          f"Brier {mlb['moneyline']['brier']}, total MAE {mlb['total']['mae']}")
    print("  " + _fmt_clv(mlb["closing_line"]))

    print("Running WNBA game backtest...")
    wnba = backtest.run_game_backtest("WNBA", wnba_seasons, draws=draws)
    print(f"  graded {wnba['n_games_graded']} games; "
          f"Brier {wnba['moneyline']['brier']}, total MAE {wnba['total']['mae']}")
    print("  " + _fmt_clv(wnba["closing_line"]))

    print("Running WNBA prop calibration...")
    props = backtest.run_wnba_prop_calibration(prop_seasons)
    for stat, d in props.items():
        print(f"  {stat}: n={d['n']}, MAE {d['projection_mae']}")

    today = date.today().isoformat()
    reports = REPO_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"backtest_{today}.json").write_text(
        json.dumps({"mlb": mlb, "wnba": wnba, "wnba_props": props}, indent=1))
    md = "\n".join([
        f"# Backtest report — {today}", "",
        "Walk-forward, no lookahead. MLB uses the production Monte-Carlo "
        "model **without probable-starter xFIP** (not in the historical "
        "import), so MLB game numbers are a team-form floor. WNBA uses the "
        "exact production model. Betting ROI is measured at **closing "
        "prices** — the conservative bar.", "",
        _md_game(mlb), _md_game(wnba), _md_props(props),
    ])
    (reports / f"backtest_{today}.md").write_text(md)
    print(f"\nWrote reports/backtest_{today}.md and .json")


if __name__ == "__main__":
    main()
