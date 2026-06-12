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


def _md_game(r: dict, label: str | None = None) -> str:
    title = label or f"{r['sport']} — seasons {', '.join(map(str, r['seasons']))}"
    starter = ""
    if r.get("use_starters"):
        starter = f" · starters on {r.get('games_with_starter')}/{r['n_games_graded']}"
    lines = [f"### {title}", "",
             f"- Games graded (walk-forward, ≥ warmup): **{r['n_games_graded']}**{starter}",
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


def _md_clv(c: dict) -> str:
    ml, tot = c["moneyline"], c["total"]
    return "\n".join([
        "### MLB — true CLV at BettingPros open→close (2026)", "",
        "Model edges bet at the **opening** price, graded on results for "
        "ROI, with CLV = how the de-vigged fair probability moved open→"
        "close on the side taken. Positive CLV is the leading indicator of "
        f"real edge. Starters: {c['use_starters']}. Games matched: "
        f"{c['games_matched']}.", "",
        f"- Moneyline: {ml['bets']} bets, win {ml['win_rate']}, ROI "
        f"**{ml['roi_pct']}%**, avg CLV **{ml['avg_clv']}**, CLV-positive "
        f"rate **{ml['clv_positive_rate']}**",
        f"- Totals: {tot['bets']} bets, win {tot['win_rate']}, ROI "
        f"**{tot['roi_pct']}%**, avg CLV **{tot['avg_clv']}**, CLV-positive "
        f"rate **{tot['clv_positive_rate']}**", ""])


def _md_mlb_props(p: dict) -> str:
    out = ["### MLB props — calibration (walk-forward, 2024+)", "",
           "Production prop models on as-of-date player rates, graded vs "
           "box-score outcomes. `calibration_gap` = mean predicted P(over) − "
           "empirical over-rate (≈0 is unbiased; +ve leans over).", "",
           "| Market | n | proj MAE | mean P(over) | empirical | gap |",
           "|---|---|---|---|---|---|"]
    for mkt, d in p.items():
        if not d.get("n"):
            continue
        out.append(f"| {mkt} | {d['n']} | {d.get('projection_mae')} | "
                   f"{d.get('mean_pred_over')} | {d.get('empirical_over')} | "
                   f"{d.get('calibration_gap')} |")
    out.append("")
    return "\n".join(out)


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
    # starter box-logs only exist 2024+; use that window for the comparison
    starter_seasons = [s for s in mlb_seasons if s >= 2024] or [2024, 2025, 2026]
    draws = 1500 if args.quick else args.draws

    print("Running MLB game backtest (team-form only)...")
    mlb = backtest.run_game_backtest("MLB", mlb_seasons, draws=draws)
    print(f"  graded {mlb['n_games_graded']} games; "
          f"Brier {mlb['moneyline']['brier']}, total MAE {mlb['total']['mae']}")

    print("Running MLB game backtest (full pitching+park, 2024+)...")
    mlb_tf = backtest.run_game_backtest("MLB", starter_seasons, draws=draws,
                                        use_starters=False)
    mlb_sp = backtest.run_game_backtest("MLB", starter_seasons, draws=draws,
                                        use_starters=True, use_bullpen=True,
                                        use_park=True)
    print(f"  team-form: Brier {mlb_tf['moneyline']['brier']}, "
          f"MAE {mlb_tf['total']['mae']}; full: "
          f"Brier {mlb_sp['moneyline']['brier']}, MAE {mlb_sp['total']['mae']}")

    print("Running MLB true CLV (BettingPros open→close)...")
    clv = backtest.run_mlb_clv_open_close(starter_seasons, draws=draws,
                                          use_starters=True)
    print(f"  matched {clv['games_matched']}; ML ROI {clv['moneyline']['roi_pct']}%, "
          f"avg CLV {clv['moneyline']['avg_clv']}, "
          f"+CLV rate {clv['moneyline']['clv_positive_rate']}")

    print("Running WNBA game backtest...")
    wnba = backtest.run_game_backtest("WNBA", wnba_seasons, draws=draws)
    print(f"  graded {wnba['n_games_graded']} games; "
          f"Brier {wnba['moneyline']['brier']}, total MAE {wnba['total']['mae']}")
    print("  " + _fmt_clv(wnba["closing_line"]))

    print("Running MLB prop calibration...")
    mlb_props_cal = backtest.run_mlb_prop_calibration(starter_seasons)
    for mkt, d in mlb_props_cal.items():
        if d.get("n"):
            print(f"  {mkt}: n={d['n']}, MAE {d['projection_mae']}, "
                  f"gap {d['calibration_gap']}")

    print("Running WNBA prop calibration...")
    props = backtest.run_wnba_prop_calibration(prop_seasons)
    for stat, d in props.items():
        print(f"  {stat}: n={d['n']}, MAE {d['projection_mae']}")

    today = date.today().isoformat()
    reports = REPO_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"backtest_{today}.json").write_text(json.dumps(
        {"mlb": mlb, "mlb_team_form": mlb_tf, "mlb_starters": mlb_sp,
         "mlb_clv": clv, "mlb_props": mlb_props_cal,
         "wnba": wnba, "wnba_props": props}, indent=1))
    md = "\n".join([
        f"# Backtest report — {today}", "",
        "Walk-forward, no lookahead. WNBA uses the exact production model. "
        "MLB now runs both team-form-only and the **starter-aware** "
        "production model (as-of-date FIP from prior starts, 2024+ where "
        "box logs exist). CLV is measured at BettingPros **opening** prices "
        "with the open→close fair-probability move — the leading indicator "
        "of edge.", "",
        _md_game(mlb, "MLB (team-form, all seasons)"),
        _md_game(mlb_tf, f"MLB team-form ({', '.join(map(str, starter_seasons))})"),
        _md_game(mlb_sp, f"MLB full model — starters+bullpen+park "
                 f"({', '.join(map(str, starter_seasons))})"),
        _md_clv(clv), _md_mlb_props(mlb_props_cal),
        _md_game(wnba), _md_props(props),
    ])
    (reports / f"backtest_{today}.md").write_text(md)
    print(f"\nWrote reports/backtest_{today}.md and .json")


if __name__ == "__main__":
    main()
