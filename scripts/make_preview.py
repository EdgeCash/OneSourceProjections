"""Render a self-contained HTML preview of the site's graphics (game
research card, props heatmap board, prop trend chart) using the real
renderers and real data. Open the output in a browser.

    python scripts/make_preview.py [--date YYYY-MM-DD] [--sport WNBA] [-o out.html]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app import ui  # noqa: E402
from onesource import playerlogs, teamstats  # noqa: E402


def _heatmap_table(props: list[dict]) -> str:
    df = ui.prep_props(pd.DataFrame(props))
    cols = [c for c in ("Player", "Market", "Line", "Odds", "Proj", "EV",
                        "L5", "L10", "L20", "Season") if c in df.columns]
    df = df[cols]

    def cell(col, v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "<td style='padding:5px 8px;color:#6e7781;'>—</td>"
        if col in ui.HEAT_COLS:
            r = max(0, min(100, float(v))) / 100
            bg = f"rgba({int(220*(1-r)+30*r)},{int(40*(1-r)+140*r)},60,0.55)"
            return (f"<td style='padding:5px 8px;text-align:center;"
                    f"background:{bg};font-weight:600;'>{v:.0f}%</td>")
        if col == "EV":
            return f"<td style='padding:5px 8px;text-align:right;color:#3fb950;'>{v:+.1f}%</td>"
        txt = f"{v:.2f}" if col == "Proj" and isinstance(v, float) else v
        return f"<td style='padding:5px 8px;text-align:right;'>{txt}</td>"

    head = "".join(f"<th style='padding:6px 8px;color:#8b949e;font-size:0.72rem;"
                   f"text-transform:uppercase;text-align:right;'>{c}</th>"
                   for c in cols)
    body = "".join(
        "<tr style='border-top:1px solid #1c2330;'>"
        + "".join(cell(c, r[c]) for c in cols) + "</tr>"
        for _, r in df.iterrows())
    return ("<table style='width:100%;border-collapse:collapse;font-size:0.86rem;'>"
            f"<tr>{head}</tr>{body}</table>")


def _svg_chart(series: list[dict], line: float, title: str) -> str:
    if not series:
        return "<div style='color:#6e7781;'>no game log</div>"
    w, h, pad = 560, 220, 26
    vmax = max([s["value"] for s in series] + [line]) * 1.15 or 1
    bw = (w - 2 * pad) / len(series)
    bars = []
    for i, s in enumerate(series):
        bh = (s["value"] / vmax) * (h - 2 * pad)
        x = pad + i * bw
        y = h - pad - bh
        color = "#3fb950" if s["value"] > line else "#f85149"
        bars.append(f"<rect x='{x+3:.0f}' y='{y:.0f}' width='{bw-6:.0f}' "
                    f"height='{bh:.0f}' fill='{color}' rx='2'/>")
        bars.append(f"<text x='{x+bw/2:.0f}' y='{h-pad+12:.0f}' fill='#8b949e' "
                    f"font-size='9' text-anchor='middle'>{s['date']}</text>")
        bars.append(f"<text x='{x+bw/2:.0f}' y='{y-3:.0f}' fill='#c9d1d9' "
                    f"font-size='9' text-anchor='middle'>{s['value']:.0f}</text>")
    ly = h - pad - (line / vmax) * (h - 2 * pad)
    bars.append(f"<line x1='{pad}' y1='{ly:.0f}' x2='{w-pad}' y2='{ly:.0f}' "
                f"stroke='#e3b341' stroke-width='2' stroke-dasharray='5,4'/>")
    bars.append(f"<text x='{w-pad}' y='{ly-4:.0f}' fill='#e3b341' font-size='10' "
                f"text-anchor='end'>line {line}</text>")
    return (f"<div style='font-weight:600;margin-bottom:4px;'>{title}</div>"
            f"<svg width='100%' viewBox='0 0 {w} {h}'>{''.join(bars)}</svg>")


def build(date: str, sport: str, game: dict, props: list[dict]) -> str:
    matchup = teamstats.matchup(sport, game["home_team"], game["away_team"], date)
    card = ui.research_card_html(sport, game, matchup)
    board = _heatmap_table(props)
    p0 = props[0]
    series = playerlogs.recent_series(sport, p0["player"], p0["market"], n=10,
                                      season=int(date[:4]))
    chart = _svg_chart(series, float(p0["line"]), f"{p0['player']} · "
                       f"{ui.short_market(p0['market'])} (last {len(series)})")
    nav = "".join(
        f"<div style='padding:9px 14px;border-radius:8px;margin:2px 0;"
        f"{'background:#1c2330;font-weight:700;' if s == sport else 'color:#8b949e;'}'>"
        f"{s}</div>" for s in ["MLB", "WNBA", "NBA", "NHL", "PLAYS", "PERFORMANCE"])
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>OneSource Projections — preview</title></head>
<body style='margin:0;background:#0e1117;color:#e6e9ef;font-family:-apple-system,
Segoe UI,Roboto,sans-serif;'>
<div style='display:flex;min-height:100vh;'>
  <div style='width:190px;background:#0b0f16;border-right:1px solid #1c2330;padding:16px 12px;'>
    <div style='font-size:1.25rem;font-weight:800;margin-bottom:14px;'>🎯 OneSource</div>
    {nav}
  </div>
  <div style='flex:1;padding:22px 28px;max-width:1100px;'>
    <div style='display:flex;justify-content:space-between;align-items:center;'>
      <div style='font-size:1.6rem;font-weight:800;'>{sport}</div>
      <div style='background:#161b24;border:1px solid #232a36;border-radius:20px;
        padding:7px 16px;color:#6e7781;'>🔍  team or player…</div>
    </div>
    <div style='color:#6e7781;font-size:0.78rem;margin:4px 0 16px;'>
      Slate {date} · static preview of the live Streamlit graphics</div>
    <div style='font-size:1.05rem;font-weight:700;margin-bottom:6px;'>📅 Game research</div>
    {card}
    <div style='font-size:1.05rem;font-weight:700;margin:18px 0 6px;'>👤 Props — hit-rate board</div>
    <div style='background:#161b24;border:1px solid #232a36;border-radius:12px;padding:14px;'>{board}</div>
    <div style='font-size:1.05rem;font-weight:700;margin:18px 0 6px;'>📊 Player trend</div>
    <div style='background:#161b24;border:1px solid #232a36;border-radius:12px;padding:16px;'>{chart}</div>
  </div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-02")
    ap.add_argument("--sport", default="WNBA")
    ap.add_argument("-o", "--out", default="preview.html")
    args = ap.parse_args()
    # sample slate (real teams/players so logos + logs resolve)
    if args.sport == "WNBA":
        game = {"away_team": "Indiana Fever", "home_team": "Las Vegas Aces",
                "game_time": "2026-06-02T23:00:00Z", "away_exp": 79.2,
                "home_exp": 86.0, "proj_total": 165.2, "home_win_prob": 0.70,
                "away_win_prob": 0.30, "home_ml": -260, "home_ml_ev": 0.03,
                "away_ml": 215, "away_ml_ev": -0.07, "total_line": 165.5,
                "model_over_prob": 0.49, "over_ev": -0.03}
        props = [
            {"player": "A'ja Wilson", "market": "Points", "line": 22.5, "over_odds": -115,
             "projection": 24.6, "ev_over": 0.09, "hr_l5": 0.6, "hr_l10": 0.5,
             "hr_l20": 0.55, "hr_season": 0.62},
            {"player": "Kelsey Plum", "market": "Points", "line": 17.5, "odds": -110,
             "projection": 18.4, "ev": 0.05, "hr_l5": 0.6, "hr_l10": 0.7,
             "hr_l20": 0.6, "hr_season": 0.55},
        ]
    else:
        game = {"away_team": "New York Yankees", "home_team": "Boston Red Sox",
                "game_time": "2026-06-02T23:10:00Z", "away_exp_runs": 4.6,
                "home_exp_runs": 4.2, "proj_total": 8.8, "home_win_prob": 0.49,
                "away_win_prob": 0.51, "home_ml": 105, "home_ml_ev": 0.04,
                "away_ml": -115, "away_ml_ev": -0.02, "total_line": 8.5,
                "model_over_prob": 0.53, "over_ev": 0.03}
        props = [
            {"player": "Aaron Judge", "market": "batter_total_bases", "line": 1.5,
             "odds": -120, "projection": 1.8, "ev": 0.06, "hr_l5": 0.4, "hr_l10": 0.5,
             "hr_l20": 0.45, "hr_season": 0.47}]
    Path(args.out).write_text(build(args.date, args.sport, game, props))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
