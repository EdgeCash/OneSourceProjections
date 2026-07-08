"""Presentation helpers for the dashboard: formatting, view preparation,
and the cross-sport best-bets board. Pure functions over the latest.json
shapes so they're testable without Streamlit."""

from __future__ import annotations

import logging
import math
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app import assets
from project547 import config, odds

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Query params that every player link should carry through a click so a
# full reload doesn't drop them (notably the ?k= "remember sign-in" token).
_LINK_KEEP: dict = {}


def set_link_keep(params: dict) -> None:
    _LINK_KEEP.clear()
    _LINK_KEEP.update({k: v for k, v in (params or {}).items() if v})


def player_link(name: str, game_pk=None, sport: str | None = None) -> str:
    """A clickable player name. Navigates the app to ?player=… so the
    dashboard can open the player-profile dialog. Falls back to plain text
    when there's no name."""
    if not name or (isinstance(name, float) and pd.isna(name)):
        return ""
    q = dict(_LINK_KEEP)
    q["player"] = str(name)
    if game_pk is not None and pd.notna(game_pk):
        q["game"] = str(game_pk)
    if sport:
        q["s"] = sport
    return (f"<a class='osp-plink' target='_self' "
            f"href='?{urllib.parse.urlencode(q)}'>{name}</a>")


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
    if not iso_ts or (isinstance(iso_ts, float) and pd.isna(iso_ts)):
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
    # An edge this large usually means the market knows something the model
    # doesn't (injury, lineup news) — surface it, but flagged.
    ev = pd.to_numeric(df["ev"], errors="coerce")
    df["flag"] = ev.map(lambda e: "🚫 implausible" if e is not None and e >= 0.30
                        else ("⚠️ verify news" if e is not None and e >= 0.15 else ""))
    # >=30% EV means the model is missing something (injuries, rotations,
    # off-board line) far more often than the market is wrong; zero the
    # suggested stake on those rows.
    df.loc[ev >= 0.30, "kelly"] = 0.0
    # Demonstrated-edge gate: tag each row with its market's status (from realized
    # CLV) and scale the suggested stake — full when the market is proven
    # (cleared), half while unproven (probation), zero where we've shown no edge
    # (gated). The tier chip is capped separately via play_tier(gate=...).
    df = _attach_edge_gate(df)
    df = _attach_slate_staking(df)
    return df.sort_values("ev", ascending=False).reset_index(drop=True)


def _attach_slate_staking(df: pd.DataFrame) -> pd.DataFrame:
    """Shrink stakes for correlated same-game legs and cap total slate exposure
    (config.SLATE_CORR / SLATE_MAX_EXPOSURE). No-op at their defaults."""
    if not len(df) or (config.SLATE_CORR <= 0 and not config.SLATE_MAX_EXPOSURE):
        return df
    try:
        from project547 import staking
        sport = df["sport"] if "sport" in df else [""] * len(df)
        game = df["game"] if "game" in df else [""] * len(df)
        groups = [f"{s}|{g}" for s, g in zip(sport, game)]
        kelly = pd.to_numeric(df["kelly"], errors="coerce").tolist()
        adj = staking.adjust_stakes(groups, kelly)
        df = df.copy()
        df["kelly"] = [round(a, 4) if isinstance(a, (int, float)) and a == a else a
                       for a in adj]
    except Exception:
        return df
    return df


# market label (board) -> edge_gate market key
_GATE_MARKET = {"Moneyline": "moneyline", "Total": "total",
                "Run Line": "spread", "Spread": "spread"}


def _attach_edge_gate(df: pd.DataFrame) -> pd.DataFrame:
    """Add a ``gate`` column (cleared/probation/gated per market) and scale the
    ``kelly`` stake by the gate's multiplier. Safe if the gate can't be built."""
    try:
        from project547 import edge_gate
        table = edge_gate.gate_table()
    except Exception:
        df["gate"] = "probation"
        return df
    def _status(r):
        mkt = _GATE_MARKET.get(r.get("market"), str(r.get("market", "")).lower())
        return edge_gate.status_for(r.get("sport"), mkt, table)
    df["gate"] = df.apply(_status, axis=1)
    mult = df["gate"].map(edge_gate.stake_mult)
    kelly = pd.to_numeric(df["kelly"], errors="coerce")
    df["kelly"] = (kelly * mult).where(kelly.notna(), df["kelly"])
    return df


def _tennis_edges(sport: str, g: dict) -> list[dict]:
    """Match-winner edges for a tennis match (player vs player money line)."""
    rows = []
    p1, p2 = g.get("player1"), g.get("player2")
    matchup = f"{p1} vs {p2}"
    for who, price_k, ev_k, prob_k in (
            ("player1", "p1_price", "p1_ev", "player1_win_prob"),
            ("player2", "p2_price", "p2_ev", "player2_win_prob")):
        name = g.get(who)
        price, ev, prob = g.get(price_k), g.get(ev_k), g.get(prob_k)
        if ev is not None and price is not None and pd.notna(ev) and pd.notna(price):
            rows.append({"sport": sport, "type": "Game", "market": "Moneyline",
                         "bet": f"{name} ML", "game": matchup, "line": None,
                         "price": price, "model_prob": prob, "ev": ev,
                         "kelly": g.get("kelly"), "time": g.get("match_time"),
                         "_home": p1, "_away": p2,
                         "_shop_mkt": "moneyline", "_sidekey": name})
    return rows


def _game_edges(sport: str, g: dict) -> list[dict]:
    if g.get("player1") and g.get("player2"):   # tennis: no teams/totals
        return _tennis_edges(sport, g)
    rows = []
    matchup = f"{g.get('away_team')} @ {g.get('home_team')}"
    home_t, away_t = g.get("home_team"), g.get("away_team")

    def add(market, bet, line, price, prob, ev, shop_mkt=None, sidekey=None):
        if ev is not None and price is not None and pd.notna(ev) and pd.notna(price):
            rows.append({"sport": sport, "type": "Game", "market": market,
                         "bet": bet, "game": matchup, "line": line,
                         "price": price, "model_prob": prob, "ev": ev,
                         "kelly": None, "time": g.get("game_time"),
                         "_home": home_t, "_away": away_t,
                         "_shop_mkt": shop_mkt, "_sidekey": sidekey})

    for side in ("home", "away"):
        add("Moneyline", f"{g.get(f'{side}_team')} ML", None,
            g.get(f"{side}_ml"), g.get(f"{side}_win_prob"),
            g.get(f"{side}_ml_ev", g.get(f"{side}_ev")),
            shop_mkt="moneyline", sidekey=g.get(f"{side}_team"))
    # soccer draw (three-way money line)
    if g.get("draw_prob") is not None:
        add("Moneyline", "Draw", None, g.get("draw_ml"), g.get("draw_prob"),
            g.get("draw_ev"), shop_mkt="moneyline", sidekey="Draw")
    # totals: MLB/team sports quote over_odds/model_over_prob; soccer quotes
    # over_price/over_2_5 (goals O/U 2.5) — accept either.
    total_line = g.get("total_line", 2.5 if g.get("over_price") is not None else None)
    over_price = g.get("over_odds", g.get("over_price"))
    under_price = g.get("under_odds", g.get("under_price"))
    mop = g.get("model_over_prob", g.get("over_2_5"))
    add("Total", f"Over {total_line}", total_line, over_price, mop,
        g.get("over_ev"), shop_mkt="total", sidekey="over")
    add("Total", f"Under {total_line}", total_line, under_price,
        (1 - mop) if mop is not None else None,
        g.get("under_ev"), shop_mkt="total", sidekey="under")
    # run line / spread (home side line; away is the opposite handicap)
    sp_line = g.get("rl_home_line", g.get("spread_home_line"))
    sp_label = "Run Line" if "rl_home_line" in g else "Spread"
    cover = g.get("model_home_rl", g.get("model_home_cover"))
    if sp_line is not None and pd.notna(sp_line):
        add(sp_label, f"{g.get('home_team')} {sp_line:+g}", sp_line,
            g.get("rl_home_odds", g.get("spread_home_odds")), cover,
            g.get("rl_home_ev", g.get("spread_home_ev")))
        add(sp_label, f"{g.get('away_team')} {-sp_line:+g}", -sp_line,
            g.get("rl_away_odds", g.get("spread_away_odds")),
            (1 - cover) if cover is not None else None,
            g.get("rl_away_ev", g.get("spread_away_ev")))
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


def _pub(game: dict, pub_key: str, raw):
    """Published (market-anchored) headline value with a flag for whether it
    actually differs from the raw model number (roadmap T3.1 / Component 4).
    Falls back to ``raw`` when the ``*_pub`` column is absent, and reports
    ``anchored=False`` when the anchor weight is 0 (pub == raw) — so the card
    shows no redundant "model" annotation and nothing changes live by default.
    Returns ``(headline_value, anchored)``."""
    v = game.get(pub_key)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return raw, False
    if raw is not None and pd.notna(raw) and abs(float(v) - float(raw)) < 1e-9:
        return raw, False
    return v, True


def _best_edge(game: dict) -> tuple[str, float] | None:
    """Largest positive model edge on a game, as (label, ev)."""
    cands = []
    for r in _game_edges("", game):
        if r["ev"] is not None and pd.notna(r["ev"]):
            cands.append((r["bet"], float(r["ev"])))
    cands = [c for c in cands if c[1] > 0]
    return max(cands, key=lambda c: c[1]) if cands else None


def _edge_label(g: dict) -> str:
    be = _best_edge(g)
    return f"{be[0]} {be[1] * 100:+.1f}%" if be else "—"


def soccer_table(games) -> pd.DataFrame:
    """Display table for a soccer slate: matchup, expected goals, 1X2 %, O/U 2.5,
    the market money line (home/draw/away) and the best model edge. Odds columns
    read '—' until The Odds API covers the match."""
    rows = []
    for g in games or []:
        rows.append({
            "Match": f"{g.get('away_team')} @ {g.get('home_team')}",
            "Time": fmt_time_et(g.get("game_time")),
            "xG H-A": f"{_num(g.get('home_exp'))}–{_num(g.get('away_exp'))}",
            "Home%": _pct(g.get("home_win_prob")),
            "Draw%": _pct(g.get("draw_prob")),
            "Away%": _pct(g.get("away_win_prob")),
            "Over 2.5%": _pct(g.get("over_2_5")),
            "ML H/D/A": " / ".join(fmt_american(g.get(k))
                                   for k in ("home_ml", "draw_ml", "away_ml")),
            "Best edge": _edge_label(g),
        })
    return pd.DataFrame(rows)


def tennis_table(games) -> pd.DataFrame:
    """Display table for a tennis slate: matchup, tournament, surface, each
    player's model win %, the market money line, sample sizes, best edge."""
    rows = []
    for g in games or []:
        rows.append({
            "Match": f"{g.get('player1')} vs {g.get('player2')}",
            "Tournament": g.get("tournament"),
            "Surface": str(g.get("surface") or "").title() or "—",
            "P1 win%": _pct(g.get("player1_win_prob")),
            "P2 win%": _pct(g.get("player2_win_prob")),
            "ML P1/P2": f"{fmt_american(g.get('p1_price'))} / "
                        f"{fmt_american(g.get('p2_price'))}",
            "Matches P1/P2": f"{g.get('p1_matches', '—')}/{g.get('p2_matches', '—')}",
            "Best edge": _edge_label(g),
        })
    return pd.DataFrame(rows)


def match_edge_table(sport: str, g: dict) -> pd.DataFrame:
    """The priced sides behind a soccer/tennis match (Sharp Sheet body): market,
    bet, model %, price, EV. Empty when no odds have arrived yet."""
    rows = []
    for r in _game_edges(sport, g):
        rows.append({
            "Market": r["market"], "Bet": r["bet"],
            "Model%": _pct(r["model_prob"]),
            "Price": fmt_american(r["price"]),
            "EV%": (f"{r['ev'] * 100:+.1f}"
                    if r["ev"] is not None and pd.notna(r["ev"]) else "—"),
        })
    return pd.DataFrame(rows)


def lineup_status(sport: str, g: dict) -> dict:
    """Confirmation readiness of a game — the latency signal that props are
    live and the market is about to move. {state, label} where state is
    'confirmed' / 'partial' / 'pending'."""
    lu = g.get("lineups") or {}
    home, away = lu.get("home") or [], lu.get("away") or []
    if len(home) >= 9 and len(away) >= 9:
        return {"state": "confirmed", "label": "Lineups confirmed"}
    if sport == "MLB" and g.get("home_pitcher") and g.get("away_pitcher"):
        return {"state": "partial", "label": "Pitchers set · lineups pending"}
    if home or away:
        return {"state": "partial", "label": "Partial lineups"}
    return {"state": "pending", "label": "Lineups pending"}


_STATUS_COLOR = {"confirmed": "var(--good)", "partial": "var(--mid)", "pending": "var(--faint)"}


def _status_badge(sport: str, g: dict) -> str:
    s = lineup_status(sport, g)
    c = _STATUS_COLOR[s["state"]]
    dot = "●" if s["state"] == "confirmed" else "○"
    return (f"<span style='color:{c};font-size:0.72rem;font-weight:600;'>"
            f"{dot} {s['label']}</span>")


