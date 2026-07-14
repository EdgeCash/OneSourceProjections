"""CLI entrypoint: run the engines and print a clean terminal view.

Examples
--------
    python -m src.app --mode both   --sport MLB
    python -m src.app --mode dfs    --sport WNBA --source sample --budget 50000
    python -m src.app --mode pickem --sport MLB  --platform PrizePicks

Run from the ``sports_wagering_pipeline/`` directory. For the daily Excel
workbook across sports, use ``python -m src.export`` instead.
"""

from __future__ import annotations

import argparse

from . import api_client, db_manager, export


def _hr(char: str = "=", width: int = 72) -> str:
    return char * width


def print_dfs(sport: str, budget: int, lineup: list) -> None:
    print(f"\n{_hr()}")
    print(f"  DRAFTKINGS SALARY-CAP DFS  |  {sport}  |  cap ${budget:,}")
    print(_hr())
    if not lineup:
        print("  No feasible lineup (empty slate or constraints unmet).")
        return
    print(f"  {'POS':<4} {'PLAYER':<26} {'PROJ':>7} {'SALARY':>9}")
    print(f"  {'-'*4} {'-'*26} {'-'*7} {'-'*9}")
    total_pts = total_sal = 0.0
    for p in lineup:
        total_pts += p["projected_points"]
        total_sal += p["salary_dk"]
        print(
            f"  {p['position']:<4} {p['player_name'][:26]:<26} "
            f"{p['projected_points']:>7.1f} {p['salary_dk']:>9,}"
        )
    print(f"  {'-'*4} {'-'*26} {'-'*7} {'-'*9}")
    print(
        f"  {'TOT':<4} {'':<26} {total_pts:>7.1f} {int(total_sal):>9,}"
        f"   (${budget - int(total_sal):,} left)"
    )


def print_pickem(sport: str, platform: str, plays: list) -> None:
    print(f"\n{_hr()}")
    print(f"  PICK'EM SLIP  |  {sport}  |  {platform}  |  break-even 54.3%")
    print(_hr())
    if not plays:
        print("  No viable plays above the 54.3% threshold.")
        return
    print(
        f"  {'#':<2} {'PLAYER':<24} {'STAT':<16} {'LINE':>6} "
        f"{'SIDE':<6} {'WIN%':>7} {'EDGE':>7}"
    )
    print(f"  {'-'*2} {'-'*24} {'-'*16} {'-'*6} {'-'*6} {'-'*7} {'-'*7}")
    for i, p in enumerate(plays, 1):
        print(
            f"  {i:<2} {p['player_name'][:24]:<24} {p['stat_type'][:16]:<16} "
            f"{p['line_value']:>6.1f} {p['side']:<6} "
            f"{p['win_rate']*100:>6.1f}% {p['edge_vs_breakeven']*100:>+6.1f}%"
        )
    n = len(plays)
    print(f"\n  -> {n}-pick slip ready ({'viable' if 2 <= n <= 6 else 'review'}).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sports wagering / DFS / Pick'em pipeline")
    ap.add_argument("--mode", choices=["dfs", "pickem", "both"], default="both")
    ap.add_argument("--sport", default="MLB")
    ap.add_argument("--platform", default="PrizePicks",
                    help="Pick'em book: PrizePicks | Underdog | DraftKings_Pick6")
    ap.add_argument("--budget", type=int, default=50000, help="DK salary cap")
    ap.add_argument(
        "--source", choices=["shared", "sample"], default="shared",
        help="shared: reuse the main engine's warm FantasyPros/BettingPros cache "
             "(zero extra API calls); sample: offline baked-in slate",
    )
    ap.add_argument("--date", default=None,
                    help="slate date YYYY-MM-DD (default: main engine's date)")
    args = ap.parse_args(argv)

    sport = args.sport.upper()
    date = export.anchor_date(args.date)
    conn = db_manager.connect()
    db_manager.init_db(conn)

    try:
        res = export.run_one(conn, sport, args.platform, args.budget,
                             args.source, date, mode=args.mode)

        if args.mode in ("dfs", "both"):
            print(f"[cache] projections ({args.source}): "
                  f"{'refreshed ' + str(res['proj_refreshed']) if res['proj_refreshed'] else 'hit'}")
            print_dfs(sport, args.budget, res["dfs"])
            if not res["dfs"] and args.source == "shared":
                print("  (shared FantasyPros data carries no DK salary/position; "
                      "run --source sample for the salary-cap demo.)")

        if args.mode in ("pickem", "both"):
            print(f"[cache] {args.platform} lines ({args.source}): "
                  f"{'refreshed ' + str(res['lines_refreshed']) if res['lines_refreshed'] else 'hit'}"
                  f"  [{res['lines_source']}]")
            print_pickem(sport, args.platform, res["pickem"])

        print(f"\n[budget] external requests last 24h: "
              f"{db_manager.api_usage_today(conn)} / {api_client.DAILY_BUDGET}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
