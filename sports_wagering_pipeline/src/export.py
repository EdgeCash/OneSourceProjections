"""Structured runner + daily Excel (.xlsx) workbook writer.

``run_one`` executes the engines for one sport and returns a plain dict (reused
by the CLI printer in ``app.py`` and by the workbook builder here). ``main``
builds a multi-sport ``.xlsx`` — the automated daily deliverable — plus an
optional JSON sidecar.

Excel, not Google Sheets, on purpose: openpyxl is already a repo dependency and
this needs no Google Cloud project, service account, or secrets. The workbook is
just written to disk and committed by the hourly job.

    python -m src.export --sports MLB,WNBA --source shared \
        --out data/output/latest.xlsx --json data/output/latest.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from . import api_client, db_manager, engine


# --------------------------------------------------------------------------- #
# Shared runner
# --------------------------------------------------------------------------- #
def anchor_date(cli_date: str | None) -> str | None:
    """Slate date used to align the shared cache key with the main engine's
    pull: explicit --date, then the committed slate's ``primary_date``, then
    today ET."""
    if cli_date:
        return cli_date
    latest = Path(__file__).resolve().parents[2] / "data" / "output" / "latest.json"
    try:
        return json.loads(latest.read_text())["primary_date"]
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return None


def run_one(
    conn,
    sport: str,
    platform: str = "PrizePicks",
    budget: int = 50000,
    source: str = "shared",
    date: str | None = None,
    mode: str = "both",
) -> dict:
    """Run the engines for one sport; return a structured result dict."""
    sport = sport.upper()
    res: dict = {
        "sport": sport, "platform": platform, "budget": budget,
        "source": source, "dfs": [], "pickem": [],
        "proj_refreshed": 0, "lines_refreshed": 0, "lines_source": None,
    }

    if mode in ("dfs", "both"):
        res["proj_refreshed"] = api_client.refresh_projections(
            conn, sport, date=date, source=source)
        res["dfs"] = engine.optimize_salary_cap_dfs(sport, budget, conn=conn)

    if mode in ("pickem", "both"):
        res["lines_refreshed"] = api_client.refresh_market_lines(
            conn, sport, platform, source=source, date=date)
        res["pickem"] = engine.generate_optimal_pickem_slips(
            sport, platform, conn=conn)
        # Real BettingPros lines carry a per-stat proj_mean; derived ones don't.
        lines = db_manager.get_market_lines(conn, sport, platform)
        res["lines_source"] = (
            "bettingpros" if any(l.get("proj_mean") is not None for l in lines)
            else "derived" if lines else "none"
        )
    return res


# --------------------------------------------------------------------------- #
# Excel workbook
# --------------------------------------------------------------------------- #
_HEADER_FILL = "1F2937"     # graphite
_HEADER_FONT = "FFFFFF"
_BAND_FILL = "F3F4F6"       # light zebra band
_TITLE_FONT = "111827"


def _style_header(ws, row_idx: int, ncols: int):
    from openpyxl.styles import Alignment, Font, PatternFill

    fill = PatternFill("solid", fgColor=_HEADER_FILL)
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row_idx, column=c)
        cell.fill = fill
        cell.font = Font(bold=True, color=_HEADER_FONT)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = ws.cell(row=row_idx + 1, column=1)


def _autosize(ws, widths: dict[int, int]):
    from openpyxl.utils import get_column_letter

    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _write_table(ws, start_row: int, headers: list, rows: list[list],
                 fmts: dict[int, str] | None = None) -> int:
    """Write a header + rows block; return the next free row."""
    from openpyxl.styles import Font, PatternFill

    for j, h in enumerate(headers, 1):
        ws.cell(row=start_row, column=j, value=h)
    _style_header(ws, start_row, len(headers))

    band = PatternFill("solid", fgColor=_BAND_FILL)
    r = start_row + 1
    for i, row in enumerate(rows):
        for j, val in enumerate(row, 1):
            cell = ws.cell(row=r, column=j, value=val)
            if fmts and j in fmts:
                cell.number_format = fmts[j]
            if i % 2 == 1:
                cell.fill = band
        r += 1
    return r


def _title(ws, text: str, sub: str | None = None):
    from openpyxl.styles import Font

    ws["A1"] = text
    ws["A1"].font = Font(bold=True, size=15, color=_TITLE_FONT)
    if sub:
        ws["A2"] = sub
        ws["A2"].font = Font(italic=True, size=10, color="6B7280")
    return 4 if sub else 3


def write_workbook(payload: dict, logs: list[dict], out_path: str | Path) -> Path:
    from openpyxl import Workbook

    PCT, EDGE = "0.0%", "+0.0%;-0.0%"
    MONEY, ODDS, NUM = "#,##0", "+0;-0", "0.0"

    wb = Workbook()

    # --- Summary -----------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    row = _title(ws, "Sports Wagering — Daily Comparison Workbook",
                 f"generated {payload['generated_at']}  |  source: "
                 f"{payload['source']}  |  slate date: {payload.get('date')}")
    headers = ["Sport", "Lines source", "DFS proj", "DFS salary",
               "Pick'em plays", "Top play", "Top win%"]
    rows = []
    for s in payload["sports"]:
        dfs_pts = round(sum(p["projected_points"] for p in s["dfs"]), 1)
        dfs_sal = int(sum(p["salary_dk"] for p in s["dfs"]))
        top = s["pickem"][0] if s["pickem"] else None
        rows.append([
            s["sport"], s.get("lines_source") or "-",
            dfs_pts if s["dfs"] else None,
            dfs_sal if s["dfs"] else None,
            len(s["pickem"]),
            f"{top['player_name']} {top['stat_type']} {top['side']} "
            f"{top['line_value']}" if top else "-",
            top["win_rate"] if top else None,
        ])
    _write_table(ws, row, headers, rows,
                 fmts={3: NUM, 4: MONEY, 7: PCT})
    ws["A" + str(row + len(rows) + 2)] = (
        f"API requests spent by this pipeline (last 24h): "
        f"{payload['budget_used']} / {payload['daily_budget']}  "
        f"— shared cache reuse, no double usage."
    )
    _autosize(ws, {1: 8, 2: 14, 3: 10, 4: 12, 5: 14, 6: 34, 7: 10})

    # --- Pickem ------------------------------------------------------------
    ws = wb.create_sheet("Pickem")
    row = _title(ws, "Pick'em Slips", "ranked by distance from the 54.3% "
                 "break-even; each line paired with its per-stat projection")
    headers = ["Sport", "Player", "Stat", "Line", "Side", "Win%",
               "Edge vs 54.3%", "Proj mean", "Proj std", "Over", "Under",
               "Platform"]
    rows = []
    for s in payload["sports"]:
        for p in s["pickem"]:
            rows.append([
                s["sport"], p["player_name"], p["stat_type"], p["line_value"],
                p["side"], p["win_rate"], p["edge_vs_breakeven"],
                p.get("proj_mean"), p.get("proj_std"),
                p.get("over_odds"), p.get("under_odds"), p["platform"],
            ])
    _write_table(ws, row, headers, rows,
                 fmts={4: NUM, 6: PCT, 7: EDGE, 8: NUM, 9: NUM,
                       10: ODDS, 11: ODDS})
    _autosize(ws, {1: 7, 2: 22, 3: 14, 4: 7, 5: 7, 6: 8, 7: 13, 8: 10,
                   9: 9, 10: 7, 11: 7, 12: 12})

    # --- DFS ---------------------------------------------------------------
    ws = wb.create_sheet("DFS")
    row = _title(ws, "DraftKings Salary-Cap Lineups",
                 "sample-salary slate (FantasyPros carries no DK salary/position)")
    headers = ["Sport", "Pos", "Player", "Proj", "Salary"]
    rows = []
    for s in payload["sports"]:
        for p in s["dfs"]:
            rows.append([s["sport"], p["position"], p["player_name"],
                         p["projected_points"], p["salary_dk"]])
        if s["dfs"]:
            rows.append([s["sport"], "TOT", "",
                         round(sum(p["projected_points"] for p in s["dfs"]), 1),
                         int(sum(p["salary_dk"] for p in s["dfs"]))])
    _write_table(ws, row, headers, rows, fmts={4: NUM, 5: MONEY})
    _autosize(ws, {1: 7, 2: 6, 3: 24, 4: 8, 5: 10})

    # --- Run_Log -----------------------------------------------------------
    ws = wb.create_sheet("Run_Log")
    row = _title(ws, "Run Log", "cache/source ledger — request_count=0 means a "
                 "warm-cache read, no external call")
    rows = [[lg["timestamp"], lg["endpoint"], lg["request_count"]] for lg in logs]
    _write_table(ws, row, ["Timestamp", "Endpoint", "Requests"], rows)
    _autosize(ws, {1: 22, 2: 40, 3: 10})

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))
    return out_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the daily comparison workbook")
    ap.add_argument("--sports", default="MLB", help="comma-separated, e.g. MLB,WNBA")
    ap.add_argument("--platform", default="PrizePicks")
    ap.add_argument("--budget", type=int, default=50000)
    ap.add_argument("--source", choices=["shared", "sample"], default="shared")
    ap.add_argument("--date", default=None)
    ap.add_argument("--out", default="data/output/latest.xlsx", help="xlsx path")
    ap.add_argument("--json", default=None, help="optional JSON sidecar path")
    args = ap.parse_args(argv)

    date = anchor_date(args.date)
    conn = db_manager.connect()
    db_manager.init_db(conn)
    try:
        sports = [s.strip().upper() for s in args.sports.split(",") if s.strip()]
        results = [run_one(conn, s, args.platform, args.budget, args.source, date)
                   for s in sports]
        logs = [dict(r) for r in conn.execute(
            "SELECT timestamp, endpoint, request_count FROM api_log ORDER BY id")]
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": args.source, "platform": args.platform, "date": date,
            "budget_used": db_manager.api_usage_today(conn),
            "daily_budget": api_client.DAILY_BUDGET,
            "sports": results,
        }
        out = write_workbook(payload, logs, args.out)
        msg = f"wrote {out}"
        if args.json:
            jp = Path(args.json)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps(payload, indent=1, default=str))
            msg += f" and {jp}"
        print(msg)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
