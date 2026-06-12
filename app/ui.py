"""Presentation helpers for the dashboard: formatting, view preparation,
and the cross-sport best-bets board. Pure functions over the latest.json
shapes so they're testable without Streamlit."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app import assets

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_american(odds) -> str:
    """-110 -> '-110', 120 -> '+120'."""
    if odds is None or (isinstance(odds, float) and pd.isna(odds)):
        return ""
    n = int(round(float(odds)))
    return f"+{n}" if n > 0 else str(n)


def fmt_time_et(iso_ts: str | None) -> str:
    """ISO timestamp -> '7:10 PM' Eastern."""
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return str(iso_ts)


def short_market(market: str) -> str:
    """'pitcher_strikeouts' -> 'Pitcher Ks', 'batter_total_bases' -> 'Total Bases'."""
    pretty = {
        "pitcher_strikeouts": "Pitcher Ks",
        "batter_hits": "Hits",
        "batter_total_bases": "Total Bases",
        "batter_home_runs": "Home Run",
        "moneyline": "Moneyline",
        "total": "Total",
        "spread": "Spread",
    }
    if market in pretty:
        return pretty[market]
    return str(market).replace("batter_", "").replace("_", " ").title()


# ---------------------------------------------------------------------------
# Best-bets board (cross-sport, one slate date)
# ---------------------------------------------------------------------------

def build_best_bets(day_slates: dict, min_edge: float) -> pd.DataFrame:
    """Flatten every model edge >= min_edge in a date's slates (all sports,
    games + props) into one board sorted by EV."""
    rows: list[dict] = []
    for sport, blob in (day_slates or {}).items():
        for g in blob.get("games", []) or []:
            rows += _game_edges(sport, g)
        for p in blob.get("props", []) or []:
            row = _prop_edge(sport, p)
            if row:
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[pd.to_numeric(df["ev"], errors="coerce") >= min_edge]
    return df.sort_values("ev", ascending=False).reset_index(drop=True)


def _game_edges(sport: str, g: dict) -> list[dict]:
    rows = []
    matchup = f"{g.get('away_team')} @ {g.get('home_team')}"
    for side in ("home", "away"):
        ev = g.get(f"{side}_ml_ev", g.get(f"{side}_ev"))
        price = g.get(f"{side}_ml")
        prob = g.get(f"{side}_win_prob")
        if ev is not None and price is not None and pd.notna(ev):
            rows.append({
                "sport": sport, "type": "Game", "market": "Moneyline",
                "bet": f"{g.get(f'{side}_team')} ML", "game": matchup,
                "line": None, "price": price, "model_prob": prob, "ev": ev,
                "kelly": None, "time": g.get("game_time"),
            })
    if g.get("over_ev") is not None and pd.notna(g.get("over_ev")):
        rows.append({
            "sport": sport, "type": "Game", "market": "Total",
            "bet": f"Over {g.get('total_line')}", "game": matchup,
            "line": g.get("total_line"), "price": g.get("over_odds"),
            "model_prob": g.get("model_over_prob"), "ev": g.get("over_ev"),
            "kelly": None, "time": g.get("game_time"),
        })
    return rows


def _prop_edge(sport: str, p: dict) -> dict | None:
    """Best side of a prop row (handles both the MLB shape: ev/odds over
    only, and the generic shape: ev_over/ev_under)."""
    cands = []
    if p.get("ev") is not None and pd.notna(p.get("ev")):
        cands.append(("Over", p["ev"], p.get("odds"), p.get("model_over_prob")))
    if p.get("ev_over") is not None and pd.notna(p.get("ev_over")):
        cands.append(("Over", p["ev_over"], p.get("over_odds"), p.get("model_over_prob")))
    if p.get("ev_under") is not None and pd.notna(p.get("ev_under")):
        mp = p.get("model_over_prob")
        cands.append(("Under", p["ev_under"], p.get("under_odds"),
                      (1 - mp) if mp is not None else None))
    if not cands:
        return None
    side, ev, price, prob = max(cands, key=lambda c: c[1])
    line = p.get("line")
    line_txt = "" if line is None or pd.isna(line) else f" {line}"
    return {
        "sport": sport, "type": "Prop", "market": short_market(p.get("market", "")),
        "bet": f"{p.get('player')} {side}{line_txt} {short_market(p.get('market', ''))}",
        "game": f"{p.get('team', '')} vs {p.get('opponent', '')}".strip(" vs"),
        "line": line, "price": price, "model_prob": prob, "ev": ev,
        "kelly": p.get("kelly"), "time": None,
    }


# ---------------------------------------------------------------------------
# Game matchup card (HTML)
# ---------------------------------------------------------------------------

def _pct(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v) * 100:.0f}%"


def _num(v, dp=1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v):.{dp}f}"


def _exp(game: dict, side: str):
    return game.get(f"{side}_exp_runs", game.get(f"{side}_exp"))


def _best_edge(game: dict) -> tuple[str, float] | None:
    """Largest positive model edge on a game, as (label, ev)."""
    cands = []
    for side in ("home", "away"):
        ev = game.get(f"{side}_ml_ev", game.get(f"{side}_ev"))
        if ev is not None and pd.notna(ev):
            cands.append((f"{game.get(f'{side}_team')} ML", float(ev)))
    if game.get("over_ev") is not None and pd.notna(game.get("over_ev")):
        cands.append((f"Over {game.get('total_line')}", float(game["over_ev"])))
    cands = [c for c in cands if c[1] > 0]
    return max(cands, key=lambda c: c[1]) if cands else None


def game_card_html(sport: str, g: dict) -> str:
    """A compact matchup card: logos, projected score, win %, line/total,
    and the best model edge. Designed to read at a glance."""
    away, home = g.get("away_team", ""), g.get("home_team", "")
    a_badge = assets.team_badge_html(sport, away, 40)
    h_badge = assets.team_badge_html(sport, home, 40)
    a_exp, h_exp = _exp(g, "away"), _exp(g, "home")
    a_wp, h_wp = g.get("away_win_prob"), g.get("home_win_prob")
    time = fmt_time_et(g.get("game_time"))
    total = g.get("total_line") or g.get("proj_total")

    edge = _best_edge(g)
    if edge:
        edge_html = (f"<span style='color:#3fb950;font-weight:600;'>"
                     f"▲ {edge[0]} · +{edge[1] * 100:.1f}% EV</span>")
    else:
        edge_html = "<span style='color:#8b949e;'>no edge ≥ threshold</span>"

    def side(badge, name, exp, wp, fav):
        weight = "700" if fav else "500"
        return (
            f"<div style='display:flex;align-items:center;gap:10px;flex:1;'>"
            f"{badge}"
            f"<div><div style='font-weight:{weight};font-size:0.95rem;'>{name}</div>"
            f"<div style='color:#8b949e;font-size:0.8rem;'>win {_pct(wp)}</div></div>"
            f"<div style='margin-left:auto;font-size:1.5rem;font-weight:700;'>"
            f"{_num(exp)}</div></div>"
        )

    home_fav = (h_wp or 0) >= (a_wp or 0)
    return (
        "<div style='background:#161b24;border:1px solid #232a36;border-radius:12px;"
        "padding:14px 16px;margin-bottom:12px;'>"
        f"<div style='color:#8b949e;font-size:0.78rem;margin-bottom:8px;'>"
        f"{time} · O/U {_num(total)} · proj total {_num(g.get('proj_total'))}</div>"
        f"{side(a_badge, away, a_exp, a_wp, not home_fav)}"
        "<div style='height:8px;'></div>"
        f"{side(h_badge, home, h_exp, h_wp, home_fav)}"
        "<div style='border-top:1px solid #232a36;margin-top:10px;padding-top:8px;"
        f"font-size:0.85rem;'>{edge_html}</div>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Full game research card (HTML): header, gauges, stat tables, trends
# ---------------------------------------------------------------------------

def _fmt_stat(label: str, v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    v = float(v)
    if "%" in label:
        return f"{v * 100:.1f}%"
    if label == "AVG":
        return f"{v:.3f}".lstrip("0")
    return f"{v:.1f}"


def _rank_badge(rank, n_teams: int) -> str:
    if rank is None or pd.isna(rank):
        return "<span style='color:#6e7781;font-size:0.72rem;'>—</span>"
    rank = int(rank)
    third = max(1, n_teams / 3)
    color = "#3fb950" if rank <= third else ("#f0b72f" if rank <= 2 * third else "#f85149")
    return (f"<span style='color:{color};font-size:0.72rem;font-weight:600;'>"
            f"{rank}</span>")


def _stat_table_html(title: str, rows: list[dict], n_teams: int) -> str:
    head = (
        "<tr style='color:#8b949e;font-size:0.7rem;text-transform:uppercase;'>"
        "<th style='text-align:left;padding:4px 6px;'>Stat</th>"
        "<th style='text-align:right;'>L5</th><th>Rk</th>"
        "<th style='width:34px;'></th>"
        "<th>Rk</th><th style='text-align:left;padding-left:6px;'>Opp L5</th></tr>"
    )
    body = []
    for r in rows:
        stars = "★" * r.get("adv", 0)
        star_html = (f"<span style='color:#e3b341;'>{stars}</span>" if stars
                     else "")
        body.append(
            "<tr style='border-top:1px solid #1c2330;'>"
            f"<td style='text-align:left;padding:4px 6px;font-weight:600;'>{r['stat']}</td>"
            f"<td style='text-align:right;'>{_fmt_stat(r['stat'], r['off_l5'])}</td>"
            f"<td style='text-align:center;'>{_rank_badge(r['off_rank'], n_teams)}</td>"
            f"<td style='text-align:center;'>{star_html}</td>"
            f"<td style='text-align:center;'>{_rank_badge(r['def_rank'], n_teams)}</td>"
            f"<td style='text-align:left;padding-left:6px;color:#8b949e;'>"
            f"{_fmt_stat(r['stat'], r['def_l5'])}</td></tr>"
        )
    return (
        f"<div style='font-size:0.78rem;color:#58a6ff;font-weight:700;"
        f"text-transform:uppercase;margin:10px 0 2px;'>{title}</div>"
        "<table style='width:100%;border-collapse:collapse;font-size:0.85rem;'>"
        f"{head}{''.join(body)}</table>"
    )


def _gauge_pill(label: str, value: str, ev, threshold: float) -> str:
    play = ev is not None and pd.notna(ev) and ev >= threshold
    color = "#3fb950" if play else "#6e7781"
    tag = "PLAY" if play else "PASS"
    ev_txt = f" · {ev * 100:+.1f}% EV" if ev is not None and pd.notna(ev) else ""
    return (
        f"<div style='flex:1;background:#0d1117;border:1px solid {color};"
        f"border-radius:10px;padding:8px 12px;text-align:center;'>"
        f"<div style='color:#8b949e;font-size:0.7rem;text-transform:uppercase;'>{label}</div>"
        f"<div style='font-size:1.0rem;font-weight:700;margin:2px 0;'>{value}</div>"
        f"<div style='color:{color};font-size:0.72rem;font-weight:700;'>{tag}{ev_txt}</div>"
        "</div>"
    )


def research_card_html(sport: str, g: dict, matchup: dict, min_edge: float = 0.02) -> str:
    away, home = g.get("away_team", ""), g.get("home_team", "")
    a_badge = assets.team_badge_html(sport, away, 38)
    h_badge = assets.team_badge_html(sport, home, 38)
    n = matchup.get("n_teams", 30)

    # header
    header = (
        "<div style='display:flex;align-items:center;gap:14px;'>"
        f"<div style='flex:1;display:flex;align-items:center;gap:8px;justify-content:flex-end;'>"
        f"<span style='font-weight:700;'>{away}</span>{a_badge}</div>"
        "<span style='color:#8b949e;font-size:0.8rem;'>@</span>"
        f"<div style='flex:1;display:flex;align-items:center;gap:8px;'>"
        f"{h_badge}<span style='font-weight:700;'>{home}</span></div></div>"
        f"<div style='text-align:center;color:#8b949e;font-size:0.76rem;margin-top:4px;'>"
        f"{fmt_time_et(g.get('game_time'))} · O/U {_num(g.get('total_line') or g.get('proj_total'))}"
        f" · proj {_num(_exp(g,'away'))}–{_num(_exp(g,'home'))}</div>"
    )

    # gauges (model)
    ml_fav = (g.get("home_win_prob") or 0) >= (g.get("away_win_prob") or 0)
    ml_team = home if ml_fav else away
    ml_prob = g.get("home_win_prob") if ml_fav else g.get("away_win_prob")
    ml_ev = g.get("home_ml_ev", g.get("home_ev")) if ml_fav else g.get("away_ml_ev", g.get("away_ev"))
    gauges = (
        "<div style='display:flex;gap:8px;margin:10px 0;'>"
        + _gauge_pill("Moneyline", f"{ml_team} {_pct(ml_prob)}", ml_ev, min_edge)
        + _gauge_pill("Total", f"O {_num(g.get('total_line'))} · {_pct(g.get('model_over_prob'))}",
                      g.get("over_ev"), min_edge)
        + "</div>"
    )

    # stat tables
    away_lbl = ("Batting vs Pitching" if sport == "MLB" else "Offense vs Defense")
    tables = ""
    if matchup.get("away_off_vs_home_def"):
        tables += _stat_table_html(f"{away} {away_lbl}",
                                   matchup["away_off_vs_home_def"], n)
    if matchup.get("home_off_vs_away_def"):
        tables += _stat_table_html(f"{home} {away_lbl}",
                                   matchup["home_off_vs_away_def"], n)

    # trends (MLB)
    trends = ""
    tr = matchup.get("trends") or []
    if tr:
        cells = "".join(
            f"<div style='flex:1;text-align:center;'>"
            f"<div style='color:#8b949e;font-size:0.66rem;'>{t['stat']}</div>"
            f"<div style='font-size:0.8rem;'>{_fmt_stat(t['stat']+'%', t['away'])}"
            f" / {_fmt_stat(t['stat']+'%', t['home'])}</div></div>"
            for t in tr)
        trends = ("<div style='font-size:0.72rem;color:#58a6ff;font-weight:700;"
                  "text-transform:uppercase;margin:10px 0 2px;'>Trends "
                  "(away / home)</div>"
                  f"<div style='display:flex;gap:6px;'>{cells}</div>")

    return (
        "<div style='background:#161b24;border:1px solid #232a36;border-radius:14px;"
        "padding:16px 18px;margin-bottom:14px;'>"
        f"{header}{gauges}{tables}{trends}</div>"
    )


# ---------------------------------------------------------------------------
# View preparation (friendly columns + column_config-ready values)
# ---------------------------------------------------------------------------

GAME_RENAMES = {
    "game_time": "Time", "away_team": "Away", "home_team": "Home",
    "away_pitcher": "Away SP", "home_pitcher": "Home SP",
    "away_exp_runs": "Away Proj", "home_exp_runs": "Home Proj",
    "away_exp": "Away Proj", "home_exp": "Home Proj",
    "proj_total": "Proj Total", "away_win_prob": "Away Win",
    "home_win_prob": "Home Win", "away_ml": "Away ML", "home_ml": "Home ML",
    "away_ev": "Away EV", "home_ev": "Home EV",
    "away_ml_ev": "Away EV", "home_ml_ev": "Home EV",
    "total_line": "O/U Line", "over_odds": "Over Odds",
    "model_over_prob": "Over %", "over_ev": "Over EV",
}

PROP_RENAMES = {
    "player": "Player", "team": "Team", "opponent": "Opp", "market": "Market",
    "projection": "Proj", "fp_projection": "FP Proj", "line": "Line",
    "odds": "Odds", "over_odds": "Over", "under_odds": "Under",
    "model_over_prob": "Over %", "ev": "EV", "ev_over": "Over EV",
    "ev_under": "Under EV", "kelly": "Kelly",
    "hr_l5": "L5", "hr_l10": "L10", "hr_l20": "L20", "hr_season": "Season",
    "hr_h2h": "H2H",
    "bp_projection": "BP Proj", "bp_ev": "BP EV",
    "bp_recommended_side": "BP Side", "bp_bet_rating": "BP ★",
}

# hit-rate heatmap columns (rendered 0-100 with a red->green gradient)
HEAT_COLS = ["L5", "L10", "L20", "Season", "H2H"]

PCT_COLS = {"Away Win", "Home Win", "Over %", "Model %"}
EV_COLS = {"Away EV", "Home EV", "Over EV", "Under EV", "EV", "EV %"}
ODDS_COLS = {"Away ML", "Home ML", "Over Odds", "Odds", "Over", "Under", "Price"}


def prep_games(games: pd.DataFrame) -> pd.DataFrame:
    df = games.copy()
    keep = [c for c in GAME_RENAMES if c in df.columns]
    df = df[keep].rename(columns=GAME_RENAMES)
    df = df.loc[:, ~df.columns.duplicated()]
    if "Time" in df.columns:
        df["Time"] = df["Time"].map(fmt_time_et)
    for c in df.columns:
        if c in PCT_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce") * 100
        elif c in ODDS_COLS:
            df[c] = df[c].map(fmt_american)
        elif c in EV_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce") * 100
    return df


def prep_props(props: pd.DataFrame) -> pd.DataFrame:
    df = props.copy()
    if "market" in df.columns:
        df["market"] = df["market"].map(short_market)
    keep = [c for c in PROP_RENAMES if c in df.columns]
    df = df[keep].rename(columns=PROP_RENAMES)
    df = df.dropna(axis=1, how="all")
    for c in df.columns:
        if c in PCT_COLS or c in HEAT_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce") * 100
        elif c in ODDS_COLS:
            df[c] = df[c].map(fmt_american)
        elif c in EV_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce") * 100
    return df


def prop_chart(series: list[dict], line: float, title: str):
    """Altair bar chart of recent games vs the line — green over, red under,
    dashed line at the prop number. Returns None if there's no data."""
    import altair as alt

    if not series:
        return None
    df = pd.DataFrame(series)
    df["over"] = df["value"] > line
    df["label"] = df["date"] + "  " + df["opp"].fillna("")
    bars = alt.Chart(df).mark_bar().encode(
        x=alt.X("label:N", sort=None, axis=alt.Axis(title=None, labelAngle=-40)),
        y=alt.Y("value:Q", title=title),
        color=alt.condition("datum.value > %f" % line,
                            alt.value("#3fb950"), alt.value("#f85149")),
        tooltip=["date", "value", "opp"],
    )
    rule = alt.Chart(pd.DataFrame({"y": [line]})).mark_rule(
        color="#e3b341", strokeDash=[5, 4], size=2).encode(y="y:Q")
    return (bars + rule).properties(height=260, width="container")


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def cumulative_units(ledger: list[dict]) -> pd.DataFrame:
    """Date-indexed cumulative P&L of graded bets, for the equity chart."""
    bets = [r for r in ledger if "pnl" in r]
    if not bets:
        return pd.DataFrame()
    df = pd.DataFrame(bets)
    daily = df.groupby("date")["pnl"].sum().sort_index()
    return daily.cumsum().rename("units").to_frame()


def recent_bets(ledger: list[dict], n: int = 25) -> pd.DataFrame:
    bets = [r for r in ledger if "pnl" in r]
    if not bets:
        return pd.DataFrame()
    df = pd.DataFrame(bets).sort_values("date", ascending=False).head(n)
    df["price"] = df["price"].map(fmt_american)
    df["result"] = df["won"].map(lambda w: "✅ Win" if w else "❌ Loss")
    cols = ["date", "sport", "game", "market", "side", "line", "price",
            "ev", "result", "pnl"]
    return df[[c for c in cols if c in df.columns]].rename(columns={
        "date": "Date", "sport": "Sport", "game": "Game", "market": "Market",
        "side": "Side", "line": "Line", "price": "Price", "ev": "EV",
        "result": "Result", "pnl": "Units"})