def game_card_html(sport: str, g: dict) -> str:
    """A compact matchup card: logos, projected score, win %, line/total,
    and the best model edge. Designed to read at a glance."""
    away, home = g.get("away_team", ""), g.get("home_team", "")
    a_badge = assets.team_badge_html(sport, away, 40)
    h_badge = assets.team_badge_html(sport, home, 40)
    a_exp, h_exp = _exp(g, "away"), _exp(g, "home")
    a_wp, h_wp = g.get("away_win_prob"), g.get("home_win_prob")
    # Published (market-anchored) headline projection, with the raw model number
    # shown alongside (roadmap T3.1 / Component 4). Falls back to the raw column
    # when the *_pub column is absent; the "model" annotation only appears when a
    # nonzero anchor makes pub differ from raw, so the default (weight 0) card is
    # unchanged.
    h_wp_pub, h_anch = _pub(g, "home_win_prob_pub", h_wp)
    a_wp_pub = ((1 - float(h_wp_pub)) if (h_anch and h_wp_pub is not None
                                          and pd.notna(h_wp_pub)) else a_wp)
    time = fmt_time_et(g.get("game_time"))
    proj_total_raw = g.get("proj_total")
    proj_total_pub, tot_anch = _pub(g, "proj_total_pub", proj_total_raw)
    total = g.get("total_line") or proj_total_pub

    hml, aml = g.get("home_ml"), g.get("away_ml")
    odds_bits = []
    if aml is not None and pd.notna(aml) and hml is not None and pd.notna(hml):
        odds_bits.append(f"ML {fmt_american(aml)} / {fmt_american(hml)}")
    sp = g.get("rl_home_line", g.get("spread_home_line"))
    if sp is not None and pd.notna(sp):
        sp_o = g.get("rl_home_odds", g.get("spread_home_odds"))
        odds_bits.append(f"{'RL' if 'rl_home_line' in g else 'Spread'} "
                         f"{sp:+g} {fmt_american(sp_o)}")
    market_line = (f"<div style='color:var(--muted);font-size:0.76rem;margin-top:6px;'>"
                   f"{' · '.join(odds_bits)}{_weather_txt(g)}</div>"
                   if (odds_bits or g.get('weather')) else "")

    edge = _best_edge(g)
    if edge:
        edge_html = (f"<span style='color:var(--good);font-weight:600;'>"
                     f"▲ {edge[0]} · +{edge[1] * 100:.1f}% EV</span>")
    else:
        edge_html = "<span style='color:var(--muted);'>no edge ≥ threshold</span>"

    gpk = g.get("game_pk")

    def side(badge, name, exp, wp, fav, pitcher=None, wp_model=None):
        weight = "700" if fav else "500"
        model_wp = (f" · <span style='opacity:0.7;'>model {_pct(wp_model)}</span>"
                    if wp_model is not None and pd.notna(wp_model) else "")
        sp_row = (f"<div style='color:var(--muted);font-size:0.74rem;margin-top:1px;'>"
                  f"⚾ {player_link(pitcher, gpk, sport)}</div>"
                  if pitcher and pd.notna(pitcher) else "")
        framed = (f"<span style='display:inline-flex;border-radius:11px;padding:2px;"
                  f"background:var(--bg);border:2px solid rgba(255,255,255,0.78);"
                  f"box-shadow:0 0 0 1px #000,0 2px 6px rgba(0,0,0,0.5);'>{badge}</span>")
        return (
            f"<div style='display:flex;align-items:center;gap:10px;flex:1;'>"
            f"{framed}"
            f"<div><div style='font-weight:{weight};font-size:0.95rem;'>{name}</div>"
            f"<div style='color:var(--muted);font-size:0.8rem;'>win {_pct(wp)}{model_wp}</div>"
            f"{sp_row}</div>"
            f"<div style='margin-left:auto;font-size:1.5rem;font-weight:700;'>"
            f"{_num(exp)}</div></div>"
        )

    home_fav = (h_wp or 0) >= (a_wp or 0)
    tot_model_html = (f" · <span style='opacity:0.7;'>model {_num(proj_total_raw)}</span>"
                      if tot_anch else "")
    return (
        "<div style='background:var(--card);border:1px solid var(--line);border-radius:14px;"
        "padding:14px 16px;margin-bottom:12px;"
        "box-shadow:0 1px 2px rgba(0,0,0,0.4),0 8px 22px rgba(0,0,0,0.4);'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin-bottom:8px;'>"
        f"<span style='color:var(--muted);font-size:0.78rem;'>"
        f"{time} · O/U {_num(total)} · proj total {_num(proj_total_pub)}"
        f"{tot_model_html}</span>"
        f"{_status_badge(sport, g)}</div>"
        f"{side(a_badge, away, a_exp, a_wp_pub, not home_fav, g.get('away_pitcher'), a_wp if h_anch else None)}"
        "<div style='height:8px;'></div>"
        f"{side(h_badge, home, h_exp, h_wp_pub, home_fav, g.get('home_pitcher'), h_wp if h_anch else None)}"
        f"{market_line}"
        "<div style='border-top:1px solid var(--line);margin-top:10px;padding-top:8px;"
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
        return "<span style='color:var(--faint);font-size:0.72rem;'>—</span>"
    rank = int(rank)
    third = max(1, n_teams / 3)
    color = "var(--good)" if rank <= third else ("var(--mid)" if rank <= 2 * third else "var(--neg)")
    return (f"<span style='color:{color};font-size:0.72rem;font-weight:600;'>"
            f"{rank}</span>")


def _adv_badge(adv: int) -> str:
    """Center advantage marker: filled green chevrons when the offense
    out-ranks the defense it faces, a muted dash otherwise."""
    if not adv:
        return "<span style='color:var(--faint);'>·</span>"
    return (f"<span style='color:var(--bg);background:var(--good);border-radius:5px;"
            f"padding:1px 5px;font-size:0.66rem;font-weight:800;'>"
            f"{'▲' * adv}</span>")


def _stat_table_html(title: str, rows: list[dict], n_teams: int,
                     off_label: str = "OFF", def_label: str = "DEF") -> str:
    """Mirrored split table: the offense team's spread on the left, the
    opposing defense's spread mirrored on the right, advantage in the
    middle. Columns each side: Season · situational (home/away) · L10 · L5 ·
    rank."""
    osl = (rows[0].get("off_situ_label") if rows else None) or off_label
    dsl = (rows[0].get("def_situ_label") if rows else None) or def_label
    th = "text-align:right;padding:3px 5px;"
    head = (
        "<tr style='color:var(--muted);font-size:0.62rem;text-transform:uppercase;"
        "letter-spacing:0.3px;'>"
        "<th style='text-align:left;padding:3px 6px;'>Stat</th>"
        f"<th style='{th}'>Szn</th><th style='{th}'>{osl}</th>"
        f"<th style='{th}'>L10</th><th style='{th}'>L5</th><th>Rk</th>"
        "<th style='width:30px;'>Adv</th>"
        f"<th>Rk</th><th style='{th}'>L5</th><th style='{th}'>L10</th>"
        f"<th style='{th}'>{dsl}</th><th style='{th}'>Szn</th></tr>"
    )
    body = []
    for r in rows:
        s = r["stat"]
        muted = "text-align:right;padding:3px 5px;color:var(--muted);"
        strong = "text-align:right;padding:3px 5px;font-weight:700;"
        norm = "text-align:right;padding:3px 5px;"
        body.append(
            "<tr style='border-top:1px solid var(--line);'>"
            f"<td style='text-align:left;padding:3px 6px;font-weight:600;'>{s}</td>"
            f"<td style='{muted}'>{_fmt_stat(s, r.get('off_season'))}</td>"
            f"<td style='{norm}'>{_fmt_stat(s, r.get('off_situ'))}</td>"
            f"<td style='{norm}'>{_fmt_stat(s, r.get('off_l10'))}</td>"
            f"<td style='{strong}'>{_fmt_stat(s, r.get('off_l5'))}</td>"
            f"<td style='text-align:center;'>{_rank_badge(r.get('off_rank'), n_teams)}</td>"
            f"<td style='text-align:center;'>{_adv_badge(r.get('adv', 0))}</td>"
            f"<td style='text-align:center;'>{_rank_badge(r.get('def_rank'), n_teams)}</td>"
            f"<td style='{strong}'>{_fmt_stat(s, r.get('def_l5'))}</td>"
            f"<td style='{norm}'>{_fmt_stat(s, r.get('def_l10'))}</td>"
            f"<td style='{norm}'>{_fmt_stat(s, r.get('def_situ'))}</td>"
            f"<td style='{muted}'>{_fmt_stat(s, r.get('def_season'))}</td></tr>"
        )
    return (
        f"<div style='font-size:0.74rem;color:var(--acc2);font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.4px;margin:12px 0 2px;'>{title}</div>"
        "<table style='width:100%;border-collapse:collapse;font-size:0.82rem;'>"
        f"{head}{''.join(body)}</table>"
    )


def _conviction(ev) -> float:
    """Map a model edge (EV) to a 0–10 conviction score: roughly one point
    per percentage point of edge, capped at 10. Negative edge → 0."""
    if ev is None or pd.isna(ev):
        return 0.0
    return round(min(10.0, max(0.0, float(ev) * 100)), 1)


def _conv_color(score: float) -> str:
    return "var(--good)" if score >= 6 else "var(--mid)" if score >= 3 else "var(--neg)"


def market_convictions(g: dict) -> dict:
    """Per-market lean + conviction for the dials and the analysis footer.
    Returns {label: {"side": str, "score": float, "ev": float|None}} for
    Moneyline, Run Line/Spread, and Total."""
    home, away = g.get("home_team", ""), g.get("away_team", "")
    out: dict = {}

    hwp = g.get("home_win_prob") or 0
    if hwp >= 0.5:
        ev = g.get("home_ml_ev", g.get("home_ev"))
        side = f"{home.split()[-1]} {fmt_american(g.get('home_ml'))}".strip()
    else:
        ev = g.get("away_ml_ev", g.get("away_ev"))
        side = f"{away.split()[-1]} {fmt_american(g.get('away_ml'))}".strip()
    out["Moneyline"] = {"side": side or "—", "score": _conviction(ev), "ev": ev}

    sp_line = g.get("rl_home_line", g.get("spread_home_line"))
    sp_label = "Run Line" if "rl_home_line" in g else "Spread"
    eh = g.get("rl_home_ev", g.get("spread_home_ev"))
    ea = g.get("rl_away_ev", g.get("spread_away_ev"))
    best = max([e for e in (eh, ea) if e is not None and pd.notna(e)], default=None)
    if sp_line is not None and pd.notna(sp_line):
        side = (f"{home.split()[-1]} {sp_line:+g}" if best == eh
                else f"{away.split()[-1]} {-sp_line:+g}")
    else:
        side = "—"
    out[sp_label] = {"side": side, "score": _conviction(best), "ev": best}

    oe, ue = g.get("over_ev"), g.get("under_ev")
    best = max([e for e in (oe, ue) if e is not None and pd.notna(e)], default=None)
    line = g.get("total_line")
    if line is not None and pd.notna(line):
        side = f"{'Over' if best == oe else 'Under'} {line:g}"
    else:
        side = "—"
    out["Total"] = {"side": side, "score": _conviction(best), "ev": best}
    return out


def _conviction_dial(label: str, side: str, score: float) -> str:
    """A conic-gradient ring filled to score/10, the number in the middle,
    colored by conviction — the at-a-glance read the mockups lead with."""
    color = _conv_color(score)
    pct = max(0.0, min(100.0, score * 10))
    return (
        "<div style='flex:1;text-align:center;padding:4px 6px;'>"
        f"<div style='color:var(--muted);font-size:0.66rem;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.4px;margin-bottom:4px;'>{label}</div>"
        f"<div style='width:62px;height:62px;border-radius:50%;margin:0 auto;"
        f"background:conic-gradient({color} {pct}%, var(--line) {pct}% 100%);"
        f"display:flex;align-items:center;justify-content:center;'>"
        f"<div style='width:48px;height:48px;border-radius:50%;background:var(--card2);"
        f"display:flex;align-items:center;justify-content:center;"
        f"font-size:1.15rem;font-weight:800;color:{color};'>{score:g}</div></div>"
        f"<div style='font-size:0.74rem;font-weight:600;margin-top:4px;'>{side}</div>"
        "</div>"
    )


def _form_html(badge: str, team: str, form: dict, align: str,
               extra: str = "") -> str:
    """A team block for the header: bordered logo, full team name, W-L record
    + streak, last-5 result chips (green win / red loss), and an optional extra
    row (e.g. the probable starting pitcher)."""
    rec = ""
    if form:
        streak = f" · {form['streak']}" if form.get("streak") else ""
        rec = (f"<div style='color:var(--muted);font-size:0.74rem;margin-top:2px;'>"
               f"{form.get('w', 0)}–{form.get('l', 0)}{streak}</div>")
    chips = ""
    for r in (form or {}).get("last5", []):
        c = "var(--good)" if r["win"] else "var(--neg)"
        chips += (f"<span title='{r.get('opp', '')} {r.get('score', '')}' "
                  f"style='display:inline-block;width:15px;height:15px;border-radius:4px;"
                  f"background:{c};margin:0 1px;'></span>")
    chips_html = (f"<div style='margin-top:5px;text-align:{align};'>{chips}</div>"
                  if chips else "")
    # bordered logo — the framed look that reads as premium on a dark sheet
    logo = (f"<span style='display:inline-flex;border-radius:13px;padding:2px;"
            f"background:var(--bg);border:2px solid rgba(255,255,255,0.78);"
            f"box-shadow:0 0 0 1px #000,0 3px 8px rgba(0,0,0,0.5);'>{badge}</span>")
    name = f"<span style='font-weight:700;font-size:1.05rem;'>{team}</span>"
    name_row = f"{name}{logo}" if align == "right" else f"{logo}{name}"
    return (
        f"<div style='flex:1;'>"
        f"<div style='display:flex;align-items:center;gap:10px;"
        f"justify-content:flex-{'end' if align == 'right' else 'start'};'>{name_row}</div>"
        f"<div style='text-align:{align};'>{rec}</div>{chips_html}{extra}</div>"
    )


# ---------------------------------------------------------------------------
# Redesign helpers: odds/info bar, confidence chip, letter-graded verdicts
# ---------------------------------------------------------------------------

def _letter(ev) -> str:
    """Just the letter grade for a model edge (EV). A = strong, F = negative.
    Kept separate from the tier ladder so play_tier can attach a letter without
    re-deriving the tier from it (and vice versa)."""
    if ev is None or (isinstance(ev, float) and pd.isna(ev)):
        return "—"
    e = float(ev) * 100
    if e >= 8:
        return "A"
    if e >= 5:
        return "B"
    if e >= 3:
        return "C"
    if e >= 1:
        return "D"
    if e >= 0:
        return "D-"
    return "F"


_TIER_LABEL = {"pass": ("PASS", "var(--faint)"), "lean": ("LEAN", "var(--mid)"),
               "core": ("CORE PLAY", "var(--good)"), "watch": ("WATCH", "var(--mid)"),
               "verify": ("VERIFY", "var(--neg)"), "gated": ("GATED", "var(--faint)")}


def play_tier(ev, min_edge: float = 0.02, gate: str | None = None) -> dict:
    """The single source of truth for confidence — collapses the old competing
    systems (the A–F letter grade, the 0–10 conviction, and the 2–6% "band")
    into one ladder. Returns {"label", "color", "kind", "letter", "gate"}.

    ``gate`` is the market's demonstrated-edge status (edge_gate: cleared /
    probation / gated). It CAPS the tier: a market we've proven no edge in
    (GATED) can never be more than PASS no matter the EV; an unproven (PROBATION)
    market caps at LEAN (never a headline CORE PLAY). A too-large-edge VERIFY
    warning is preserved regardless — it's a caution, not a recommendation.

    The anti-guru twist: a *huge* edge is a WARNING, not a lock. On these
    efficient markets a >=8% model edge almost always means the market knows
    something we don't (injury, lineup, bullpen), so we flag it rather than
    chase it. Bands track project547.config (SHARP_EV_MIN / SHARP_EV_MAX /
    STALE_EV) so the dashboard and the ladder never drift apart.

        ev < min_edge              -> PASS       (faint)
        min_edge <= ev < SHARP_MIN -> LEAN       (mid)   [only when min_edge<SHARP_MIN]
        SHARP_MIN <= ev < SHARP_MAX-> CORE PLAY  (good)  [the curated 2–6% band]
        SHARP_MAX <= ev < STALE    -> WATCH      (mid)
        ev >= STALE                -> VERIFY      (neg)   [edge too big, market knows]
    """
    sharp_min = config.SHARP_EV_MIN   # 0.02 — bottom of the curated band
    sharp_max = config.SHARP_EV_MAX   # 0.06 — top of the validated sweet spot
    stale = config.STALE_EV           # 0.08 — above this the line's likely stale
    letter = _letter(ev)
    if ev is None or (isinstance(ev, float) and pd.isna(ev)):
        kind = "pass"
    else:
        e = float(ev)
        if e < min_edge:
            kind = "pass"
        # LEAN only exists when the caller's bar sits below the curated band.
        elif min_edge < sharp_min and e < sharp_min:
            kind = "lean"
        elif e < sharp_max:
            kind = "core"
        elif e < stale:
            kind = "watch"
        else:
            kind = "verify"

    display_kind = kind
    if gate is not None:
        from project547 import edge_gate
        capped = edge_gate.cap_tier(kind, gate)
        # show a distinct GATED label when a no-edge market forced the cap
        display_kind = "gated" if (gate == edge_gate.GATED and kind != "verify"
                                   and capped == "pass") else capped
    label, color = _TIER_LABEL[display_kind]
    return {"label": label, "color": color,
            "kind": "pass" if display_kind == "gated" else display_kind,
            "letter": letter, "gate": gate}


def _tier_color(letter: str) -> str:
    """The letter-chip color, derived from the grade alone (A/B win, C/D
    caution, F loss). Keeps the small grade badge readable independent of the
    tier label."""
    if letter in ("A", "B"):
        return "var(--good)"
    if letter in ("C", "D"):
        return "var(--mid)"
    if letter == "F":
        return "var(--neg)"
    return "var(--faint)"


def _grade(ev) -> tuple[str, str]:
    """Letter grade + color for a model edge (EV). A = strong, F = negative.
    Thin wrapper over _letter/_tier_color — preserved for callers that want the
    (letter, color) tuple."""
    letter = _letter(ev)
    return (letter, _tier_color(letter))


def _info_bar_html(sport: str, g: dict) -> str:
    """The top odds/info strip: date · time · moneyline · total. Bleeds to the
    card edges. Honest about what we have — no ballpark field exists yet."""
    segs = []
    t = g.get("game_time")
    if t:
        try:
            dt = datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(ET)
            segs.append(f"<b>{dt.strftime('%a %b %-d')}</b> · "
                        f"{dt.strftime('%-I:%M %p')} ET")
        except (ValueError, TypeError):
            pass
    aml, hml = g.get("away_ml"), g.get("home_ml")
    a, h = g.get("away_team", ""), g.get("home_team", "")
    if (aml is not None and pd.notna(aml) and hml is not None and pd.notna(hml)
            and a and h):
        segs.append(f"ML <b>{a.split()[-1]} {fmt_american(aml)} / "
                    f"{h.split()[-1]} {fmt_american(hml)}</b>")
    tl = g.get("total_line")
    if tl is not None and pd.notna(tl):
        segs.append(f"Total <b>{tl:g}</b>")
    if not segs:
        return ""
    cells = "".join(
        "<span style='padding:7px 14px;border-right:1px solid rgba(255,255,255,0.1);"
        f"white-space:nowrap;'>{s}</span>" for s in segs)
    return ("<div style='background:linear-gradient(90deg,#0f2a4d,#1b4f8c);"
            "color:#cfe2f7;display:flex;flex-wrap:wrap;font-size:0.73rem;"
            "border-radius:16px 16px 0 0;margin:-16px -18px 12px;'>" + cells + "</div>")


_CONF_MAP = {
    "confirmed": ("1.00", "Lineups confirmed, both starters set, no rainout flag"),
    "partial": ("0.70", "Pitchers set; lineups still posting"),
    "pending": ("0.45", "Lineups & inputs still firming up — numbers may move"),
}


def _conf_chip(sport: str, g: dict) -> str:
    """Confidence read derived from lineup-confirmation readiness, with a
    hover tooltip explaining the range (native title attribute)."""
    state = lineup_status(sport, g)["state"]
    val, desc = _CONF_MAP.get(state, _CONF_MAP["pending"])
    return (f"<span title='Confidence {val} — {desc}.' "
            f"style='cursor:help;border-bottom:1px dotted var(--faint);'>"
            f"Confidence <b style='color:var(--text);'>{val}</b></span>")


def _verdict_rows(g: dict) -> list[tuple]:
    """Per-market model pick for the verdict boxes: (label, pick, prob, ev).
    Uses only fields the slate actually carries; markets without data drop."""
    rows: list[tuple] = []
    home, away = g.get("home_team", ""), g.get("away_team", "")

    def short(t):
        return t.split()[-1] if t else t

    hwp, awp = g.get("home_win_prob"), g.get("away_win_prob")
    if hwp is not None and pd.notna(hwp):
        if (hwp or 0) >= (awp or 0):
            rows.append(("Moneyline", f"{short(home)} {fmt_american(g.get('home_ml'))}",
                         hwp, g.get("home_ml_ev", g.get("home_ev"))))
        else:
            rows.append(("Moneyline", f"{short(away)} {fmt_american(g.get('away_ml'))}",
                         awp, g.get("away_ml_ev", g.get("away_ev"))))

    sp = g.get("rl_home_line", g.get("spread_home_line"))
    cover = g.get("model_home_rl", g.get("model_home_cover"))
    eh = g.get("rl_home_ev", g.get("spread_home_ev"))
    ea = g.get("rl_away_ev", g.get("spread_away_ev"))
    # only show the run-line/spread box when cover is a clean scalar probability
    # (some slates carry home_rl_cover as a per-line dict — skip rather than guess)
    if (sp is not None and pd.notna(sp)
            and isinstance(cover, (int, float)) and pd.notna(cover)):
        label = "Run Line" if "rl_home_line" in g else "Spread"
        if (eh if eh is not None else -9) >= (ea if ea is not None else -9):
            rows.append((label, f"{short(home)} {sp:+g}", cover, eh))
        else:
            rows.append((label, f"{short(away)} {-sp:+g}", 1 - cover, ea))

    line, mop = g.get("total_line"), g.get("model_over_prob")
    oe, ue = g.get("over_ev"), g.get("under_ev")
    if line is not None and pd.notna(line) and mop is not None and pd.notna(mop):
        have_ev = (oe is not None and pd.notna(oe)) or (ue is not None and pd.notna(ue))
        if have_ev:
            over = (oe if (oe is not None and pd.notna(oe)) else -9) >= \
                   (ue if (ue is not None and pd.notna(ue)) else -9)
        else:
            # no EV to lean on — show the side the model itself favors, so the
            # gauge always points at the model's pick (never a sub-50% "pick")
            over = mop >= 0.5
        if over:
            rows.append(("Total", f"Over {line:g}", mop, oe))
        else:
            rows.append(("Total", f"Under {line:g}", 1 - mop, ue))
    return rows


def _gauge_svg(prob, needle_color: str = "var(--text)") -> str:
    """A semicircular speedometer: red→amber→green arc with a needle pointing
    to ``prob`` (0–1). Right side (high probability) is green = strong pick."""
    if prob is None or (isinstance(prob, float) and pd.isna(prob)):
        prob = 0.0
    prob = max(0.0, min(1.0, float(prob)))
    cx, cy, R = 50.0, 47.0, 38.0

    def pt(v, r=R):
        a = math.radians(180.0 - v * 180.0)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def seg(v1, v2, col):
        x1, y1 = pt(v1)
        x2, y2 = pt(v2)
        return (f"<path d='M {x1:.2f} {y1:.2f} A {R} {R} 0 0 1 {x2:.2f} {y2:.2f}' "
                f"fill='none' stroke='{col}' stroke-width='8'/>")

    arcs = (seg(0.0, 0.5, "var(--neg)") + seg(0.5, 0.62, "var(--mid)")
            + seg(0.62, 1.0, "var(--good)"))
    nx, ny = pt(prob, R - 7)
    needle = (f"<line x1='{cx}' y1='{cy}' x2='{nx:.2f}' y2='{ny:.2f}' "
              f"stroke='{needle_color}' stroke-width='2.6' stroke-linecap='round'/>"
              f"<circle cx='{cx}' cy='{cy}' r='4' fill='{needle_color}'/>")
    return (f"<svg viewBox='0 0 100 54' style='width:100%;max-width:124px;'>"
            f"{arcs}{needle}</svg>")


def _verdict_box(label: str, pick: str, prob, ev, min_edge: float, wm: str = "") -> str:
    tier = play_tier(ev, min_edge)
    gl, gc = tier["letter"], _tier_color(tier["letter"])
    play = tier["kind"] != "pass"
    dco, dbg = ("var(--good)", "#0f2c1c") if play else ("var(--muted)", "var(--line)")
    decision = "PASS" if tier["kind"] == "pass" else tier["label"]
    pctc = ("var(--good)" if (prob is not None and pd.notna(prob) and prob >= 0.62)
            else ("var(--mid)" if (prob is not None and pd.notna(prob) and prob >= 0.5)
                  else "var(--text)"))
    evtxt = f"{ev * 100:+.1f}%" if (ev is not None and pd.notna(ev)) else "—"
    return (
        "<div style='border:1px solid var(--line);border-radius:12px;padding:9px 8px 9px;"
        "text-align:center;background:linear-gradient(180deg,var(--card),var(--card2));'>"
        f"<div style='font-size:0.62rem;text-transform:uppercase;letter-spacing:0.4px;"
        f"color:var(--muted);font-weight:700;margin-bottom:1px;'>{label}</div>"
        f"<div style='position:relative;'>{_gauge_svg(prob, gc)}"
        f"<div style='position:absolute;left:0;right:0;bottom:-2px;font-size:1.25rem;"
        f"font-weight:700;color:{pctc};'>{_pct(prob)}</div></div>"
        f"<div style='font-size:0.76rem;font-weight:600;margin-top:3px;'>{pick}</div>"
        f"<div style='margin-top:4px;display:flex;gap:6px;justify-content:center;"
        f"align-items:center;'>"
        f"<span style='display:inline-flex;width:20px;height:20px;border-radius:6px;"
        f"background:{gc};color:var(--bg);font-size:0.72rem;font-weight:800;"
        f"align-items:center;justify-content:center;'>{gl}</span>"
        f"<span style='font-size:0.63rem;font-weight:700;padding:1px 6px;border-radius:5px;"
        f"color:{dco};background:{dbg};'>{decision}</span>"
        f"<span style='font-size:0.66rem;color:var(--faint);'>EV {evtxt}</span></div></div>"
    )


# ---------------------------------------------------------------------------
# The Daily Docket — calm, sharp matchup card (cream/vintage).
# ① Who's the better side?  ② Is the price fair?  ③ What's the play?
# ---------------------------------------------------------------------------

def _ring(prob, color: str) -> str:
    """A calm conviction ring filled to prob, the % in the middle."""
    p = 0 if (prob is None or pd.isna(prob)) else max(0, min(100, round(float(prob) * 100)))
    return (
        f"<div style='width:72px;height:72px;border-radius:50%;margin:0 auto 5px;"
        f"background:conic-gradient({color} {p}%, var(--line) 0);"
        f"display:flex;align-items:center;justify-content:center;'>"
        f"<div style='width:54px;height:54px;border-radius:50%;background:var(--card);"
        f"display:flex;align-items:center;justify-content:center;font-family:var(--disp);"
        f"font-weight:600;font-size:1.05rem;color:var(--text);'>{_pct(prob)}</div></div>")


def _gauge_color(prob) -> str:
    if prob is None or pd.isna(prob):
        return "var(--faint)"
    if prob >= 0.60:
        return "var(--good)"
    if prob >= 0.52:
        return "var(--mid)"
    return "var(--faint)"


def _why(text: str) -> str:
    return (
        "<div style='display:flex;gap:10px;background:var(--card2);"
        "border-left:3px solid var(--acc2);border-radius:0 5px 5px 0;"
        "padding:9px 13px;margin-top:11px;'>"
        "<span style='font-family:var(--disp);font-size:.62rem;letter-spacing:.1em;"
        "color:var(--acc2);flex:0 0 auto;padding-top:1px;'>WHY</span>"
        f"<span style='font-size:.84rem;color:var(--text);line-height:1.45;'>{text}</span></div>")


_DTAG = {
    "lean": "background:var(--card2);color:var(--text);border:1px solid var(--line);",
    "edge": "background:rgba(47,122,74,.14);color:var(--good);border:1px solid rgba(47,122,74,.35);",
    "play": "background:var(--good);color:var(--bg);",
    "pass": "background:var(--card2);color:var(--muted);border:1px solid var(--line);",
    "warn": "background:rgba(200,148,26,.16);color:var(--mid);border:1px solid rgba(200,148,26,.4);",
}


def _qhead(num: int, q: str, tag_text: str = "", tag_kind: str = "lean") -> str:
    tag = (f"<span style='margin-left:auto;font-family:var(--disp);font-size:.64rem;"
           f"letter-spacing:.06em;padding:3px 9px;border-radius:3px;"
           f"{_DTAG.get(tag_kind, _DTAG['lean'])}'>{tag_text}</span>") if tag_text else ""
    return (
        "<div style='display:flex;align-items:center;gap:10px;margin-bottom:12px;'>"
        "<span style='width:22px;height:22px;border-radius:50%;background:var(--text);"
        "color:var(--bg);font-family:var(--disp);font-weight:600;font-size:.8rem;"
        f"display:flex;align-items:center;justify-content:center;flex:0 0 auto;'>{num}</span>"
        f"<span style='font-family:var(--disp);font-weight:600;font-size:1rem;"
        f"letter-spacing:.02em;'>{q}</span>{tag}</div>")


def _docket_team(badge: str, team: str, form: dict, align: str, extra: str = "") -> str:
    rec = ""
    if form:
        streak = f" · {form['streak']}" if form.get("streak") else ""
        rec = (f"<div style='font-size:0.72rem;color:var(--muted);'>"
               f"{form.get('w', 0)}–{form.get('l', 0)}{streak}</div>")
    chips = ""
    for r in (form or {}).get("last5", []):
        c = "var(--good)" if r["win"] else "var(--neg)"
        chips += (f"<span title='{r.get('opp','')} {r.get('score','')}' "
                  f"style='display:inline-block;width:13px;height:13px;border-radius:3px;"
                  f"background:{c};margin:0 1px;'></span>")
    chips = (f"<div style='margin-top:4px;text-align:{align};'>{chips}</div>"
             if chips else "")
    frame = (f"<span style='display:inline-flex;border-radius:11px;padding:2px;"
             f"background:var(--bg);border:1.5px solid var(--text);'>{badge}</span>")
    name = (f"<div style='font-family:var(--disp);font-weight:600;font-size:1.2rem;"
            f"letter-spacing:.03em;'>{team.split()[-1]}</div>")
    nm = (f"<div>{name}{rec}</div>{frame}" if align == "right"
          else f"{frame}<div>{name}{rec}</div>")
    return (
        f"<div style='flex:1;'><div style='display:flex;align-items:center;gap:11px;"
        f"justify-content:flex-{'end' if align == 'right' else 'start'};'>{nm}</div>"
        f"<div style='text-align:{align};'>{chips}{extra}</div></div>")


def research_card_html(sport: str, g: dict, matchup: dict, min_edge: float = 0.02) -> str:
    away, home = g.get("away_team", ""), g.get("home_team", "")
    a_badge = assets.team_badge_html(sport, away, 34)
    h_badge = assets.team_badge_html(sport, home, 34)
    n = matchup.get("n_teams", 30)
    gpk = g.get("game_pk")

    def _sp(name, align):
        if not name or (isinstance(name, float) and pd.isna(name)):
            return ""
        return (f"<div style='text-align:{align};font-size:0.72rem;color:var(--muted);"
                f"margin-top:5px;'>⚾ {player_link(name, gpk, sport)}</div>")

    # ---- header: teams + records + last-5, line + proj down the middle ----
    line_bits = []
    aml, hml = g.get("away_ml"), g.get("home_ml")
    if aml is not None and pd.notna(aml) and hml is not None and pd.notna(hml):
        line_bits.append(f"ML <b>{away.split()[-1]} {fmt_american(aml)} / "
                         f"{home.split()[-1]} {fmt_american(hml)}</b>")
    if g.get("total_line") is not None and pd.notna(g.get("total_line")):
        line_bits.append(f"Total <b>{g['total_line']:g}</b>")
    wx = _weather_txt(g).lstrip(" ·")
    if wx:
        line_bits.append(wx)
    header = (
        "<div style='display:flex;align-items:center;gap:14px;'>"
        + _docket_team(a_badge, away, matchup.get("away_form") or {}, "right",
                       _sp(g.get("away_pitcher"), "right"))
        + ("<div style='flex:0 0 auto;text-align:center;min-width:120px;'>"
           f"<div style='font-family:var(--disp);font-size:.6rem;letter-spacing:.1em;"
           f"color:var(--faint);'>PROJECTED</div>"
           f"<div style='font-family:var(--disp);font-weight:600;font-size:1.5rem;'>"
           f"{_num(_exp(g,'away'))} – {_num(_exp(g,'home'))}</div>"
           f"<div style='font-size:.66rem;color:var(--muted);'>"
           f"{fmt_time_et(g.get('game_time'))}</div></div>")
        + _docket_team(h_badge, home, matchup.get("home_form") or {}, "left",
                       _sp(g.get("home_pitcher"), "left"))
        + "</div>"
        + (f"<div style='display:flex;gap:20px;justify-content:center;margin-top:11px;"
           f"font-size:.74rem;color:var(--muted);'>"
           + "".join(f"<span>{b}</span>" for b in line_bits) + "</div>"
           if line_bits else "")
    )

    # ---- ① who's the better side? — gauges + lean + why ----
    vrows = _verdict_rows(g)
    gauges = "".join(
        f"<div style='text-align:center;'>{_ring(prob, _gauge_color(prob))}"
        f"<div style='font-family:var(--disp);font-size:.6rem;letter-spacing:.07em;"
        f"color:var(--muted);text-transform:uppercase;'>{lbl}</div>"
        f"<div style='font-size:.72rem;font-weight:600;'>{pick}</div></div>"
        for lbl, pick, prob, ev in vrows)
    hwp, awp = g.get("home_win_prob") or 0, g.get("away_win_prob") or 0
    fav = home if hwp >= awp else away
    wp = max(hwp, awp)
    mop = g.get("model_over_prob")
    over = (mop or 0) >= 0.5
    tot = g.get("total_line") or g.get("proj_total")
    lean_tag = f"{fav.split()[-1]} · {'OVER' if over else 'UNDER'} LEAN"
    why1 = (f"Model makes the <b>{fav.split()[-1]} {wp:.0%}</b> to win and projects "
            f"<b>{_num(_exp(g,'away'))}–{_num(_exp(g,'home'))}</b>"
            + (f" — a lean to the <b>{'over' if over else 'under'} {_num(tot)}</b>."
               if mop is not None and pd.notna(mop) else "."))
    sec1 = (_qhead(1, "Who's the better side?", lean_tag, "lean")
            + (f"<div style='display:grid;grid-template-columns:repeat({max(1,len(vrows))},"
               f"1fr);gap:14px;'>{gauges}</div>" if vrows else "")
            + _why(why1))

    # ---- best model edge drives ② and ③ ----
    edges = [e for e in _game_edges("", g)
             if e.get("ev") is not None and pd.notna(e["ev"])]
    best = max(edges, key=lambda e: e["ev"]) if edges else None

    # ---- ② is the price fair? ----
    if best and best.get("price") is not None:
        imp = _implied(best["price"])
        ev = float(best["ev"])
        kind = "edge" if ev >= min_edge else "pass"
        grid = (
            "<div style='display:grid;grid-template-columns:repeat(4,1fr);"
            "border:1.5px solid var(--text);border-radius:6px;overflow:hidden;'>"
            + _pf_cell("MARKET", fmt_american(best["price"]))
            + _pf_cell("IMPLIED", _pct(imp))
            + _pf_cell("OUR MODEL", _pct(best.get("model_prob")), True)
            + _pf_cell("EDGE", f"{ev * 100:+.1f}%", True,
                       "var(--good)" if ev >= 0 else "var(--neg)") + "</div>")
        why2 = (f"We price <b>{best['bet']}</b> at <b>{_pct(best.get('model_prob'))}</b> "
                f"vs the market's <b>{_pct(imp)}</b> — a <b>{ev*100:+.1f}%</b> edge.")
        sec2 = _qhead(2, "Is the price fair?", "EDGE" if kind == "edge" else "EFFICIENT",
                      kind) + grid + _why(why2)
    else:
        sec2 = _qhead(2, "Is the price fair?", "NO LINE", "pass") + _why(
            "No market price posted yet — the docket fills in once the board opens.")

    # ---- ③ what's the play? ----
    if best and float(best["ev"]) >= min_edge:
        ev = float(best["ev"])
        tier = play_tier(ev, min_edge)
        if tier["kind"] == "core":
            tag, kind, vcol = "PLAY · 2–6% BAND", "play", "var(--good)"
            why3 = ("Squarely in our curated <b>2–6% band</b> — the range our record hits "
                    "~60%. Logged at this price and graded to the close. No revisions.")
        elif tier["kind"] == "verify":
            tag, kind, vcol = "VERIFY — OFF-MARKET", "warn", "var(--mid)"
            why3 = ("Edge is large enough that the market likely knows something we don't "
                    "(injury, lineup, bullpen). We flag these — we don't chase them.")
        elif tier["kind"] == "watch":
            tag, kind, vcol = "WATCH", "warn", "var(--mid)"
            why3 = ("Just past the sweet spot — a touch rich for the curated band. "
                    "Worth watching, not yet a core play.")
        else:
            tag, kind, vcol = "LEAN", "edge", "var(--good)"
            why3 = "A modest edge — worth a lean, just under our core play threshold."
        glyph = "▶" if kind != "warn" else "⚠"
        verdict = (
            f"<div style='display:flex;align-items:center;gap:14px;border:1.5px solid "
            f"{vcol};border-radius:6px;padding:12px 16px;"
            f"background:color-mix(in srgb, {vcol} 7%, transparent);'>"
            f"<span style='font-family:var(--disp);font-weight:600;font-size:1.1rem;"
            f"color:{vcol};'>{glyph} {best['bet']}</span>"
            "<div style='margin-left:auto;display:flex;gap:18px;text-align:center;'>"
            + _vmeta("MODEL", _pct(best.get("model_prob")))
            + _vmeta("EV", f"{ev*100:+.1f}%", vcol) + "</div></div>")
    else:
        tag, kind = "NO PLAY", "pass"
        verdict = (
            "<div style='border:1.5px solid var(--line);border-radius:6px;"
            "padding:12px 16px;background:var(--card2);font-family:var(--disp);"
            "font-weight:600;font-size:1rem;color:var(--muted);'>"
            "No flagged edge — priced efficiently. Pass.</div>")
        why3 = ("Our number and the market agree tonight. We only fire when the gap clears "
                "our threshold — discipline is the edge.")
    sec3 = _qhead(3, "Is the price wrong — what's the play?", tag, kind) + verdict + _why(why3)

    # ---- advanced analytics (deep tables + trends + lineups) behind a reveal ----
    off_lbl = ("Batting vs Pitching" if sport == "MLB" else "Offense vs Defense")
    tables = ""
    if matchup.get("away_off_vs_home_def"):
        tables += _stat_table_html(f"{away} {off_lbl}", matchup["away_off_vs_home_def"], n)
    if matchup.get("home_off_vs_away_def"):
        tables += _stat_table_html(f"{home} {off_lbl}", matchup["home_off_vs_away_def"], n)
    tr = matchup.get("trends") or []
    if tr:
        cells = "".join(
            f"<div style='flex:1;text-align:center;'>"
            f"<div style='color:var(--muted);font-size:0.66rem;'>{t['stat']}</div>"
            f"<div style='font-size:0.8rem;'>{_fmt_stat(t['stat']+'%', t['away'])}"
            f" / {_fmt_stat(t['stat']+'%', t['home'])}</div></div>" for t in tr)
        tables += ("<div style='font-size:0.7rem;color:var(--acc2);font-weight:700;"
                   "text-transform:uppercase;margin:10px 0 2px;'>Trends (away / home)</div>"
                   f"<div style='display:flex;gap:6px;'>{cells}</div>")
    reveal = ""
    if tables or _lineups_html(g, sport):
        reveal = (
            "<details style='border:1.5px solid var(--line);border-radius:6px;"
            "margin-top:6px;background:var(--card2);'>"
            "<summary style='cursor:pointer;padding:11px 14px;font-family:var(--disp);"
            "font-size:.82rem;font-weight:600;letter-spacing:.04em;color:var(--acc2);'>"
            "ADVANCED MATCHUP ANALYTICS &amp; LINEUPS →</summary>"
            f"<div style='padding:2px 14px 14px;'>{tables}{_lineups_html(g, sport)}</div></details>")

    secwrap = lambda h: f"<div style='padding:15px 20px;border-top:1.5px solid var(--line);'>{h}</div>"
    return (
        "<div style='background:var(--card);border:1.5px solid var(--text);"
        "border-radius:8px;margin-bottom:16px;overflow:hidden;'>"
        f"<div style='padding:16px 20px;'>{header}</div>"
        f"{secwrap(sec1)}{secwrap(sec2)}{secwrap(sec3)}"
        f"<div style='padding:12px 20px;'>{reveal}</div></div>")


def _pf_cell(label, value, hl=False, color=None) -> str:
    bg = "background:var(--card2);" if hl else ""
    col = f"color:{color};" if color else ""
    # Numbers set in mono, tabular — the "precision instrument" read that makes
    # the card feel like a bet ticket, not a marketing card, and keeps columns aligned.
    return (f"<div style='padding:11px 8px;text-align:center;border-right:1px solid var(--line);{bg}'>"
            f"<div style='font-family:var(--disp);font-size:.58rem;letter-spacing:.06em;"
            f"color:var(--muted);'>{label}</div>"
            f"<div style='font-family:var(--mono);font-weight:600;font-size:1.02rem;"
            f"font-variant-numeric:tabular-nums;margin-top:4px;{col}'>{value}</div></div>")


def _vmeta(label, value, color=None) -> str:
    col = f"color:{color};" if color else ""
    return (f"<div><div style='font-family:var(--disp);font-size:.58rem;letter-spacing:.06em;"
            f"color:var(--muted);'>{label}</div>"
            f"<div style='font-family:var(--disp);font-weight:600;font-size:1rem;{col}'>{value}</div></div>")


# ---------------------------------------------------------------------------
# The reusable play card (Today / Plays / Ledger) — one compact, themed card.
# ---------------------------------------------------------------------------

def _g(play: dict, *keys):
    """First non-null/non-NaN value among `keys` in the play dict, else None.
    Plays come from a few shapes; tolerate missing keys, never raise."""
    for k in keys:
        v = play.get(k)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            return v
    return None


def _signed_pct(v) -> str:
    """0.012 -> '+1.2%', -0.004 -> '-0.4%'. '—' when missing."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v) * 100:+.1f}%"


def _signed_units(v) -> str:
    """0.8 -> '+0.8u', -1.0 -> '-1.0u'. '—' when missing."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v):+.1f}u"


def _fallback_card(title: str, note: str = "card unavailable") -> str:
    """Minimal card shown when a rich renderer raises — keeps the page up."""
    return (f"<div style='background:var(--card);border:1.5px solid var(--line);"
            f"border-radius:12px;padding:14px 16px;color:var(--text);"
            f"font-family:var(--font, system-ui, sans-serif);'>"
            f"<b>{title}</b><br><span style='color:var(--muted);font-size:.72rem;'>"
            f"{note}</span></div>")


def play_card_html(play: dict, *, graded: bool = False) -> str:
    """Never-raises wrapper around the play card: one bad row degrades to a
    minimal card (and logs the real error) instead of crashing the whole page."""
    try:
        return _play_card_impl(play, graded=graded)
    except Exception:
        log.exception("play_card_html failed for %r", (play or {}).get("bet"))
        return _fallback_card(_g(play or {}, "bet", "pick") or "Play")


def _play_card_impl(play: dict, *, graded: bool = False) -> str:
    """One compact, reusable play card (pure st.markdown HTML, themed via CSS
    vars). Used on Today / Plays / Ledger. Pulls every field defensively from
    the play dict (bet/pick, price, model_prob, ev, kelly/stake, sport, game,
    clv, won, pnl) — missing keys degrade to graceful blanks, never raise.

    Layout, top to bottom:
      · kicker: sport chip + matchup/time (time right-aligned)
      · hero: the pick + american price, large (Oswald 700)
      · the MARKET / IMPLIED / MODEL / EDGE 4-cell grid (reuses _pf_cell)
      · tier row: play_tier chip + ¼-Kelly stake + conviction
      · optional one-line WHY ("Model 61%; market implies 57%.")
      · graded=True also shows CLV and the result (✅ Win / ❌ Loss).
    """
    play = play or {}
    bet = _g(play, "bet", "pick") or "—"
    price = _g(play, "price", "odds")
    ev = _g(play, "ev")
    model_prob = _g(play, "model_prob", "model_over_prob")
    sport = _g(play, "sport") or ""
    game = _g(play, "game", "matchup") or ""
    time = fmt_time_et(_g(play, "time", "game_time"))

    tier = play_tier(ev, gate=_g(play, "gate"))
    imp = _implied(price) if (price is not None and pd.notna(price)) else None

    # ---- kicker: sport chip + matchup, time pushed right ----
    chip = (f"<span style='font-family:var(--disp);font-size:.62rem;letter-spacing:.08em;"
            f"padding:2px 8px;border-radius:3px;background:var(--card2);color:var(--muted);"
            f"border:1px solid var(--line);'>{sport}</span>" if sport else "")
    kicker = (
        "<div style='display:flex;align-items:center;gap:9px;margin-bottom:8px;"
        "font-size:.74rem;color:var(--muted);'>"
        f"{chip}<span>{game}</span>"
        f"<span style='margin-left:auto;'>{time}</span></div>")

    # ---- THE PLAY: a brass-tinted ticket band — the answer, up top ----
    price_txt = fmt_american(price) or "—"
    stake = _g(play, "stake", "kelly")
    stake_txt = (f"{float(stake):.1f}u" if (stake is not None and pd.notna(stake))
                 else "—")
    tier_chip = (
        f"<span style='font-family:var(--disp);font-size:.62rem;letter-spacing:.06em;"
        f"font-weight:700;padding:2px 8px;border-radius:4px;color:var(--bg);"
        f"background:{tier['color']};'>{tier['label']}</span>")
    grade_seal = (
        f"<span style='display:inline-flex;width:30px;height:30px;border-radius:8px;"
        f"flex:0 0 auto;background:{_tier_color(tier['letter'])};color:var(--bg);"
        f"font-family:var(--disp);font-size:1.05rem;font-weight:800;"
        f"align-items:center;justify-content:center;'>{tier['letter']}</span>")
    ticket = (
        "<div style='display:flex;align-items:center;gap:11px;margin-bottom:10px;"
        "padding:10px 12px;border-radius:10px;"
        "border:1px solid color-mix(in srgb, var(--acc) 34%, var(--line));"
        "background:color-mix(in srgb, var(--acc) 9%, transparent);'>"
        "<div style='min-width:0;flex:1;'>"
        "<div style='font-family:var(--disp);font-size:.58rem;letter-spacing:.14em;"
        "text-transform:uppercase;font-weight:700;color:var(--acc);margin-bottom:3px;'>"
        "The play</div>"
        f"<div style='font-family:var(--disp);font-weight:700;font-size:1.24rem;"
        f"line-height:1.08;'>{bet}</div>"
        f"<div style='margin-top:6px;'>{tier_chip}</div></div>"
        "<div style='text-align:right;'>"
        f"<div style='font-family:var(--mono);font-weight:700;font-size:1.3rem;"
        f"font-variant-numeric:tabular-nums;line-height:1;'>{price_txt}</div>"
        f"<div style='font-family:var(--mono);font-size:.66rem;color:var(--muted);"
        f"margin-top:4px;'>{stake_txt} · ¼-Kelly</div></div>"
        f"{grade_seal}</div>")

    # ---- the 4-cell MARKET / IMPLIED / MODEL / EDGE grid (reuse _pf_cell) ----
    ev_col = None
    if ev is not None and pd.notna(ev):
        ev_col = "var(--good)" if float(ev) >= 0 else "var(--neg)"
    grid = (
        "<div style='display:grid;grid-template-columns:repeat(4,1fr);"
        "border:1px solid var(--line);border-radius:8px;overflow:hidden;'>"
        + _pf_cell("MARKET", price_txt)
        + _pf_cell("IMPLIED", _pct(imp))
        + _pf_cell("OUR MODEL", _pct(model_prob), True)
        + _pf_cell("EDGE", _signed_pct(ev), True, ev_col) + "</div>")

    # ---- optional one-line WHY, loosely-held numeric voice ----
    why = ""
    if model_prob is not None and imp is not None:
        why = (f"<div style='font-size:.78rem;color:var(--muted);margin-top:9px;'>"
               f"Model {_pct(model_prob)}; market implies {_pct(imp)}.</div>")

    # ---- graded: CLV + result ----
    graded_row = ""
    if graded:
        bits = []
        clv = _g(play, "clv")
        if clv is not None:
            cc = "var(--good)" if float(clv) >= 0 else "var(--neg)"
            bits.append(f"<span>CLV <b style='color:{cc};'>{_signed_pct(clv)}</b></span>")
        won = play.get("won")
        pnl = _g(play, "pnl")
        if won is not None and not (isinstance(won, float) and pd.isna(won)):
            ok = bool(won)
            rc = "var(--good)" if ok else "var(--neg)"
            glyph = "✅ Win" if ok else "❌ Loss"
            ptxt = f" {_signed_units(pnl)}" if pnl is not None else ""
            bits.append(f"<span style='color:{rc};font-weight:600;'>{glyph}{ptxt}</span>")
        if bits:
            graded_row = (
                "<div style='display:flex;align-items:center;gap:14px;margin-top:10px;"
                "padding-top:9px;border-top:1px solid var(--line);font-size:.78rem;'>"
                + "".join(bits) + "</div>")

    # Brass left-rail marks this as the play (the answer-first ticket signature
    # from the Edge Card); soft theme-aware shadow lifts it off the page.
    return (
        "<div style='background:var(--card);border:1px solid var(--line);"
        "border-left:3px solid var(--acc);border-radius:12px;padding:14px 16px 15px;"
        "margin-bottom:12px;box-shadow:0 2px 10px rgba(0,0,0,var(--shadow));'>"
        f"{kicker}{ticket}{grid}{why}{graded_row}</div>")


def _weather_txt(g: dict) -> str:
    w = g.get("weather")
    if not w:
        return ""
    bits = f" · 🌡 {w.get('temp_f')}°F · 💨 {w.get('wind_mph')}mph {w.get('wind_dir', '')}"
    if (w.get("precip_pct") or 0) >= 20:
        bits += f" · 🌧 {w['precip_pct']}%"
    return bits


def _lineups_html(g: dict, sport: str | None = None) -> str:
    lu = g.get("lineups") or {}
    home, away = lu.get("home") or [], lu.get("away") or []
    if not home and not away:
        return ""
    gpk = g.get("game_pk")

    def col(team, names):
        rows = "".join(
            f"<div style='font-size:0.8rem;padding:2px 0;'>{i+1}. "
            f"{player_link(n, gpk, sport)}</div>"
            for i, n in enumerate(names[:9]))
        return (f"<div style='flex:1;'><div style='color:var(--muted);font-size:0.72rem;"
                f"font-weight:700;text-transform:uppercase;margin-bottom:3px;'>"
                f"{team}</div>{rows or '—'}</div>")

    return ("<div style='border-top:1px solid var(--line);margin-top:12px;padding-top:10px;'>"
            "<div style='font-size:0.78rem;color:var(--acc2);font-weight:700;"
            "text-transform:uppercase;margin-bottom:5px;'>Lineups "
            "<span style='color:var(--faint);font-weight:400;text-transform:none;'>"
            "— click a name for the player panel</span></div>"
            f"<div style='display:flex;gap:18px;'>{col(g.get('away_team',''), away)}"
            f"{col(g.get('home_team',''), home)}</div></div>")


def matchup_analysis(sport: str, g: dict, matchup: dict,
                     min_edge: float = 0.02) -> list[dict]:
    """Written read on each market: [{market, decision, text}]. Decision is
    PLAY when the model edge clears the threshold at an available price."""
    home, away = g.get("home_team", ""), g.get("away_team", "")
    out = []

    def decide(ev):
        return "PLAY" if (ev is not None and pd.notna(ev) and ev >= min_edge) else "PASS"

    # MONEYLINE
    hwp = g.get("home_win_prob") or 0
    fav, fav_wp = (home, hwp) if hwp >= 0.5 else (away, 1 - hwp)
    ml_ev = (g.get("home_ml_ev", g.get("home_ev")) if hwp >= 0.5
             else g.get("away_ml_ev", g.get("away_ev")))
    price = g.get("home_ml") if hwp >= 0.5 else g.get("away_ml")
    txt = f"Model makes {fav} {fav_wp:.0%} to win"
    if price is not None and pd.notna(price):
        txt += (f"; best price {fmt_american(price)} implies "
                f"{_implied(price):.0%}")
        if ml_ev is not None and pd.notna(ml_ev):
            txt += f" — edge {ml_ev:+.1%}"
        gap = abs(fav_wp - _implied(price))
        if gap >= 0.18:
            txt += (". ⚠️ Model and market disagree sharply — the market "
                    "may know lineup/injury news the model doesn't; verify "
                    "before betting")
    else:
        txt += "; no market price available yet"
    out.append({"market": "MONEYLINE", "decision": decide(ml_ev), "text": txt + "."})

    # SPREAD / RUN LINE
    sp_line = g.get("rl_home_line", g.get("spread_home_line"))
    sp_cover = g.get("model_home_rl", g.get("model_home_cover"))
    sp_ev_h = g.get("rl_home_ev", g.get("spread_home_ev"))
    sp_ev_a = g.get("rl_away_ev", g.get("spread_away_ev"))
    label = "RUN LINE" if "rl_home_line" in g else "SPREAD"
    if sp_line is not None and pd.notna(sp_line) and sp_cover is not None:
        best_ev = max([e for e in (sp_ev_h, sp_ev_a)
                       if e is not None and pd.notna(e)], default=None)
        side = (f"{home} {sp_line:+g}" if best_ev == sp_ev_h
                else f"{away} {-sp_line:+g}")
        txt = (f"{home} {sp_line:+g} covers {sp_cover:.0%} of simulations; "
               f"best side {side}")
        if best_ev is not None:
            txt += f" at {best_ev:+.1%} edge"
        out.append({"market": label, "decision": decide(best_ev), "text": txt + "."})
    else:
        out.append({"market": label, "decision": "PASS",
                    "text": "No line posted yet."})

    # TOTAL
    line = g.get("total_line")
    proj = g.get("proj_total")
    mop = g.get("model_over_prob")
    o_ev, u_ev = g.get("over_ev"), g.get("under_ev")
    if line is not None and pd.notna(line) and proj is not None:
        gap = float(proj) - float(line)
        lean = "over" if gap > 0 else "under"
        best_ev = max([e for e in (o_ev, u_ev)
                       if e is not None and pd.notna(e)], default=None)
        txt = (f"Projected total {proj:.1f} vs line {line:g} "
               f"({gap:+.1f} toward the {lean})")
        if mop is not None and pd.notna(mop):
            txt += f"; model has the over {mop:.0%}"
        if best_ev is not None:
            txt += f" — best side {best_ev:+.1%}"
        out.append({"market": "TOTAL", "decision": decide(best_ev), "text": txt + "."})
    else:
        out.append({"market": "TOTAL", "decision": "PASS",
                    "text": f"Projected total {_num(proj)}; no market line yet."})

    # ADVANTAGES from the stat tables
    stars = []
    for key, team in (("away_off_vs_home_def", away), ("home_off_vs_away_def", home)):
        for r in matchup.get(key, []) or []:
            if r.get("adv", 0) >= 2:
                stars.append(f"{team} {r['stat']} (#{r['off_rank']} vs #{r['def_rank']})")
    if stars:
        out.append({"market": "EDGES", "decision": "NOTE",
                    "text": "Biggest stat mismatches: " + "; ".join(stars[:4]) + "."})
    return out


def _implied(american: float) -> float:
    a = float(american)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def _analysis_html(sport, g, matchup, min_edge) -> str:
    rows = matchup_analysis(sport, g, matchup, min_edge)
    conf = {k.upper(): c["score"] for k, c in market_convictions(g).items()}
    items = []
    for r in rows:
        color = {"PLAY": "var(--good)", "PASS": "var(--muted)", "NOTE": "var(--mid)"}[r["decision"]]
        score = conf.get(r["market"])
        conf_html = ""
        if r["decision"] != "NOTE":
            verdict = "DECISION: " + r["decision"]
            if score is not None:
                verdict += (f" <span style='color:{_conv_color(score)};'>"
                            f"· CONFIDENCE {score:g}</span>")
            conf_html = f"<span style='color:{color};font-weight:700;'>{verdict}</span>"
        items.append(
            "<div style='margin:6px 0;font-size:0.84rem;'>"
            f"<span style='color:var(--acc2);font-weight:700;'>{r['market']}:</span> "
            f"{r['text']} {conf_html}</div>"
        )
    return (
        "<div style='border-top:1px solid var(--line);margin-top:10px;padding-top:8px;'>"
        "<div style='font-size:0.78rem;color:var(--acc2);font-weight:700;"
        "text-transform:uppercase;margin-bottom:2px;'>📊 Statistical analysis</div>"
        + "".join(items) + "</div>"
    )


# ---------------------------------------------------------------------------
# AI briefs — clean markdown exports to paste straight into an AI chat
# ---------------------------------------------------------------------------

def _form_line(team: str, form: dict) -> str:
    """One-line team form: '12-8 (W3) — last 5: W W L W W'."""
    if not form:
        return f"{team}: —"
    rec = f"{form.get('w', 0)}-{form.get('l', 0)}"
    streak = f" ({form['streak']})" if form.get("streak") else ""
    last5 = " ".join("W" if r.get("win") else "L"
                     for r in (form.get("last5") or []))
    last5 = f" — last 5: {last5}" if last5 else ""
    return f"{team}: {rec}{streak}{last5}"


def ai_brief_game(sport: str, g: dict, matchup: dict | None = None,
                  min_edge: float = 0.02) -> str:
    """A clean markdown brief of one game — model reads, team form, and the
    biggest stat mismatches — built to paste straight into an AI chat or share.
    Robust to a missing/partial matchup dict so it works off the slate alone."""
    matchup = matchup or {}
    away, home = g.get("away_team", ""), g.get("home_team", "")
    wlbl = matchup.get("window_label")  # ranks/advantages reflect this window
    head = (f"# {sport} — {away} @ {home}\n"
            f"*{fmt_time_et(g.get('game_time'))} ET · "
            f"O/U {_num(g.get('total_line') or g.get('proj_total'))} · "
            f"proj {_num(_exp(g, 'away'))}–{_num(_exp(g, 'home'))}"
            + (f" · stats over the **{wlbl}** window" if wlbl else "") + "*")

    conv = {k.upper(): c for k, c in market_convictions(g).items()}
    reads = ["## Model read"]
    for r in matchup_analysis(sport, g, matchup, min_edge):
        if r["market"] == "EDGES":
            continue
        tag = r["decision"]
        c = conv.get(r["market"])
        if c is not None and r["decision"] != "NOTE":
            tag += f", confidence {c['score']:g}/10"
        reads.append(f"- **{r['market'].title()}** — {tag}: {r['text']}")
    nrfi = g.get("model_nrfi_prob")
    if nrfi is not None and not (isinstance(nrfi, float) and pd.isna(nrfi)):
        lean = "NRFI (no run)" if nrfi >= 0.5 else "YRFI (run scores)"
        reads.append(f"- **First inning** — model leans **{lean}** "
                     f"({max(nrfi, 1 - nrfi) * 100:.0f}%)")
    parts = [head, "\n".join(reads)]

    aform, hform = matchup.get("away_form"), matchup.get("home_form")
    if aform or hform:
        parts.append("## Team form\n"
                     f"- {_form_line(away, aform or {})}\n"
                     f"- {_form_line(home, hform or {})}")

    stars = []
    for key, team in (("away_off_vs_home_def", away),
                      ("home_off_vs_away_def", home)):
        for r in matchup.get(key, []) or []:
            if r.get("adv", 0) >= 2:
                stars.append(f"- {team} {r['stat']} (#{r.get('off_rank')} "
                             f"offense vs #{r.get('def_rank')} defense)")
    if stars:
        hdr = f"## Biggest stat mismatches{f' ({wlbl})' if wlbl else ''}"
        parts.append(hdr + "\n" + "\n".join(stars[:6]))

    parts.append("_360Five — model estimates, not financial "
                 "advice._")
    return "\n\n".join(parts)


def ai_brief_prop(sport: str, p: dict) -> str:
    """Markdown brief of one prop — projection, model read, hit-rate splits,
    and market context — for pasting into an AI chat."""
    player = p.get("player", "")
    market = short_market(p.get("market", ""))
    line = p.get("line")
    line_txt = (f" {line:g}" if isinstance(line, (int, float)) and pd.notna(line)
                else "")

    cands = []
    if p.get("ev") is not None and pd.notna(p.get("ev")):
        cands.append(("Over", p["ev"], p.get("odds")))
    if p.get("ev_over") is not None and pd.notna(p.get("ev_over")):
        cands.append(("Over", p["ev_over"], p.get("over_odds")))
    if p.get("ev_under") is not None and pd.notna(p.get("ev_under")):
        cands.append(("Under", p["ev_under"], p.get("under_odds")))
    side, ev, price = (max(cands, key=lambda c: c[1]) if cands
                       else ("", None, None))

    matchup = " vs ".join(x for x in (p.get("team"), p.get("opponent")) if x)
    parts = [f"# {sport} Prop — {player} · {market}{line_txt}"
             + (f"\n*{matchup}*" if matchup else "")]

    facts = []
    mop = p.get("model_over_prob")
    if p.get("projection") is not None and pd.notna(p.get("projection")):
        facts.append(f"- Our projection: **{p['projection']}** (line {_num(line)})")
    if mop is not None and pd.notna(mop):
        facts.append(f"- Model Over %: **{mop * 100:.0f}%**")
    if side and ev is not None and pd.notna(ev):
        facts.append(f"- Best side: **{side}{line_txt}** at "
                     f"**{fmt_american(price)}** — EV **{ev:+.1%}**")
    k = p.get("kelly")
    if k is not None and pd.notna(k) and k > 0:
        facts.append(f"- Suggested stake: **{k:.1%}** of bankroll (¼-Kelly)")
    if facts:
        parts.append("\n".join(facts))

    hr = [(lbl, p.get(key)) for lbl, key in
          (("L5", "hr_l5"), ("L10", "hr_l10"), ("L20", "hr_l20"),
           ("Season", "hr_season"), ("H2H", "hr_h2h"))]
    hr_bits = [f"{lbl} {v * 100:.0f}%" for lbl, v in hr
               if v is not None and pd.notna(v)]
    if hr_bits:
        parts.append("## Hit rate (over the line)\n" + " · ".join(hr_bits))

    ctx = []
    bp_proj, bp_side = p.get("bp_projection"), p.get("bp_recommended_side")
    bp_rating = p.get("bp_bet_rating")
    if bp_proj is not None and pd.notna(bp_proj):
        ctx.append(f"BettingPros projects {bp_proj:g}")
    if bp_side:
        stars = ("★" * int(bp_rating)) if bp_rating and pd.notna(bp_rating) else ""
        ctx.append(f"BP lean {str(bp_side).upper()} {stars}".strip())
    opp_rank = p.get("opp_rank")
    if opp_rank is not None and pd.notna(opp_rank):
        ctx.append(f"opponent ranks #{int(opp_rank)} defending this stat")
    if ctx:
        parts.append("## Market context\n- " + "\n- ".join(ctx))

    parts.append("_360Five — model estimates, not financial "
                 "advice._")
    return "\n\n".join(parts)


def ai_brief_board(board: pd.DataFrame, date: str | None = None,
                   limit: int = 40) -> str:
    """Markdown table of the slate's edges (the PLAYS board) for pasting into
    an AI chat. `board` is the build_best_bets frame (raw ev/prob floats)."""
    if board is None or board.empty:
        return "No model edges clear the threshold on this slate."
    head = f"# Slate edges{(' — ' + date) if date else ''}"
    lines = [head, "", "| Sport | Bet | Game | Price | Model % | EV % |",
             "|---|---|---|---|---|---|"]
    for _, r in board.head(limit).iterrows():
        mp = pd.to_numeric(r.get("model_prob"), errors="coerce")
        ev = pd.to_numeric(r.get("ev"), errors="coerce")
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            r.get("sport", ""), r.get("bet", ""), r.get("game", ""),
            fmt_american(r.get("price")),
            f"{mp * 100:.0f}%" if pd.notna(mp) else "—",
            f"{ev * 100:+.1f}%" if pd.notna(ev) else "—"))
    lines += ["", "_360Five — ¼-Kelly staking, model estimates, "
              "not financial advice._"]
    return "\n".join(lines)


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
    "hr_h2h": "H2H", "opp_rank": "Def Rk",
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
                            alt.value("var(--good)"), alt.value("var(--neg)")),
        tooltip=["date", "value", "opp"],
    )
    rule = alt.Chart(pd.DataFrame({"y": [line]})).mark_rule(
        color="var(--mid)", strokeDash=[5, 4], size=2).encode(y="y:Q")
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


