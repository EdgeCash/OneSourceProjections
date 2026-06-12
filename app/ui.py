"""Presentation helpers for the dashboard: formatting, view preparation,
and the cross-sport best-bets board. Pure functions over the latest.json
shapes so they're testable without Streamlit."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

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
    "bp_projection": "BP Proj", "bp_ev": "BP EV",
    "bp_recommended_side": "BP Side", "bp_bet_rating": "BP ★",
}

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
        if c in PCT_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce") * 100
        elif c in ODDS_COLS:
            df[c] = df[c].map(fmt_american)
        elif c in EV_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce") * 100
    return df


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