def equity_chart(equity: pd.DataFrame):
    """Palette-matched equity curve (cumulative units) with a gradient fill and
    a dashed zero line — replaces the default blue st.line_chart. None if empty."""
    import altair as alt

    if equity is None or equity.empty:
        return None
    df = equity.reset_index()
    df.columns = ["date", "units"]
    base = alt.Chart(df)
    area = base.mark_area(
        line={"color": "var(--good)", "strokeWidth": 2},
        color=alt.Gradient(gradient="linear", x1=1, x2=1, y1=0, y2=1, stops=[
            alt.GradientStop(color="rgba(0,196,106,0.32)", offset=0),
            alt.GradientStop(color="rgba(0,196,106,0.02)", offset=1)])).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("units:Q", title="Units"),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("units:Q", format="+.1f")])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color="var(--faint)", strokeDash=[4, 4]).encode(y="y:Q")
    return (area + zero).properties(height=260, width="container")


def calibration_curve(ledger: list[dict], n_bins: int = 10) -> pd.DataFrame:
    """Reliability curve from the model_winprob ledger rows: bucket games by
    predicted home win-prob and compare to the empirical win rate. Columns:
    predicted (mean prediction in bin), empirical (actual win rate), n."""
    rows = [r for r in ledger if r.get("market") == "model_winprob"
            and r.get("pred_home_wp") is not None and r.get("home_won") is not None]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["p"] = pd.to_numeric(df["pred_home_wp"], errors="coerce")
    df["won"] = pd.to_numeric(df["home_won"], errors="coerce")
    df = df.dropna(subset=["p", "won"])
    if df.empty:
        return pd.DataFrame()
    edges = [i / n_bins for i in range(n_bins + 1)]
    df["bin"] = pd.cut(df["p"], bins=edges, include_lowest=True)
    g = (df.groupby("bin", observed=True)
           .agg(predicted=("p", "mean"), empirical=("won", "mean"), n=("won", "size"))
           .reset_index(drop=True))
    return g


def calibration_error(curve: pd.DataFrame) -> float | None:
    """Expected calibration error: average gap between predicted and actual,
    weighted by games per bin. 0 = perfectly calibrated."""
    if curve.empty:
        return None
    w = curve["n"].sum()
    if not w:
        return None
    return float((curve["n"] * (curve["predicted"] - curve["empirical"]).abs()).sum() / w)


def calibration_chart(curve: pd.DataFrame):
    """Reliability diagram: predicted vs actual win-rate with the perfect-
    calibration diagonal. Points sized by sample count. None if no data."""
    import altair as alt

    if curve.empty:
        return None
    diag = alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]})).mark_line(
        strokeDash=[4, 4], color="var(--faint)").encode(x="x:Q", y="y:Q")
    base = alt.Chart(curve)
    line = base.mark_line(color="var(--acc2)").encode(
        x=alt.X("predicted:Q", title="Model predicted win %",
                scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%")),
        y=alt.Y("empirical:Q", title="Actual win %",
                scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%")))
    pts = base.mark_circle(color="var(--good)").encode(
        x="predicted:Q", y="empirical:Q",
        size=alt.Size("n:Q", title="games"),
        tooltip=[alt.Tooltip("predicted:Q", format=".0%"),
                 alt.Tooltip("empirical:Q", format=".0%"), "n:Q"])
    return (diag + line + pts).properties(height=320, width="container")


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


# ===========================================================================
# Premium matchup card — the competitor-style team-vs-team research graphic.
# Mirrored offense/defense tables (ADV column + recency-window ranks), a team
# info bar (record/streak/results/rest/power/SOS), top-advantage star panels,
# ML/RL/Total confidence gauges, and a model decision block. Window-aware so
# the site can toggle L5/L10/L15/L20/L30/Season. Pure HTML over the matchup
# dict from project547.teamstats — themed via CSS vars so the SAME markup
# renders on the dashboard and (with concrete vars) screenshots for the
# workbook. Reuses var(--text/-muted/-good/-neg/-mid/-card/-line/-disp).
# ===========================================================================
def _mcf(v):
    """Finite float or None (collapses None and NaN)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _last(name) -> str:
    s = str(name or "").strip()
    return s.split()[-1] if s else ""


def _ord(n) -> str:
    if n is None:
        return "—"
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _rank_color(rank, n) -> str:
    """Green for a top-third rank, red for bottom-third, muted middle."""
    if rank is None or not n:
        return "var(--muted)"
    if rank <= n / 3:
        return "var(--good)"
    if rank > 2 * n / 3:
        return "var(--neg)"
    return "var(--mid)"


def _stars(k: int, of: int = 3) -> str:
    k = max(0, min(of, int(k or 0)))
    return ("<span style='color:var(--mid);'>" + "★" * k + "</span>"
            + "<span style='color:var(--line);'>" + "★" * (of - k) + "</span>")


def _fmtv(stat: str, v) -> str:
    v = _mcf(v)
    if v is None:
        return "—"
    if "%" in stat:
        return f"{v * 100:.1f}%" if v <= 1.5 else f"{v:.1f}%"
    return f"{v:.1f}"


def _mc_result_chips(last5) -> str:
    out = []
    for r in (last5 or [])[-5:]:
        win = r.get("win")
        bg = "var(--good)" if win else "var(--neg)"
        out.append(f"<span style='display:inline-block;min-width:34px;padding:2px 4px;"
                   f"margin:0 2px;border-radius:4px;background:{bg};color:#fff;"
                   f"font-size:.58rem;font-weight:700;text-align:center;'>{r.get('score','')}</span>")
    return "".join(out)


def _mc_team_panel(sport, team, form, power_rank, sos_rank, win_label, align) -> str:
    badge = assets.team_badge_html(sport, team, 40)
    form = form or {}
    rec = f"{form.get('w', 0)}-{form.get('l', 0)}"
    streak = form.get("streak", "")
    chips = _mc_result_chips(form.get("last5"))
    meta = (f"<span style='color:var(--muted);'>{rec}</span>"
            f"<span style='color:var(--faint);'> · </span>"
            f"<span style='color:var(--text);font-weight:600;'>{streak}</span>"
            f"<span style='color:var(--faint);'> · </span>"
            f"<span style='color:var(--muted);'>PWR {_ord(power_rank)}</span>"
            f"<span style='color:var(--faint);'> · </span>"
            f"<span style='color:var(--muted);'>{win_label} SOS {_ord(sos_rank)}</span>")
    name = (f"<div style='font-family:var(--disp);font-weight:700;font-size:1.15rem;"
            f"line-height:1.1;'>{team}</div>"
            f"<div style='font-size:.64rem;margin-top:3px;'>{meta}</div>"
            f"<div style='margin-top:5px;'>{chips}</div>")
    badge_html = (f"<div style='flex:0 0 auto;'>{badge}</div>")
    inner = ([badge_html, f"<div style='text-align:{align};'>{name}</div>"]
             if align == "left" else
             [f"<div style='text-align:{align};'>{name}</div>", badge_html])
    return (f"<div style='flex:1;display:flex;align-items:center;gap:11px;"
            f"justify-content:{'flex-start' if align=='left' else 'flex-end'};'>"
            + "".join(inner) + "</div>")


def _mc_gauge(call) -> str:
    color = {"PLAY": "var(--good)", "LEAN": "var(--mid)",
             "PASS": "var(--faint)", "VERIFY": "var(--neg)"}.get(
        call["decision"], "var(--faint)")
    pct = int(round(call["conf"] / 10 * 100))
    return (
        f"<div style='text-align:center;min-width:96px;'>"
        f"<div style='width:78px;height:78px;border-radius:50%;margin:0 auto 6px;"
        f"background:conic-gradient({color} {pct}%, var(--line) 0);display:flex;"
        f"align-items:center;justify-content:center;'>"
        f"<div style='width:60px;height:60px;border-radius:50%;background:var(--card);"
        f"display:flex;align-items:center;justify-content:center;font-family:var(--disp);"
        f"font-weight:700;font-size:1.3rem;color:{color};'>{call['conf']:.1f}</div></div>"
        f"<div style='font-family:var(--disp);font-size:.6rem;letter-spacing:.09em;"
        f"text-transform:uppercase;color:var(--muted);'>{call['label']}</div>"
        f"<div style='font-size:.74rem;font-weight:600;line-height:1.15;'>{call['pick']}</div>"
        f"<div style='font-size:.58rem;font-weight:800;color:{color};letter-spacing:.06em;'>"
        f"{call['decision']}</div></div>")


_GATE_BADGE = {"cleared": "🟢", "probation": "🟡", "gated": "🔴"}
_KIND_DECISION = {"core": "PLAY", "lean": "LEAN", "watch": "LEAN",
                  "verify": "VERIFY", "pass": "PASS"}


def _mc_market_calls(sport, g, min_edge, gate_table=None, bankroll: float = 0) -> list:
    """Per-market read for the Model-read block. Each call carries the pick, EV,
    conf, the gate-capped decision, the market's gate status, and the ¼-Kelly
    stake at the best price (units = % of bankroll, plus $ when a bankroll is
    given) — the 'bet ticket' a bettor needs to actually place and size it."""
    calls = []

    def add(label, gate_key, sides, line=None, p_push=0.0):
        # sides: list of (name, prob, ev, american_odds); prob is the
        # market-blended probability the EV was computed from (p_used), so the
        # Kelly stake below is sized on the same number the selection used —
        # the raw model prob is systematically more extreme (audit 2026-07 #3).
        opts = [(n, _mcf(p), _mcf(e), _mcf(o)) for (n, p, e, o) in sides
                if _mcf(p) is not None]
        if not opts:
            return
        name, prob, ev, price = max(
            opts, key=lambda o: (o[2] if o[2] is not None else -9, o[1]))
        status = None
        if gate_table is not None:
            try:
                from project547 import edge_gate
                status = edge_gate.status_for(sport, gate_key, gate_table)
            except Exception:
                status = None
        tier = play_tier(ev, min_edge, gate=status)
        decision = _KIND_DECISION.get(tier["kind"], "PASS")
        conf = max(0.0, min(10.0, 5 + (ev * 60 if ev is not None else -3)))
        # ¼-Kelly stake at the best price, scaled by the gate; only for live plays
        units = dollars = None
        if (decision in ("PLAY", "LEAN") and ev is not None and ev > 0
                and price is not None):
            f = odds.kelly_stake(prob, float(price), config.KELLY_FRACTION,
                                 push_prob=_mcf(p_push) or 0.0)
            if status:
                try:
                    from project547 import edge_gate
                    f *= edge_gate.stake_mult(status)
                except Exception:
                    pass
            if f > 0:
                units = round(f * 100, 1)                 # 1 unit = 1% of bankroll
                dollars = round(f * bankroll) if bankroll else None
        calls.append({"label": label, "pick": name, "prob": prob, "ev": ev,
                      "price": price, "line": line,
                      "decision": decision, "conf": round(conf, 1),
                      "gate": status, "stake_units": units,
                      "stake_dollars": dollars})

    # Prefer the pipeline's p_used columns (calibrated + market-blended, the
    # probability each EV was actually computed from) over the raw model prob;
    # fall back to the model prob for older latest.json payloads.
    ml_p = _mcf(g.get("home_ml_p_used"))
    add("Moneyline", "moneyline", [
        (_last(g.get("away_team")),
         (1 - ml_p) if ml_p is not None else g.get("away_win_prob"),
         g.get("away_ml_ev"), g.get("away_ml")),
        (_last(g.get("home_team")),
         ml_p if ml_p is not None else g.get("home_win_prob"),
         g.get("home_ml_ev"), g.get("home_ml"))])
    mover = _mcf(g.get("model_over_prob"))
    if mover is not None:
        tl = _mcf(g.get("total_line"))
        ln = f" {tl:g}" if tl is not None else ""
        op = _mcf(g.get("over_p_used"))
        opsh = _mcf(g.get("over_p_push")) or 0.0
        o_p = op if op is not None else mover
        u_p = max(0.0, 1 - o_p - opsh)
        add("Total", "total", [
            (f"Over{ln}", o_p, g.get("over_ev"), g.get("over_odds")),
            (f"Under{ln}", u_p, g.get("under_ev"), g.get("under_odds"))],
            line=tl, p_push=opsh)
    hc = _mcf(g.get("model_home_rl") if g.get("model_home_rl") is not None
              else g.get("model_home_cover"))
    if hc is not None:
        sl = _mcf(g.get("rl_home_line") if g.get("rl_home_line") is not None
                  else g.get("spread_home_line"))
        h_ev = g.get("rl_home_ev") if g.get("rl_home_ev") is not None else g.get("spread_home_ev")
        a_ev = g.get("rl_away_ev") if g.get("rl_away_ev") is not None else g.get("spread_away_ev")
        h_od = g.get("rl_home_odds") if g.get("rl_home_odds") is not None else g.get("spread_home_odds")
        a_od = g.get("rl_away_odds") if g.get("rl_away_odds") is not None else g.get("spread_away_odds")
        sp_p = _mcf(g.get("rl_home_p_used") if g.get("rl_home_p_used") is not None
                    else g.get("spread_home_p_used"))
        sp_push = _mcf(g.get("spread_home_p_push")) or 0.0
        h_p = sp_p if sp_p is not None else hc
        a_p = max(0.0, 1 - h_p - sp_push)
        hn = f"{_last(g.get('home_team'))} {sl:+g}" if sl is not None else _last(g.get("home_team"))
        an = f"{_last(g.get('away_team'))} {-sl:+g}" if sl is not None else _last(g.get("away_team"))
        add("Run Line" if sport == "MLB" else "Spread", "spread",
            [(hn, h_p, h_ev, h_od), (an, a_p, a_ev, a_od)], line=sl,
            p_push=sp_push)
    return calls


def _mc_stat_table(sport, n, window, win_label, title, rows, off_team,
                   def_team) -> str:
    extra = window if window in ("l15", "l20", "l30") else None
    vcols = ["season", "l10", "l5"] + ([extra] if extra else [])
    vheads = ["SEASON", "L10", "L5"] + ([win_label] if extra else [])
    rank_h = f"{win_label} RANK"
    off_logo = assets.team_badge_html(sport, off_team, 16)
    def_logo = assets.team_badge_html(sport, def_team, 16)

    th = ("padding:5px 7px;color:var(--muted);font-size:.58rem;font-weight:700;"
          "text-transform:uppercase;letter-spacing:.03em;")
    head = (f"<th style='{th}text-align:left;'>STAT</th>"
            + "".join(f"<th style='{th}text-align:center;'>{h}</th>" for h in vheads)
            + f"<th style='{th}text-align:center;'>{rank_h}</th>"
            + f"<th style='{th}text-align:center;'>ADV</th>"
            + f"<th style='{th}text-align:center;'>{rank_h}</th>"
            + "".join(f"<th style='{th}text-align:center;'>{h}</th>" for h in reversed(vheads))
            + f"<th style='{th}text-align:right;'>STAT</th>")

    def num(stat, v, em=False):
        w = "700" if em else "500"
        disp = _gap("") if _mcf(v) is None else _fmtv(stat, v)
        return (f"<td style='padding:5px 7px;text-align:center;font-weight:{w};"
                f"font-size:.74rem;'>{disp}</td>")

    def rank_pill(rank):
        if _mcf(rank) is None:
            return f"<td style='padding:5px 7px;text-align:center;'>{_gap('')}</td>"
        c = _rank_color(rank, n)
        return (f"<td style='padding:5px 7px;text-align:center;'>"
                f"<span style='color:{c};font-weight:700;font-size:.74rem;'>{_ord(rank)}</span></td>")

    def adv_cell(r):
        if not r.get("adv"):
            return "<td style='text-align:center;color:var(--line);font-size:.7rem;'>–</td>"
        logo = off_logo if (r.get("off_rank") or 99) <= (r.get("def_rank") or 99) else def_logo
        return (f"<td style='text-align:center;white-space:nowrap;'>{logo}"
                f"<div style='font-size:.6rem;'>{_stars(r['adv'])}</div></td>")

    body = []
    for r in rows:
        cells = [f"<td style='padding:5px 7px;text-align:left;font-weight:700;"
                 f"font-size:.72rem;'>{r['stat']}</td>"]
        for c in vcols:
            cells.append(num(r["stat"], r.get(f"off_{c}"), em=(c == window)))
        cells.append(rank_pill(r.get("off_rank")))
        cells.append(adv_cell(r))
        cells.append(rank_pill(r.get("def_rank")))
        for c in reversed(vcols):
            cells.append(num(r["stat"], r.get(f"def_{c}"), em=(c == window)))
        cells.append(f"<td style='padding:5px 7px;text-align:right;font-weight:700;"
                     f"font-size:.72rem;color:var(--muted);'>Opp {r['stat']}</td>")
        body.append("<tr style='border-top:1px solid var(--line);'>" + "".join(cells) + "</tr>")

    return (f"<div style='font-family:var(--disp);font-size:.66rem;font-weight:700;"
            f"letter-spacing:.08em;text-transform:uppercase;color:var(--text);"
            f"margin:14px 0 4px;'>{title}</div>"
            f"<table style='width:100%;border-collapse:collapse;'>"
            f"<tr>{head}</tr>{''.join(body)}</table>")


def _mc_top_adv(title, rows, off_team, def_team, align) -> str:
    adv = sorted([r for r in rows if r.get("adv")],
                 key=lambda r: (-r["adv"], (r.get("def_rank") or 99) - (r.get("off_rank") or 0)),
                 reverse=False)[:4]
    items = []
    for r in adv:
        line = (f"<b>{r['stat']}</b>: {_last(off_team)} ({_ord(r['off_rank'])}) "
                f"vs {_last(def_team)} ({_ord(r['def_rank'])})")
        items.append(f"<div style='font-size:.68rem;margin:3px 0;'>"
                     f"{_stars(r['adv'])} {line}</div>")
    if not items:
        items = ["<div style='font-size:.66rem;color:var(--muted);'>No standout edges.</div>"]
    return (f"<div style='flex:1;text-align:{align};'>"
            f"<div style='font-family:var(--disp);font-size:.62rem;font-weight:700;"
            f"letter-spacing:.08em;text-transform:uppercase;color:var(--good);"
            f"margin-bottom:4px;'>{title}</div>" + "".join(items) + "</div>")


def _mc_trends(trends, away, home) -> str:
    rows = [t for t in (trends or []) if _mcf(t.get("home")) is not None
            or _mcf(t.get("away")) is not None]
    if not rows:
        return ""
    th = ("padding:5px 7px;color:var(--muted);font-size:.58rem;font-weight:700;"
          "text-transform:uppercase;letter-spacing:.03em;")

    def cell(v):
        v = _mcf(v)
        if v is None:
            s = _gap("")
        else:
            s = f"{v * 100:.0f}%" if abs(v) <= 1.5 else f"{v:.0f}%"
        return f"<td style='padding:5px 7px;text-align:center;font-weight:600;font-size:.74rem;'>{s}</td>"

    head = (f"<th style='{th}text-align:left;'>{_last(away)}</th>"
            f"<th style='{th}text-align:center;'>Trend</th>"
            f"<th style='{th}text-align:right;'>{_last(home)}</th>")
    body = "".join("<tr style='border-top:1px solid var(--line);'>"
                   + cell(t.get("away"))
                   + f"<td style='padding:5px 7px;text-align:center;font-weight:700;"
                   f"font-size:.7rem;'>{t['stat']}</td>"
                   + cell(t.get("home")) + "</tr>" for t in rows)
    return (f"<div style='font-family:var(--disp);font-size:.66rem;font-weight:700;"
            f"letter-spacing:.08em;text-transform:uppercase;color:var(--text);"
            f"margin:14px 0 4px;'>Game trends</div>"
            f"<table style='width:100%;max-width:420px;border-collapse:collapse;'>"
            f"<tr>{head}</tr>{body}</table>")


def _supporting_set(sport):
    try:
        from project547 import teamstats
        return teamstats.SUPPORTING_LABELS.get(sport, set())
    except Exception:  # noqa: BLE001 — degrade to no split if data layer absent
        return set()


def _mc_supporting(sport, n, window, win_label, away, home, a_supp, h_supp) -> str:
    """Team-vs-team supporting stats (rebounding / ball control), shaded by
    league rank — the reference card's separate 'Supporting Statistics' block."""
    labels = [l for l in a_supp if l in h_supp]
    if not labels:
        return ""
    extra = window if window in ("l15", "l20", "l30") else None
    vcols = ["season", "l10", "l5"] + ([extra] if extra else [])
    vheads = ["SEASON", "L10", "L5"] + ([win_label] if extra else [])
    rank_h = f"{win_label} RANK"
    th = ("padding:5px 7px;color:var(--muted);font-size:.58rem;font-weight:700;"
          "text-transform:uppercase;letter-spacing:.03em;")
    head = (f"<th style='{th}text-align:left;'>{_last(away)}</th>"
            + "".join(f"<th style='{th}text-align:center;'>{h}</th>" for h in vheads)
            + f"<th style='{th}text-align:center;'>{rank_h}</th>"
            + f"<th style='{th}text-align:center;'>STAT</th>"
            + f"<th style='{th}text-align:center;'>{rank_h}</th>"
            + "".join(f"<th style='{th}text-align:center;'>{h}</th>" for h in reversed(vheads))
            + f"<th style='{th}text-align:right;'>{_last(home)}</th>")

    def num(stat, v, em=False):
        w = "700" if em else "500"
        disp = _gap("") if _mcf(v) is None else _fmtv(stat, v)
        return (f"<td style='padding:5px 7px;text-align:center;font-weight:{w};"
                f"font-size:.74rem;'>{disp}</td>")

    def pill(rank, better):
        c = _rank_color(rank, n)
        return (f"<td style='padding:5px 7px;text-align:center;'>"
                f"<span style='color:{c};font-weight:700;font-size:.74rem;'>"
                f"{_ord(rank)}{' ▲' if better else ''}</span></td>")

    body = []
    for lab in labels:
        ar, hr = a_supp[lab], h_supp[lab]
        a_rk, h_rk = ar.get("off_rank"), hr.get("off_rank")
        a_better = (a_rk or 99) < (h_rk or 99)
        cells = [num(lab, ar.get(f"off_{c}"), em=(c == window)) for c in vcols]
        cells.append(pill(a_rk, a_better))
        cells.append(f"<td style='padding:5px 7px;text-align:center;font-weight:700;"
                     f"font-size:.72rem;'>{lab}</td>")
        cells.append(pill(h_rk, (not a_better) and h_rk is not None))
        cells += [num(lab, hr.get(f"off_{c}"), em=(c == window)) for c in reversed(vcols)]
        body.append("<tr style='border-top:1px solid var(--line);'>"
                    + "".join(cells) + "</tr>")
    return (f"<div style='font-family:var(--disp);font-size:.66rem;font-weight:700;"
            f"letter-spacing:.08em;text-transform:uppercase;color:var(--text);"
            f"margin:14px 0 4px;'>Supporting statistics</div>"
            f"<table style='width:100%;border-collapse:collapse;'>"
            f"<tr>{head}</tr>{''.join(body)}</table>")


def _invert_survival(over_probs: dict, target_s: float):
    """Line where P(total > line) crosses ``target_s`` in the sim's survival
    curve (``{line: P(total>line)}``), linearly interpolated. S decreases with
    line, so a percentile p maps to target_s = 1 - p."""
    pts = sorted((float(k), float(v)) for k, v in over_probs.items())
    if len(pts) < 2:
        return None
    # clamp outside the grid
    if target_s >= pts[0][1]:
        return pts[0][0]
    if target_s <= pts[-1][1]:
        return pts[-1][0]
    for (l0, s0), (l1, s1) in zip(pts, pts[1:]):
        if s0 >= target_s >= s1 and s0 != s1:
            return l0 + (l1 - l0) * (s0 - target_s) / (s0 - s1)
    return None


def _total_range(sport: str, g: dict):
    """(p25, p50, p75) of the projected total. From the sim's over_probs when
    present (MLB), else a normal IQR around proj_total using the sport's
    sigma_total (WNBA/normal model). None when neither is available."""
    op = g.get("over_probs")
    if op:
        p25, p50, p75 = (_invert_survival(op, 0.75), _invert_survival(op, 0.5),
                         _invert_survival(op, 0.25))
        if None not in (p25, p75):
            return p25, p50, p75
    pt = g.get("proj_total")
    if pt is None or (isinstance(pt, float) and pd.isna(pt)):
        return None
    try:
        from project547.sports import SPORTS
        sig = SPORTS[sport].sigma_total
    except Exception:
        sig = 0.0
    if not sig:
        return None
    iqr = 0.6745 * sig                       # 25/75 of a normal
    return float(pt) - iqr, float(pt), float(pt) + iqr


def _projection_hero(sport: str, g: dict) -> str:
    """The premium headline: SCORE PROJECTION · away x.x · home y.y · total ·
    projected margin · home win% · likely total range and the market cushion.
    Pure model output; safe when fields are missing."""
    ax, hx = _exp(g, "away"), _exp(g, "home")
    away, home = _last(g.get("away_team", "")), _last(g.get("home_team", ""))
    pt = g.get("proj_total")
    if pt is None and ax is not None and hx is not None:
        pt = float(ax) + float(hx)
    hwp = _mcf(g.get("home_win_prob"))
    # margin + favorite
    fav_txt = ""
    if ax is not None and hx is not None:
        m = float(hx) - float(ax)
        who, mag = (home, m) if m >= 0 else (away, -m)
        fav_txt = f"{who} by {mag:.1f}"
    win_txt = ""
    if hwp is not None:
        fav, p = (home, hwp) if hwp >= 0.5 else (away, 1 - hwp)
        win_txt = f"{fav} {p * 100:.0f}%"

    # range + market cushion
    rng = _total_range(sport, g)
    tl = _mcf(g.get("total_line"))
    range_txt = ""
    if rng:
        p25, _p50, p75 = rng
        range_txt = f"likely {p25:.1f}–{p75:.1f}"
        if tl is not None:
            if tl <= p25:
                range_txt += (f" · market {tl:g} → <b style='color:var(--good);'>"
                              f"lean OVER</b> (+{p25 - tl:.1f})")
            elif tl >= p75:
                range_txt += (f" · market {tl:g} → <b style='color:var(--good);'>"
                              f"lean UNDER</b> (+{tl - p75:.1f})")
            else:
                range_txt += (f" · market {tl:g} <span style='color:var(--faint);'>"
                              f"inside range · no edge</span>")

    sub = " · ".join(x for x in (f"Total {_num(pt)}", fav_txt, win_txt) if x)
    return (
        f"<div style='background:var(--card2);border:1.5px solid var(--line);"
        f"border-radius:12px;padding:12px 16px;margin-bottom:12px;text-align:center;'>"
        f"<div style='font-family:var(--disp);font-size:.6rem;letter-spacing:.16em;"
        f"text-transform:uppercase;color:var(--faint);margin-bottom:3px;'>Score projection</div>"
        f"<div style='font-family:var(--disp);font-weight:700;font-size:1.7rem;"
        f"line-height:1.1;color:var(--text);'>"
        f"{away} <span style='color:var(--good);'>{_num(ax)}</span>"
        f"<span style='color:var(--faint);font-weight:400;'> · </span>"
        f"{home} <span style='color:var(--good);'>{_num(hx)}</span></div>"
        f"<div style='font-size:.74rem;color:var(--muted);margin-top:4px;'>{sub}</div>"
        + (f"<div style='font-size:.66rem;color:var(--muted);margin-top:3px;'>{range_txt}</div>"
           if range_txt else "")
        + "</div>")


def matchup_card_html(sport: str, g: dict, matchup: dict, window: str = "l5",
                      min_edge: float = 0.02, title: str | None = None,
                      gate_table=None, bankroll: float = 0) -> str:
    """Never-raises wrapper: on any failure fall back to the compact game card
    (and log the real error) so one bad matchup can't crash the sport page."""
    try:
        return _matchup_card_impl(sport, g, matchup, window=window,
                                  min_edge=min_edge, title=title,
                                  gate_table=gate_table, bankroll=bankroll)
    except Exception:
        log.exception("matchup_card_html failed for %s @ %s",
                      (g or {}).get("away_team"), (g or {}).get("home_team"))
        try:
            return game_card_html(sport, g)
        except Exception:
            log.exception("game_card_html fallback also failed")
            return _fallback_card(
                f"{(g or {}).get('away_team', '')} @ {(g or {}).get('home_team', '')}")


def _matchup_card_impl(sport: str, g: dict, matchup: dict, window: str = "l5",
                       min_edge: float = 0.02, title: str | None = None,
                       gate_table=None, bankroll: float = 0) -> str:
    """The full premium matchup graphic. ``matchup`` is teamstats.matchup(...,
    window=window). Safe on an empty matchup (renders the header only).

    ``gate_table`` (edge_gate.gate_table()) and ``bankroll`` turn the Model-read
    block into a bet ticket: each market shows its gate status and the ¼-Kelly
    stake alongside the pick and EV."""
    away, home = g.get("away_team", ""), g.get("home_team", "")
    n = matchup.get("n_teams", 30)
    win_label = matchup.get("window_label", "L5")
    hl = (title or f"{_last(away)} vs {_last(home)}").upper()
    when = fmt_time_et(g.get("game_time"))

    # info bar bits
    bits = []
    aml, hml = _mcf(g.get("away_ml")), _mcf(g.get("home_ml"))
    if aml is not None and hml is not None:
        bits.append(f"Line <b>{_last(away)} {fmt_american(aml)} / {_last(home)} {fmt_american(hml)}</b>")
    tl = _mcf(g.get("total_line"))
    if tl is not None:
        bits.append(f"Total <b>{tl:g}</b>")
    ar, hr = matchup.get("away_rest"), matchup.get("home_rest")
    if ar is not None or hr is not None:
        bits.append(f"Rest <b>{_last(away)} {ar if ar is not None else '–'} / "
                    f"{_last(home)} {hr if hr is not None else '–'}</b>")
    nrfi = _mcf(g.get("model_nrfi_prob"))
    if nrfi is not None:
        lean = "NRFI" if nrfi >= 0.5 else "YRFI"
        bits.append(f"1st inning <b>{lean} {max(nrfi, 1 - nrfi) * 100:.0f}%</b>")
    # Park factor (MLB) + weather — top-of-mind for totals/HR bettors and
    # previously only shown on the fallback card, never the premium one.
    if sport == "MLB":
        try:
            from project547 import parks
            pf = parks.factor(g.get("home_team", ""))
            if pf:
                tag = ("hitter" if pf > 1.02 else "pitcher" if pf < 0.98
                       else "neutral")
                bits.append(f"Park <b>{pf:.2f}× {tag}</b>")
        except Exception:
            pass
    wx = _weather_txt(g).lstrip(" ·").strip()
    if wx:
        bits.append(wx)
    # Bullpen fatigue (MLB) — a taxed pen leaks late runs; only surface if notable
    for tm, key in ((_last(away), "away_bullpen"), (_last(home), "home_bullpen")):
        bp = matchup.get(key) or {}
        if bp.get("level") in ("heavy", "moderate"):
            emoji = "🔴" if bp["level"] == "heavy" else "🟠"
            bits.append(f"Pen {emoji} <b>{tm} {bp['level']} ({bp['ip']:g} IP L2)</b>")
    bits.append(f"Window <b>{win_label}</b>")

    header = (
        "<div style='display:flex;align-items:center;gap:10px;'>"
        + _mc_team_panel(sport, away, matchup.get("away_form"),
                         matchup.get("away_power_rank"), matchup.get("away_sos_rank"),
                         win_label, "right")
        + (f"<div style='flex:0 0 auto;text-align:center;padding:0 10px;'>"
           f"<div style='font-family:var(--disp);font-weight:700;font-size:1.1rem;"
           f"color:var(--faint);'>@</div>"
           f"<div style='font-size:.6rem;color:var(--muted);margin-top:2px;'>{when}</div></div>")
        + _mc_team_panel(sport, home, matchup.get("home_form"),
                         matchup.get("home_power_rank"), matchup.get("home_sos_rank"),
                         win_label, "left")
        + "</div>"
        + (f"<div style='display:flex;flex-wrap:wrap;gap:16px;justify-content:center;"
           f"margin-top:10px;padding-top:9px;border-top:1px solid var(--line);"
           f"font-size:.66rem;color:var(--muted);'>"
           + "".join(f"<span>{b}</span>" for b in bits) + "</div>"))

    calls = _mc_market_calls(sport, g, min_edge, gate_table=gate_table,
                             bankroll=bankroll)
    gauges = (f"<div style='display:flex;justify-content:center;gap:18px;margin:14px 0;'>"
              + "".join(_mc_gauge(c) for c in calls) + "</div>") if calls else ""

    a_rows = matchup.get("away_off_vs_home_def") or []
    h_rows = matchup.get("home_off_vs_away_def") or []
    # split scoring (primary) from supporting (rebounding / ball control)
    supp = _supporting_set(sport)
    a_prim = [r for r in a_rows if r["stat"] not in supp]
    h_prim = [r for r in h_rows if r["stat"] not in supp]
    a_supp = {r["stat"]: r for r in a_rows if r["stat"] in supp}
    h_supp = {r["stat"]: r for r in h_rows if r["stat"] in supp}
    top_adv = ("<div style='display:flex;gap:20px;margin:10px 0 4px;'>"
               + _mc_top_adv(f"{_last(away)} top advantages", a_rows, away, home, "left")
               + _mc_top_adv(f"{_last(home)} top advantages", h_rows, home, away, "right")
               + "</div>") if (a_rows or h_rows) else ""

    tables = ""
    if a_prim:
        tables += _mc_stat_table(sport, n, window, win_label,
                                 f"{away} offense vs {home} defense", a_prim, away, home)
    if h_prim:
        tables += _mc_stat_table(sport, n, window, win_label,
                                 f"{home} offense vs {away} defense", h_prim, home, away)
    tables += _mc_supporting(sport, n, window, win_label, away, home, a_supp, h_supp)
    tables += _mc_trends(matchup.get("trends"), away, home)

    # decision block
    dec_bits = []
    any_gate = any(c.get("gate") for c in calls)
    for c in calls:
        col = {"PLAY": "var(--good)", "LEAN": "var(--mid)", "PASS": "var(--faint)",
               "VERIFY": "var(--neg)"}.get(c["decision"], "var(--faint)")
        ev = f"{c['ev']*100:+.1f}% EV" if c["ev"] is not None else "no priced edge"
        badge = _GATE_BADGE.get(c.get("gate"), "")
        badge_html = f"{badge} " if badge else ""
        stake = ""
        if c.get("stake_units"):
            dol = f" (${c['stake_dollars']})" if c.get("stake_dollars") else ""
            stake = (f" · <b style='color:var(--text);'>"
                     f"{c['stake_units']:g}u{dol}</b>")
        dec_bits.append(f"<div style='margin:2px 0;font-size:.72rem;'>"
                        f"{badge_html}<b>{c['label']}:</b> {c['pick']} — "
                        f"<span style='color:{col};font-weight:700;'>{c['decision']}</span> "
                        f"<span style='color:var(--muted);'>({ev}, conf {c['conf']:.1f}/10)</span>"
                        f"{stake}</div>")
    legend = ("<div style='font-size:.56rem;color:var(--muted);margin-top:5px;'>"
              "🟢 proven edge (full stake) · 🟡 unproven, tracking (½) · "
              "🔴 no proven edge (no bet) · stake = ¼-Kelly, 1u = 1% bankroll"
              "</div>") if any_gate else ""
    # Answer first: the bet ticket is a self-contained panel that LEADS the card
    # (a two-second read for anyone who just wants the play), not a footnote at
    # the bottom. The brass left-rail marks it as the headline of the sheet.
    decision = (f"<div style='margin-bottom:14px;padding:11px 14px;border:1px solid var(--line);"
                f"border-left:3px solid var(--acc);border-radius:10px;background:var(--card2);'>"
                f"<div style='font-family:var(--disp);font-size:.64rem;font-weight:700;"
                f"letter-spacing:.08em;text-transform:uppercase;color:var(--acc);"
                f"margin-bottom:5px;'>Model read · bet ticket</div>" + "".join(dec_bits)
                + legend
                + "<div style='font-size:.58rem;color:var(--faint);margin-top:6px;'>"
                "Personal research · not financial advice · the trigger is always yours."
                "</div></div>") if dec_bits else ""

    return (
        f"<div style='background:var(--card);border:1.5px solid var(--line);"
        f"border-radius:14px;padding:18px 20px;color:var(--text);"
        f"font-family:var(--font, DM Sans, system-ui, sans-serif);max-width:1120px;'>"
        f"<div style='font-family:var(--disp);font-weight:700;font-size:1rem;"
        f"letter-spacing:.04em;color:var(--text);margin-bottom:10px;'>{hl}"
        f"<span style='color:var(--faint);font-weight:400;'> · {win_label} window</span></div>"
        f"{decision}{_projection_hero(sport, g)}{header}{gauges}{top_adv}{tables}</div>")


# ===========================================================================
# Sharp Sheet — the one-page research view (redesign). Composes the existing
# section builders into a single scannable sheet: header + markets + best line
# + matchup breakdown + trends + lineups (external player links) + top props +
# model read. Sport-adaptive; degrades gracefully on any missing piece.
# ===========================================================================

def player_ext_link(name, sport: str, player_id=None) -> str:
    """External advanced-stats link for a player, opening in a new tab. MLB ->
    Baseball Savant (by id when known, else name search); other sports -> an
    ESPN search. Plain text when there's no name."""
    if not name or (isinstance(name, float) and pd.isna(name)):
        return str(name or "")
    if sport == "MLB" and player_id is not None and pd.notna(player_id):
        url = f"https://baseballsavant.mlb.com/savant-player/{int(player_id)}"
    elif sport == "MLB":
        url = ("https://baseballsavant.mlb.com/savant-player-search?search="
               + urllib.parse.quote(str(name)))
    else:
        url = "https://www.espn.com/search/results?q=" + urllib.parse.quote(str(name))
    return (f"<a class='osp-plink' target='_blank' rel='noopener' href='{url}'>{name}"
            f"<span style='font-size:.62rem;color:var(--faint);'> ↗</span></a>")


def top_game_props(sport: str, props, away: str, home: str, limit: int = 6) -> list:
    """Best-EV props for a single game's two teams (reuses _prop_edge). Returns
    rows sorted by EV, positive edges first."""
    from project547.names import normalize
    names = {normalize(away or ""), normalize(home or "")}
    names.discard("")
    out = []
    for p in props or []:
        tm = {normalize(p.get("team", "")), normalize(p.get("opponent", ""))}
        if names and not (tm & names):
            continue
        row = _prop_edge(sport, p)
        if row and row.get("ev") is not None and pd.notna(row["ev"]):
            out.append(row)
    out.sort(key=lambda r: r["ev"] if r["ev"] is not None else -9, reverse=True)
    return out[:limit]


def _ss_header(sport: str, g: dict, matchup: dict) -> str:
    away, home = g.get("away_team", ""), g.get("home_team", "")
    a_badge = assets.team_badge_html(sport, away, 42)
    h_badge = assets.team_badge_html(sport, home, 42)

    def rec(side):  # records/streak if the data layer has them, else nothing
        r = matchup.get(f"{side}_record") or g.get(f"{side}_record")
        s = matchup.get(f"{side}_streak") or g.get(f"{side}_streak")
        bits = []
        if r:
            bits.append(f"<span>{r}</span>")
        if s:
            bits.append(f"<span class='osp-ss-pill'>{s}</span>")
        return ("<div class='osp-ss-sub'>" + "".join(bits) + "</div>") if bits else ""

    when = fmt_time_et(g.get("game_time"))
    venue = matchup.get("venue") or g.get("venue") or ""
    wx = _weather_txt(g).lstrip(" ·").strip()
    meta = []
    if venue:
        meta.append(f"🏟️ <b>{venue}</b>")
    if when:
        meta.append(f"🕐 <b>{when} ET</b>")
    if wx:
        meta.append(wx)
    # confidence is shown as its own multi-factor strip below the header
    meta.append("<span style='margin-left:auto;color:var(--warn,#a9791b);"
                "font-weight:600;'>🔒 Odds lock at first pitch</span>")
    return (
        "<div class='osp-ss-head'>"
        "<div class='osp-ss-teams'>"
        f"<div class='osp-ss-team'>{a_badge}<div><div class='osp-ss-name'>{away}</div>{rec('away')}</div></div>"
        f"<div class='osp-ss-hero'>{_projection_hero(sport, g)}</div>"
        f"<div class='osp-ss-team home'><div><div class='osp-ss-name'>{home}</div>{rec('home')}</div>{h_badge}</div>"
        "</div>"
        f"<div class='osp-ss-meta'>{''.join(f'<span>{m}</span>' for m in meta)}</div>"
        "</div>")


def _ss_market_cards(calls: list) -> str:
    if not calls:
        return ""
    tone = {"PLAY": ("good", "PLAY"), "LEAN": ("mid", "LEAN"),
            "VERIFY": ("neg", "VERIFY"), "PASS": ("faint", "PASS")}
    cards = []
    for c in calls:
        col, tag = tone.get(c["decision"], ("faint", "PASS"))
        ev = c.get("ev")
        sub = (f"edge {ev * 100:+.1f}%" if ev is not None and pd.notna(ev)
               else "no priced edge")
        stake = (f" · {c['stake_units']:g}u" if c.get("stake_units") else "")
        prob = _pct(c.get("prob"))
        cards.append(
            f"<div class='osp-ss-mk'><span class='osp-ss-tag t-{col}'>{tag}</span>"
            f"<div class='osp-ss-mklbl'>{c['label']}</div>"
            f"<div class='osp-ss-mkbig' style='color:var(--{col},var(--muted));'>{prob}</div>"
            f"<div class='osp-ss-mkpick'>{c['pick']}</div>"
            f"<div class='osp-ss-mksub'>{sub}{stake}</div></div>")
    return f"<div class='osp-ss-markets'>{''.join(cards)}</div>"


def _ss_bestline(g: dict, best_line: dict | None) -> str:
    if not best_line:
        return ""
    bits = []
    for label, info in best_line.items():
        price = fmt_american(info.get("price")) if isinstance(info, dict) else None
        if price:
            book = str(info.get("book", "")).replace("_", " ").title()
            bits.append(f"<b>{label}</b> {price} <span style='color:var(--muted);'>{book}</span>")
    if not bits:
        return ""
    return ("<div class='osp-ss-bl'>🛒 Best available · "
            + " · ".join(bits) + "</div>")


def _ss_lineups(sport: str, g: dict) -> str:
    lu = g.get("lineups") or {}
    if not (lu.get("home") or lu.get("away")):
        return ""
    pids = g.get("player_ids") or {}
    gpk = g.get("game_pk")

    def side(which, team, color):
        rows = lu.get(which) or []
        if not rows:
            return ""
        body = []
        for i, nm in enumerate(rows, 1):
            body.append(f"<tr><td><span class='osp-ss-num'>{i}</span> "
                        f"{player_link(nm, gpk, sport)}</td></tr>")
        return (f"<div class='osp-ss-lu'><div class='osp-ss-lut' style='color:{color};'>"
                f"{team}</div><table>{''.join(body)}</table></div>")

    a = side("away", g.get("away_team", ""), "var(--link,#8a1f2b)")
    h = side("home", g.get("home_team", ""), "var(--good,#2f7a4a)")
    if not (a or h):
        return ""
    return (f"<div class='osp-ss-split'>{a}{h}</div>")


def _ss_props(sport: str, prop_rows: list) -> str:
    if not prop_rows:
        return ""
    items = []
    for r in prop_rows:
        ev = r.get("ev")
        col = "good" if (ev or 0) >= 0.04 else "mid" if (ev or 0) > 0 else "faint"
        stake = f" · {r['kelly']:.1f}u" if r.get("kelly") and pd.notna(r["kelly"]) else ""
        items.append(
            f"<div class='osp-ss-prop'><div><div class='osp-ss-pp'>{r.get('bet', '')}</div>"
            f"<div class='osp-ss-pl'>{fmt_american(r.get('price'))} · model {_pct(r.get('model_prob'))}</div></div>"
            f"<div class='osp-ss-pev' style='color:var(--{col},var(--muted));'>"
            f"{ev * 100:+.1f}%<small>{stake}</small></div></div>")
    return f"<div class='osp-ss-props'>{''.join(items)}</div>"


_SS_STYLE = """<style>
.osp-ss{max-width:1160px;margin:0 auto}
.osp-ss-sec{margin-top:16px}
.osp-ss-sh{font-family:var(--disp);font-weight:700;font-size:.72rem;letter-spacing:.09em;
  text-transform:uppercase;color:var(--text);margin:0 2px 8px;display:flex;gap:9px;align-items:baseline}
.osp-ss-sh .hint{font-family:var(--font);font-weight:400;letter-spacing:0;text-transform:none;
  font-size:.72rem;color:var(--muted)}
.osp-ss-head{background:var(--card);border:1.5px solid var(--line);border-radius:16px;overflow:hidden}
.osp-ss-teams{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:center;padding:16px 20px}
.osp-ss-team{display:flex;align-items:center;gap:11px}
.osp-ss-team.home{justify-content:flex-end;flex-direction:row-reverse;text-align:right}
.osp-ss-name{font-family:var(--disp);font-weight:600;font-size:1.28rem;line-height:1.05}
.osp-ss-sub{font-size:.72rem;color:var(--muted);margin-top:3px;display:flex;gap:6px}
.osp-ss-team.home .osp-ss-sub{justify-content:flex-end}
.osp-ss-pill{font-size:.62rem;font-weight:700;padding:1px 5px;border-radius:5px;background:var(--card2);color:var(--muted)}
.osp-ss-hero{min-width:210px}
.osp-ss-meta{display:flex;flex-wrap:wrap;gap:6px 16px;padding:9px 20px;border-top:1px dashed var(--line);
  font-size:.75rem;color:var(--muted);align-items:center}
.osp-ss-meta b{color:var(--text)}
.osp-ss-markets{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}
.osp-ss-mk{background:var(--card);border:1.5px solid var(--line);border-radius:12px;padding:12px 10px;
  text-align:center;position:relative}
.osp-ss-mklbl{font-family:var(--disp);font-size:.68rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.osp-ss-mkbig{font-family:var(--disp);font-weight:700;font-size:1.9rem;line-height:1.05;margin:4px 0 1px}
.osp-ss-mkpick{font-size:.82rem;font-weight:600}
.osp-ss-mksub{font-size:.72rem;color:var(--muted);margin-top:3px}
.osp-ss-tag{position:absolute;top:8px;right:8px;font-size:.56rem;font-weight:700;letter-spacing:.04em;
  padding:2px 5px;border-radius:5px;text-transform:uppercase}
.t-good{background:rgba(47,122,74,.14);color:var(--good)} .t-mid{background:rgba(169,121,27,.16);color:var(--mid,#a9791b)}
.t-neg{background:rgba(162,59,45,.14);color:var(--neg)} .t-faint{background:var(--card2);color:var(--faint)}
.osp-ss-bl{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:9px 13px;
  font-size:.78rem;color:var(--text)}
.osp-ss-split{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.osp-ss-card{background:var(--card);border:1.5px solid var(--line);border-radius:13px;padding:13px 15px}
.osp-ss-lu{background:var(--card);border:1.5px solid var(--line);border-radius:13px;padding:11px 14px}
.osp-ss-lu table{width:100%;border-collapse:collapse;font-size:.82rem}
.osp-ss-lu td{padding:4px 6px;border-top:1px solid var(--line)}
.osp-ss-lut{font-family:var(--disp);font-size:.68rem;letter-spacing:.05em;text-transform:uppercase;margin-bottom:4px}
.osp-ss-num{color:var(--faint);margin-right:3px}
.osp-ss-props{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.osp-ss-prop{display:flex;align-items:center;gap:10px;background:var(--card2);border:1px solid var(--line);
  border-radius:10px;padding:8px 12px}
.osp-ss-pp{font-weight:600;font-size:.82rem} .osp-ss-pl{font-size:.72rem;color:var(--muted)}
.osp-ss-pev{margin-left:auto;text-align:right;font-family:var(--disp);font-weight:700}
.osp-ss-pev small{display:block;font-size:.58rem;color:var(--muted);font-weight:600}
.osp-plink,.osp-ss-lu a,.osp-ss-spn a{color:var(--link,#8a1f2b);font-weight:600;
  text-decoration:none;border-bottom:1px solid transparent}
.osp-plink:hover,.osp-ss-lu a:hover,.osp-ss-spn a:hover{border-bottom-color:currentColor}
</style>"""


def _match_market_cards(sport: str, g: dict) -> str:
    """1X2 (+O/U) cards for soccer, or two player cards for tennis, in the dark
    osp-ss market-card style."""
    is_tennis = sport in ("ATP", "WTA")

    def card(lbl, prob, price, ev, tone):
        sub = (f"edge {ev * 100:+.1f}%" if ev is not None and pd.notna(ev)
               else "no priced edge")
        return (f"<div class='osp-ss-mk'><div class='osp-ss-mklbl'>{lbl}</div>"
                f"<div class='osp-ss-mkbig' style='color:var(--{tone},var(--muted));'>{_pct(prob)}</div>"
                f"<div class='osp-ss-mkpick'>{fmt_american(price) or '—'}</div>"
                f"<div class='osp-ss-mksub'>{sub}</div></div>")

    if is_tennis:
        cards = [
            card(_last(g.get("player1", "")) or "Player 1", g.get("player1_win_prob"),
                 g.get("p1_price"), g.get("p1_ev"), "good"),
            card(_last(g.get("player2", "")) or "Player 2", g.get("player2_win_prob"),
                 g.get("p2_price"), g.get("p2_ev"), "link")]
    else:
        cards = [
            card(_last(g.get("home_team", "")), g.get("home_win_prob"),
                 g.get("home_ml"), g.get("home_ev"), "good"),
            card("Draw", g.get("draw_prob"), g.get("draw_ml"), g.get("draw_ev"), "mid"),
            card(_last(g.get("away_team", "")), g.get("away_win_prob"),
                 g.get("away_ml"), g.get("away_ev"), "link"),
            card("Over 2.5", g.get("over_2_5"), g.get("over_price"),
                 g.get("over_ev"), "good")]
    return (f"<div class='osp-ss-markets' style='grid-template-columns:"
            f"repeat({len(cards)},1fr);'>{''.join(cards)}</div>")


def _match_header(sport: str, g: dict) -> str:
    is_tennis = sport in ("ATP", "WTA")
    if is_tennis:
        p1, p2 = g.get("player1", ""), g.get("player2", "")
        meta = [f"🎾 <b>{g.get('tournament', '')}</b>",
                f"court <b>{str(g.get('surface') or '').title() or '—'}</b>",
                fmt_time_et(g.get("match_time"))]
        teams = (f"<div class='osp-ss-team'><div><div class='osp-ss-name'>{p1}</div></div></div>"
                 f"<div class='osp-ss-hero' style='text-align:center;'>"
                 f"<div class='osp-ss-name' style='color:var(--faint);'>vs</div></div>"
                 f"<div class='osp-ss-team home'><div><div class='osp-ss-name'>{p2}</div></div></div>")
    else:
        away, home = g.get("away_team", ""), g.get("home_team", "")
        meta = [f"⚽ proj <b>{_num(g.get('home_exp'))}–{_num(g.get('away_exp'))}</b> goals",
                fmt_time_et(g.get("game_time"))]
        teams = (f"<div class='osp-ss-team'>{assets.team_badge_html(sport, away, 40)}"
                 f"<div><div class='osp-ss-name'>{away}</div></div></div>"
                 f"<div class='osp-ss-hero' style='text-align:center;'>"
                 f"<div class='osp-ss-name'>{_num(g.get('proj_total'))}</div>"
                 f"<small style='color:var(--muted);font-family:var(--disp);letter-spacing:.05em;'>PROJ GOALS</small></div>"
                 f"<div class='osp-ss-team home'><div><div class='osp-ss-name'>{home}</div></div>"
                 f"{assets.team_badge_html(sport, home, 40)}</div>")
    return (f"<div class='osp-ss-head'><div class='osp-ss-teams'>{teams}</div>"
            f"<div class='osp-ss-meta'>{''.join(f'<span>{m}</span>' for m in meta)}</div></div>")


def _match_priced(sport: str, g: dict) -> str:
    """Priced-sides table (model % vs market → EV) for a match, from _game_edges."""
    edges = [e for e in _game_edges(sport, g)
             if e.get("ev") is not None and pd.notna(e["ev"])]
    if not edges:
        return f"<div class='osp-ss-card'>{_gap('no market prices yet')}</div>"
    rows = []
    for e in sorted(edges, key=lambda x: -x["ev"]):
        col = "good" if e["ev"] > 0.005 else "neg" if e["ev"] < -0.005 else "faint"
        rows.append(
            f"<tr><td><b>{e['market']}</b></td><td>{e['bet']}</td>"
            f"<td>{_pct(e.get('model_prob'))}</td>"
            f"<td>{fmt_american(e['price'])}</td>"
            f"<td style='color:var(--{col},var(--muted));font-weight:700;'>"
            f"{e['ev'] * 100:+.1f}%</td></tr>")
    return ("<div class='osp-ss-card'><table class='osp-ss-mtab'>"
            "<tr><th>Market</th><th>Bet</th><th>Model</th><th>Price</th><th>EV</th></tr>"
            f"{''.join(rows)}</table><div class='osp-ss-note'>Projection-only until "
            "an odds feed is wired; priced sides flow through the edge gate in "
            "probation.</div></div>")


def match_sheet_html(sport: str, g: dict, best_line: dict | None = None) -> str:
    """Dark Sharp Sheet for the match-model sports (soccer 1X2/totals, tennis
    player match-win). Never-raises."""
    try:
        body = (_match_header(sport, g)
                + _section("Markets", "model win % · price · edge",
                           _match_market_cards(sport, g))
                + _section("Best Available", "line-shop across books",
                           _ss_bestline(g, best_line))
                + _section("Priced Sides", "", _match_priced(sport, g)))
        return _SS_STYLE + _SS_STYLE2 + f"<div class='osp-ss'>{body}</div>"
    except Exception:
        log.exception("match_sheet_html failed for %s", sport)
        title = (f"{g.get('player1', '')} vs {g.get('player2', '')}"
                 if sport in ("ATP", "WTA")
                 else f"{g.get('away_team', '')} @ {g.get('home_team', '')}")
        return _fallback_card(title)


def _gap(what: str = "data gap") -> str:
    """A visible DATA GAP chip — never a silent blank when a field is missing."""
    return (f"<span class='osp-ss-gap' title='This input is not available; the "
            f"model does not fabricate it.'>⚠ {what}</span>")


def _num_or_gap(v, fmt="{:.2f}", gap="gap"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return _gap(gap)
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return _gap(gap)


def _ss_pitching(sport: str, g: dict, pitching: dict | None) -> str:
    """Starters (hand, IP, K/9, BB/9, xFIP, times-through-order flag) + a bullpen
    availability line per team. DATA GAP chips for any missing driver."""
    if sport != "MLB":
        return ""
    p = pitching or {}

    def sp(side, color):
        d = (p.get(f"{side}_sp") or {})
        name = d.get("name") or g.get(f"{side}_pitcher")
        if not name and not d:
            return f"<div class='osp-ss-sp'>{_gap('starter TBD')}</div>"
        pid = d.get("id") or g.get(f"{side}_pitcher_id")
        hand = d.get("hand")
        hand_txt = f"{hand}HP" if hand else _gap("hand")
        tto = ("<span class='osp-ss-flag'>3rd-time penalty</span>"
               if d.get("tto_flag") else "")
        stats = " · ".join([
            f"IP {_num_or_gap(d.get('ip'), '{:.1f}')}",
            f"K/9 {_num_or_gap(d.get('k9'), '{:.1f}')}",
            f"BB/9 {_num_or_gap(d.get('bb9'), '{:.1f}')}",
            f"xFIP {_num_or_gap(d.get('xfip'), '{:.2f}')}"])
        return (f"<div class='osp-ss-sp'>"
                f"<div class='osp-ss-spn' style='color:{color};'>"
                f"{player_link(name, g.get('game_pk'), sport)} <small>{hand_txt}</small> {tto}</div>"
                f"<div class='osp-ss-spd'>{stats}</div></div>")

    def pen(side):
        d = (p.get(f"{side}_bullpen") or {})
        fatigue = d.get("fatigue")
        if fatigue:
            tone = {"heavy": "neg", "moderate": "mid", "rested": "good"}.get(fatigue, "faint")
            fat = f"<span class='osp-ss-badge t-{tone}'>{fatigue}</span>"
        else:
            fat = _gap("bullpen")
        ip = f"proj {_num_or_gap(d.get('proj_ip'), '{:.1f}')} IP" if d else ""
        if "unavailable" in d:
            un = (f" · out: {', '.join(d['unavailable'])}" if d["unavailable"]
                  else " · all available")
        else:
            un = f" · {_gap('availability')}" if d else ""
        return f"<div class='osp-ss-pen'>Bullpen {fat} {ip}{un}</div>"

    a, h = g.get("away_team", ""), g.get("home_team", "")
    return (
        "<div class='osp-ss-pit'>"
        f"<div class='osp-ss-pit-col'>{sp('away','var(--link,#8a1f2b)')}{pen('away')}</div>"
        f"<div class='osp-ss-pit-col right'>{sp('home','var(--good,#2f7a4a)')}{pen('home')}</div>"
        "</div>")


def _ss_markets_clv(sport: str, g: dict, calls: list, clv: dict | None) -> str:
    """Market table framed the way a bettor reads it: our de-vigged fair prob vs
    the market's implied prob -> edge% (baseline labelled), plus realized CLV per
    market (the headline metric). CLV is realized, not a fabricated forecast."""
    if not calls:
        return ""
    clv = clv or {}
    key = {"Moneyline": "moneyline", "Total": "total",
           "Run Line": "spread", "Spread": "spread"}
    head = ("<tr><th>Market</th><th>Pick</th><th>Fair</th><th>Line</th><th>Price</th>"
            "<th>Implied</th><th>Edge</th><th>CLV (realized)</th></tr>")
    body = []
    for c in calls:
        prob = _mcf(c.get("prob"))
        price = c.get("price")
        implied = _implied(price) if price is not None and pd.notna(price) else None
        edge = (prob - implied) if (prob is not None and implied is not None) else None
        ecol = ("good" if (edge or 0) > 0.005 else "neg" if (edge or 0) < -0.005 else "faint")
        cst = clv.get(key.get(c["label"], "")) or {}
        avg, n = cst.get("avg_clv"), cst.get("clv_n") or cst.get("n")
        clv_txt = (f"<b style='color:var(--{'good' if (avg or 0)>=0 else 'neg'});'>"
                   f"{avg*100:+.1f}%</b> <small>n={n}</small>"
                   if avg is not None else _gap("no sample"))
        # a moneyline has no line by definition — N/A ("—"), not a DATA GAP
        line_cell = ("<span style='color:var(--faint);'>—</span>"
                     if c["label"] == "Moneyline"
                     else _num_or_gap(c.get("line"), "{:g}", "line"))
        body.append(
            f"<tr><td><b>{c['label']}</b></td><td>{c.get('pick','')}</td>"
            f"<td>{_pct(prob)}</td><td>{line_cell}</td>"
            f"<td>{fmt_american(price) or _gap('no line')}</td>"
            f"<td>{_pct(implied) if implied is not None else _gap('')}</td>"
            f"<td style='color:var(--{ecol},var(--muted));font-weight:700;'>"
            f"{(f'{edge*100:+.1f}%') if edge is not None else _gap('')}</td>"
            f"<td>{clv_txt}</td></tr>")
    return ("<div class='osp-ss-card'><table class='osp-ss-mtab'>"
            f"{head}{''.join(body)}</table>"
            "<div class='osp-ss-note'>Edge% = our de-vigged fair probability − the "
            "market's implied probability. CLV is our realized closing-line value on "
            "this market to date (the thesis metric), not a forecast.</div></div>")


def _total_ci_from_probs(g: dict):
    """(p10, p50, p90) total from the sim's over-probability curve, or None.
    over_probs maps line->P(over); we invert that CDF at 0.9/0.5/0.1."""
    op = g.get("over_probs") or {}
    pts = []
    for line, pover in op.items():
        try:
            pts.append((float(line), 1.0 - float(pover)))  # P(total <= line)
        except (TypeError, ValueError):
            continue
    if len(pts) < 3:
        return None
    pts.sort()

    def q(target):
        for i in range(1, len(pts)):
            (x0, c0), (x1, c1) = pts[i - 1], pts[i]
            if c0 <= target <= c1 and c1 > c0:
                return x0 + (x1 - x0) * (target - c0) / (c1 - c0)
        return pts[-1][0] if target > pts[-1][1] else pts[0][0]

    return q(0.10), q(0.50), q(0.90)


def _ss_uncertainty(sport: str, g: dict, uncertainty: dict | None) -> str:
    """Median + 80% interval for the total (and the win-prob split), from the sim
    spread — replaces the false precision of a single point total."""
    ci = (uncertainty or {}).get("total_ci") or _total_ci_from_probs(g)
    hw = _mcf(g.get("home_win_prob"))
    total_html = (
        f"<b>{ci[1]:.1f}</b> <span class='osp-ss-ci'>80% CI {ci[0]:.1f}–{ci[2]:.1f}</span>"
        if ci else _gap("no sim spread"))
    win_html = (f"{_last(g.get('home_team',''))} <b>{hw*100:.0f}%</b> / "
                f"{_last(g.get('away_team',''))} <b>{(1-hw)*100:.0f}%</b>"
                if hw is not None else _gap(""))
    return ("<div class='osp-ss-unc'>"
            f"<div><span class='osp-ss-ul'>Projected total</span>{total_html}</div>"
            f"<div><span class='osp-ss-ul'>Win probability</span>{win_html}</div></div>")


def _ss_context(sport: str, g: dict, context: dict | None) -> str:
    """Park factor, weather (wind vs CF — tied to the Total), and umpire tendency.
    The levers that move a total before any team stat does."""
    if sport != "MLB":
        return ""
    ctx = context or {}
    pf = ctx.get("park_factor")
    if pf is None:
        try:
            from project547 import parks
            pf = parks.factor(g.get("home_team", ""))
        except Exception:
            pf = None
    pf_txt = (f"{pf:.2f}× " + ("hitter" if pf > 1.02 else "pitcher" if pf < 0.98 else "neutral")
              if pf else _gap("park"))
    wx = g.get("weather") or ctx.get("weather") or {}
    temp, wind = wx.get("temp_f"), wx.get("wind_mph")
    wdir = wx.get("wind_dir")
    wx_txt = (f"{_num_or_gap(temp, '{:.0f}')}°F · wind "
              f"{_num_or_gap(wind, '{:.0f}')} mph"
              + (f" {wdir}" if wdir else "")) if wx else _gap("weather")
    ump = g.get("umpire") or ctx.get("umpire") or {}
    ki, ri = ump.get("k_idx") or ump.get("k_index"), ump.get("runs_idx") or ump.get("runs_index")
    ump_txt = (f"{ump.get('name','ump')} · K {_num_or_gap(ki,'{:.2f}')}× · "
               f"R {_num_or_gap(ri,'{:.2f}')}×"
               if ump.get("name") else _gap("umpire TBD"))
    cells = [("🏟️ Park", pf_txt), ("🌤️ Weather → Total", wx_txt), ("🧑‍⚖️ Umpire", ump_txt)]
    return ("<div class='osp-ss-ctx'>" + "".join(
        f"<div class='osp-ss-ctxc'><span class='osp-ss-cl'>{lbl}</span>{val}</div>"
        for lbl, val in cells) + "</div>")


def _ss_calibration(calibration: dict | None) -> str:
    """Footer receipt: per-market out-of-fold predicted vs actual hit-rate + n —
    the falsifiable anchor under the confidence score. The table ALWAYS renders;
    any missing cell is a DATA GAP chip, never a blank (a hollow calibration is a
    signal, not a no-op — it also caps confidence at LEAN, see _confidence)."""
    markets = list(calibration.keys()) if calibration else ["Moneyline", "Total", "Run Line"]
    rows = []
    for mkt in markets:
        d = (calibration or {}).get(mkt) or {}
        pred, act, n = d.get("pred"), d.get("actual"), d.get("n")
        gapp = (abs(pred - act) if pred is not None and act is not None else None)
        col = ("good" if gapp is not None and gapp <= 0.03 else
               "mid" if gapp is not None and gapp <= 0.06 else "neg")
        rows.append(
            f"<tr><td><b>{mkt}</b></td>"
            f"<td>{_pct(pred) if pred is not None else _gap('')}</td>"
            f"<td>{_pct(act) if act is not None else _gap('')}</td>"
            f"<td style='color:var(--{col},var(--muted));'>"
            f"{(f'{gapp*100:.1f} pt') if gapp is not None else _gap('')}</td>"
            f"<td>{('<small>n='+str(n)+'</small>') if n is not None else _gap('')}</td></tr>")
    note = ("Predicted vs realized hit-rate on held-out games. A small gap = the "
            "number means what it says." if calibration else
            "<b style='color:var(--mid,#a9791b);'>Reliability feed not loaded — "
            "confidence is capped at LEAN until this is wired.</b>")
    body = ("<div class='osp-ss-card'><table class='osp-ss-mtab'>"
            "<tr><th>Market</th><th>Predicted</th><th>Actual</th><th>Gap</th><th>Sample</th></tr>"
            f"{''.join(rows)}</table><div class='osp-ss-note'>{note}</div></div>")
    return _section("Calibration Receipt", "out-of-fold reliability per market", body)


def _confidence(sport: str, g: dict, calls: list, data: dict) -> dict:
    """Multi-factor confidence: lineup readiness × edge clarity × calibration ×
    data completeness. Returns {score, label, cap} where cap is the best tier any
    market may reach when its key driver is a DATA GAP."""
    # 1) lineup readiness
    state = lineup_status(sport, g)["state"]
    readiness = {"confirmed": 1.0, "partial": 0.7, "pending": 0.45}.get(state, 0.45)
    # 2) edge clarity — best positive edge, scaled
    best = max([abs(c.get("ev") or 0) for c in calls], default=0)
    edge_clarity = min(1.0, best / 0.06) if best else 0.3
    # 3) calibration quality — small OOF gaps = high
    cal = data.get("calibration") or {}
    gaps = [abs(d["pred"] - d["actual"]) for d in cal.values()
            if d.get("pred") is not None and d.get("actual") is not None]
    calibration_q = (max(0.0, 1.0 - (sum(gaps) / len(gaps)) / 0.06)) if gaps else 0.5
    # 4) data completeness — share of key MLB drivers present
    drivers = []
    if sport == "MLB":
        pit = data.get("pitching") or {}
        drivers = [
            bool((pit.get("away_sp") or {}).get("xfip") is not None),
            bool((pit.get("home_sp") or {}).get("xfip") is not None),
            bool(g.get("umpire")), bool(g.get("weather")),
            bool(_total_ci_from_probs(g)),
        ]
    completeness = (sum(drivers) / len(drivers)) if drivers else 0.6
    score = round(0.30 * readiness + 0.25 * edge_clarity
                  + 0.20 * calibration_q + 0.25 * completeness, 2)
    # tier cap: a missing key driver caps strong plays at LEAN — the tier can't
    # outrun the data. Blank calibration (no falsifiable anchor) or a missing SP
    # driver both cap; so does an overall low score.
    cal_missing = not gaps
    sp_gap = sport == "MLB" and completeness < 0.6
    cap = "lean" if sp_gap or cal_missing or score < 0.5 else None
    label = ("High" if score >= 0.75 else "Moderate" if score >= 0.5 else "Guarded")
    return {"score": score, "label": label, "cap": cap,
            "drivers": {"lineups": round(readiness, 2), "edge": round(edge_clarity, 2),
                        "calibration": round(calibration_q, 2),
                        "completeness": round(completeness, 2)}}


_SS_STYLE2 = """<style>
.osp-ss-gap{display:inline-block;font-size:.66rem;font-weight:700;letter-spacing:.03em;
  color:var(--mid,#e3b341);background:rgba(227,179,65,.14);border:1px solid rgba(227,179,65,.5);
  border-radius:5px;padding:0 5px;text-transform:uppercase;vertical-align:middle}
.osp-ss-pit{display:grid;grid-template-columns:1fr 1fr;gap:12px;background:var(--card);
  border:1.5px solid var(--line);border-radius:13px;padding:12px 15px}
.osp-ss-pit-col.right{text-align:right}
.osp-ss-spn{font-family:var(--disp);font-weight:600;font-size:1rem}
.osp-ss-spn small{color:var(--muted);font-weight:500;font-size:.72rem}
.osp-ss-spd{font-size:.76rem;color:var(--muted);margin-top:2px}
.osp-ss-flag{font-size:.58rem;font-weight:700;color:var(--neg);background:rgba(162,59,45,.12);
  border-radius:4px;padding:1px 5px;text-transform:uppercase}
.osp-ss-pen{font-size:.74rem;color:var(--muted);margin-top:6px}
.osp-ss-badge{font-size:.6rem;font-weight:700;padding:1px 6px;border-radius:5px;text-transform:uppercase}
.osp-ss-mtab{width:100%;border-collapse:collapse;font-size:.8rem}
.osp-ss-mtab th{font-family:var(--disp);font-size:.66rem;letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);text-align:right;padding:6px 8px;border-bottom:1.5px solid var(--line)}
.osp-ss-mtab th:first-child,.osp-ss-mtab td:first-child{text-align:left}
.osp-ss-mtab td{padding:6px 8px;text-align:right;border-top:1px solid var(--line);
  font-variant-numeric:tabular-nums;font-size:.84rem}
.osp-ss-note{font-size:.68rem;color:var(--muted);margin-top:8px;line-height:1.5}
.osp-ss-unc{display:flex;gap:26px;flex-wrap:wrap;background:var(--card2);border:1px solid var(--line);
  border-radius:11px;padding:11px 15px;font-size:.9rem}
.osp-ss-ul{display:block;font-family:var(--disp);font-size:.6rem;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.osp-ss-ci{font-size:.72rem;color:var(--muted);font-weight:500;margin-left:6px}
.osp-ss-ctx{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.osp-ss-ctxc{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:9px 12px;
  font-size:.82rem;font-weight:600}
.osp-ss-cl{display:block;font-family:var(--disp);font-size:.58rem;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.osp-ss-confbar{display:inline-flex;align-items:center;gap:7px}
.osp-ss-conftrack{width:74px;height:6px;border-radius:4px;background:var(--card2);overflow:hidden;
  display:inline-block;vertical-align:middle}
.osp-ss-conffill{height:100%;background:var(--good,#2f7a4a)}
.osp-ss-adv{margin-top:16px;border:1.5px solid var(--line);border-radius:12px;overflow:hidden}
.osp-ss-adv>summary{cursor:pointer;list-style:none;padding:12px 16px;font-family:var(--disp);
  font-weight:600;letter-spacing:.05em;text-transform:uppercase;font-size:.74rem;color:var(--text);
  display:flex;align-items:center;gap:9px;background:var(--card)}
.osp-ss-adv>summary::-webkit-details-marker{display:none}
.osp-ss-adv>summary::after{content:"▸";margin-left:auto;color:var(--muted);transition:transform .15s}
.osp-ss-adv[open]>summary::after{transform:rotate(90deg)}
.osp-ss-advbody{padding:0 16px 12px}
@media (max-width:560px){
  .osp-ss-pit,.osp-ss-ctx,.osp-ss-props,.osp-ss-split,.osp-ss-markets{grid-template-columns:1fr!important}
  .osp-ss-teams{grid-template-columns:1fr!important}
  .osp-ss-mtab{font-size:.72rem} .osp-ss-mtab th,.osp-ss-mtab td{padding:4px 5px}
  .osp-ss-name{font-size:1.05rem}
}
</style>"""


def sharp_sheet_html(sport: str, g: dict, matchup: dict, *, window: str = "l5",
                     min_edge: float = 0.02, gate_table=None, bankroll: float = 0,
                     props=None, best_line=None, data=None) -> str:
    """The one-page Sharp Sheet: never-raises wrapper. ``data`` carries the v2
    extras (pitching, clv, uncertainty, context, calibration, records) per
    docs/sharpsheet_schema.md; any missing piece renders a DATA GAP chip."""
    try:
        return _sharp_sheet_impl(sport, g, matchup or {}, window=window,
                                 min_edge=min_edge, gate_table=gate_table,
                                 bankroll=bankroll, props=props, best_line=best_line,
                                 data=data or {})
    except Exception:
        log.exception("sharp_sheet_html failed for %s @ %s",
                      (g or {}).get("away_team"), (g or {}).get("home_team"))
        try:
            return matchup_card_html(sport, g, matchup or {}, window=window,
                                     min_edge=min_edge, gate_table=gate_table,
                                     bankroll=bankroll)
        except Exception:
            return _fallback_card(
                f"{(g or {}).get('away_team', '')} @ {(g or {}).get('home_team', '')}")


def _section(title: str, hint: str, body: str) -> str:
    if not body:
        return ""
    hint_html = f"<span class='hint'>{hint}</span>" if hint else ""
    return (f"<div class='osp-ss-sec'><div class='osp-ss-sh'>{title}{hint_html}</div>{body}</div>")


def _details(summary: str, inner: str) -> str:
    """Collapsible block — keeps the sheet short by default; the dense stat
    tables live one tap away."""
    if not inner:
        return ""
    return (f"<details class='osp-ss-adv'><summary>{summary}</summary>"
            f"<div class='osp-ss-advbody'>{inner}</div></details>")


def _sharp_sheet_impl(sport, g, matchup, *, window, min_edge, gate_table,
                      bankroll, props, best_line, data=None) -> str:
    data = data or {}
    away, home = g.get("away_team", ""), g.get("home_team", "")
    n = matchup.get("n_teams", 30)
    win_label = matchup.get("window_label", window.upper())
    calls = _mc_market_calls(sport, g, min_edge, gate_table=gate_table, bankroll=bankroll)

    # confidence: multi-factor (readiness × edge × calibration × completeness); a
    # missing key driver caps every market at LEAN so the tier can't outrun data.
    conf = _confidence(sport, g, calls, data)
    if conf.get("cap") == "lean":
        for c in calls:
            if c["decision"] == "PLAY":
                c["decision"] = "LEAN"
    conf_strip = (
        "<div class='osp-ss-sec'><div class='osp-ss-card' style='display:flex;gap:16px;"
        "align-items:center;flex-wrap:wrap;'>"
        f"<b class='disp' style='font-family:var(--disp);letter-spacing:.04em;'>Confidence "
        f"<span style='font-size:1.15rem;'>{conf['label']}</span> · {conf['score']:.2f}</b>"
        "<span class='osp-ss-confbar'><span class='osp-ss-conftrack'>"
        f"<span class='osp-ss-conffill' style='width:{int(conf['score']*100)}%;'></span></span></span>"
        "<span style='font-size:.72rem;color:var(--muted);'>"
        f"lineups {conf['drivers']['lineups']:.2f} · edge {conf['drivers']['edge']:.2f} · "
        f"calibration {conf['drivers']['calibration']:.2f} · completeness "
        f"{conf['drivers']['completeness']:.2f}"
        + ("  ·  <b style='color:var(--mid,#a9791b);'>capped at LEAN — a key driver is a DATA GAP</b>"
           if conf.get("cap") == "lean" else "")
        + "</span></div></div>")

    # pitching strip (MLB) + context strip + uncertainty band
    pitching = _section("Starting Pitching",
                        "hand · IP · K/9 · BB/9 · xFIP · times-through-order",
                        _ss_pitching(sport, g, data.get("pitching")))
    uncertainty = _section("Projection & Uncertainty", "median + 80% interval from the sim",
                           _ss_uncertainty(sport, g, data.get("uncertainty")))
    context = _section("Context — the levers", "park · weather (wind→total) · umpire",
                       _ss_context(sport, g, data.get("context")))

    # markets: at-a-glance cards + the fair/implied/edge% + CLV table
    cards = _ss_market_cards(calls)
    clv_tab = _ss_markets_clv(sport, g, calls, data.get("clv"))
    markets = _section("Markets & CLV",
                       "our de-vig fair % vs the market · edge% · realized CLV (the headline)",
                       (cards + clv_tab))
    bestln = _section("Best Available", "line-shop across books · locks at first pitch",
                      _ss_bestline(g, best_line))

    # matchup breakdown (reuse the stat tables + supporting)
    a_rows = matchup.get("away_off_vs_home_def") or []
    h_rows = matchup.get("home_off_vs_away_def") or []
    supp = _supporting_set(sport)
    a_prim = [r for r in a_rows if r["stat"] not in supp]
    h_prim = [r for r in h_rows if r["stat"] not in supp]
    tables = ""
    if a_prim:
        tables += _mc_stat_table(sport, n, window, win_label,
                                 f"{away} offense vs {home} defense", a_prim, away, home)
    if h_prim:
        tables += _mc_stat_table(sport, n, window, win_label,
                                 f"{home} offense vs {away} defense", h_prim, home, away)
    a_supp = {r["stat"]: r for r in a_rows if r["stat"] in supp}
    h_supp = {r["stat"]: r for r in h_rows if r["stat"] in supp}
    tables += _mc_supporting(sport, n, window, win_label, away, home, a_supp, h_supp)
    breakdown = _section("Matchup Breakdown",
                         "offense vs the opponent's matching defense · rank pills are league ranks",
                         f"<div class='osp-ss-card'>{tables}</div>" if tables else "")

    trends = _section("Game Trends", "",
                      (f"<div class='osp-ss-card'>{_mc_trends(matchup.get('trends'), away, home)}</div>"
                       if _mc_trends(matchup.get("trends"), away, home) else ""))

    lineups = _section("Lineups", "tap a name for the player card",
                       _ss_lineups(sport, g))

    top = top_game_props(sport, props, away, home) if props else []
    props_sec = _section("Top Props", "best model edges in this game · gate-capped stake",
                         _ss_props(sport, top))

    # model read (reuse the decision block from _mc_market_calls)
    read = _ss_read(calls)
    read_sec = _section("Model Read · Bet Ticket",
                        "the call, the why, and the ¼-Kelly stake",
                        f"<div class='osp-ss-card'>{read}</div>" if read else "")

    calib = _ss_calibration(data.get("calibration"))
    # Lead with the call (markets/CLV + read), then the drivers; tuck the dense
    # offense-vs-defense tables + calibration behind an "Advanced" fold.
    advanced = _details("Advanced — full offense-vs-defense breakdown & calibration",
                        breakdown + calib)
    return (_SS_STYLE + _SS_STYLE2 + "<div class='osp-ss'>"
            + _ss_header(sport, g, matchup)
            + conf_strip
            + markets + read_sec
            + pitching + uncertainty + context + bestln
            + trends + lineups + props_sec
            + advanced
            + "</div>")


def _ss_read(calls: list) -> str:
    if not calls:
        return ""
    tone = {"PLAY": "good", "LEAN": "mid", "VERIFY": "neg", "PASS": "faint"}
    rows = []
    for c in calls:
        col = tone.get(c["decision"], "faint")
        ev = c.get("ev")
        ev_txt = (f"{ev * 100:+.1f}% EV" if ev is not None and pd.notna(ev) else "no priced edge")
        stake = (f" · {c['stake_units']:g}u" if c.get("stake_units") else "")
        rows.append(
            f"<div style='display:flex;gap:12px;align-items:center;padding:8px 4px;"
            f"border-top:1px solid var(--line);'>"
            f"<b style='font-family:var(--disp);min-width:88px;color:var(--{col},var(--muted));"
            f"text-transform:uppercase;font-size:.74rem;letter-spacing:.04em;'>{c['label']}</b>"
            f"<span style='flex:1;font-size:.82rem;'>{c['pick']} — "
            f"<span style='color:var(--{col},var(--muted));font-weight:700;'>{c['decision']}</span> "
            f"<span style='color:var(--muted);'>({ev_txt}, conf {c['conf']:.1f}/10{stake})</span></span></div>")
    return "".join(rows)


# emoji per decision — used in the collapsed expander label so a bettor can
# scan a whole slate and see which games have a live call without opening each.
_DECISION_DOT = {"PLAY": "🟢", "LEAN": "🟡", "VERIFY": "🟠", "PASS": "⚪"}
_DECISION_RANK = {"PLAY": 3, "LEAN": 2, "VERIFY": 1, "PASS": 0}


def sheet_headline(sport: str, g: dict, *, min_edge: float = 0.02,
                   gate_table=None, bankroll: float = 0) -> tuple[str, bool]:
    """A one-line summary for the collapsed game card: matchup + the single best
    actionable call. Returns (markdown_label, should_auto_expand). Never raises —
    a bad game still gets a plain matchup label so the feed keeps rendering."""
    try:
        # Match-model sports (tennis player-Elo, soccer Dixon-Coles) don't use the
        # team-sport market calls — summarise from the model's own probabilities.
        if sport in ("ATP", "WTA"):
            p1, p2 = g.get("player1", ""), g.get("player2", "")
            title = f"{p1} vs {p2}"
            q1, q2 = _mcf(g.get("player1_win_prob")), _mcf(g.get("player2_win_prob"))
            if q1 is not None and q2 is not None:
                fav, fp = (p1, q1) if q1 >= q2 else (p2, q2)
                return f"⚪  {title}  ·  model **{_last(fav)} {_pct(fp)}**", False
            return f"⚪  {title}", False
        if sport in ("MLS",) or ("home_win_prob" in g and "away_win_prob" in g
                                 and g.get("draw_prob") is not None):
            away, home = g.get("away_team", ""), g.get("home_team", "")
            title = f"{away} @ {home}"
            trio = [(_last(home), _mcf(g.get("home_win_prob"))),
                    ("Draw", _mcf(g.get("draw_prob"))),
                    (_last(away), _mcf(g.get("away_win_prob")))]
            trio = [(n, p) for n, p in trio if p is not None]
            if trio:
                n, p = max(trio, key=lambda t: t[1])
                return f"⚪  {title}  ·  model **{n} {_pct(p)}**", False
            return f"⚪  {title}", False
        title = f"{g.get('away_team', '')} @ {g.get('home_team', '')}"
        calls = _mc_market_calls(sport, g, min_edge, gate_table=gate_table,
                                 bankroll=bankroll)
        if not calls:
            return f"{title}  ·  no priced markets yet", False
        best = max(calls, key=lambda c: (
            _DECISION_RANK.get(c["decision"], 0),
            c["ev"] if c.get("ev") is not None else -9))
        dot = _DECISION_DOT.get(best["decision"], "⚪")
        ev = best.get("ev")
        if best["decision"] in ("PLAY", "LEAN") and ev is not None and pd.notna(ev):
            tail = (f"**{best['decision']} {best['label']}** {best['pick']} "
                    f"({ev * 100:+.1f}% EV)")
            return f"{dot}  {title}  ·  {tail}", True
        return f"{dot}  {title}  ·  no edge — pass", False
    except Exception:
        log.exception("sheet_headline failed")
        away, home = g.get("away_team", ""), g.get("home_team", "")
        return f"{away} @ {home}", False
