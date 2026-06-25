"""Presentation helpers for the dashboard: formatting, view preparation,
and the cross-sport best-bets board. Pure functions over the latest.json
shapes so they're testable without Streamlit."""

from __future__ import annotations

import math
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app import assets

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
    return df.sort_values("ev", ascending=False).reset_index(drop=True)


def _game_edges(sport: str, g: dict) -> list[dict]:
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
    add("Total", f"Over {g.get('total_line')}", g.get("total_line"),
        g.get("over_odds"), g.get("model_over_prob"), g.get("over_ev"),
        shop_mkt="total", sidekey="over")
    mop = g.get("model_over_prob")
    add("Total", f"Under {g.get('total_line')}", g.get("total_line"),
        g.get("under_odds"), (1 - mop) if mop is not None else None,
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


def _best_edge(game: dict) -> tuple[str, float] | None:
    """Largest positive model edge on a game, as (label, ev)."""
    cands = []
    for r in _game_edges("", game):
        if r["ev"] is not None and pd.notna(r["ev"]):
            cands.append((r["bet"], float(r["ev"])))
    cands = [c for c in cands if c[1] > 0]
    return max(cands, key=lambda c: c[1]) if cands else None


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
    time = fmt_time_et(g.get("game_time"))
    total = g.get("total_line") or g.get("proj_total")

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

    def side(badge, name, exp, wp, fav, pitcher=None):
        weight = "700" if fav else "500"
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
            f"<div style='color:var(--muted);font-size:0.8rem;'>win {_pct(wp)}</div>"
            f"{sp_row}</div>"
            f"<div style='margin-left:auto;font-size:1.5rem;font-weight:700;'>"
            f"{_num(exp)}</div></div>"
        )

    home_fav = (h_wp or 0) >= (a_wp or 0)
    return (
        "<div style='background:var(--card);border:1px solid var(--line);border-radius:14px;"
        "padding:14px 16px;margin-bottom:12px;"
        "box-shadow:0 1px 2px rgba(0,0,0,0.4),0 8px 22px rgba(0,0,0,0.4);'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin-bottom:8px;'>"
        f"<span style='color:var(--muted);font-size:0.78rem;'>"
        f"{time} · O/U {_num(total)} · proj total {_num(g.get('proj_total'))}</span>"
        f"{_status_badge(sport, g)}</div>"
        f"{side(a_badge, away, a_exp, a_wp, not home_fav, g.get('away_pitcher'))}"
        "<div style='height:8px;'></div>"
        f"{side(h_badge, home, h_exp, h_wp, home_fav, g.get('home_pitcher'))}"
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

def _grade(ev) -> tuple[str, str]:
    """Letter grade + color for a model edge (EV). A = strong, F = negative."""
    if ev is None or (isinstance(ev, float) and pd.isna(ev)):
        return ("—", "var(--faint)")
    e = float(ev) * 100
    if e >= 8:
        return ("A", "var(--good)")
    if e >= 5:
        return ("B", "var(--good)")
    if e >= 3:
        return ("C", "var(--mid)")
    if e >= 1:
        return ("D", "var(--mid)")
    if e >= 0:
        return ("D-", "var(--faint)")
    return ("F", "var(--neg)")


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
    gl, gc = _grade(ev)
    play = ev is not None and pd.notna(ev) and ev >= min_edge
    dco, dbg = ("var(--good)", "#0f2c1c") if play else ("var(--muted)", "var(--line)")
    decision = "PLAY" if play else "PASS"
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
        if 0.02 <= ev < 0.06:
            tag, kind, vcol = "PLAY · 2–6% BAND", "play", "var(--good)"
            why3 = ("Squarely in our curated <b>2–6% band</b> — the range our record hits "
                    "~60%. Logged at this price and graded to the close. No revisions.")
        elif ev >= 0.08:
            tag, kind, vcol = "VERIFY — OFF-MARKET", "warn", "var(--mid)"
            why3 = ("Edge is large enough that the market likely knows something we don't "
                    "(injury, lineup, bullpen). We flag these — we don't chase them.")
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
    bg = "background:rgba(47,122,74,.06);" if hl else ""
    col = f"color:{color};" if color else ""
    return (f"<div style='padding:11px 8px;text-align:center;border-right:1px solid var(--line);{bg}'>"
            f"<div style='font-family:var(--disp);font-size:.58rem;letter-spacing:.06em;"
            f"color:var(--muted);'>{label}</div>"
            f"<div style='font-family:var(--disp);font-weight:600;font-size:1.1rem;"
            f"margin-top:3px;{col}'>{value}</div></div>")


def _vmeta(label, value, color=None) -> str:
    col = f"color:{color};" if color else ""
    return (f"<div><div style='font-family:var(--disp);font-size:.58rem;letter-spacing:.06em;"
            f"color:var(--muted);'>{label}</div>"
            f"<div style='font-family:var(--disp);font-weight:600;font-size:1rem;{col}'>{value}</div></div>")


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
    head = (f"# {sport} — {away} @ {home}\n"
            f"*{fmt_time_et(g.get('game_time'))} ET · "
            f"O/U {_num(g.get('total_line') or g.get('proj_total'))} · "
            f"proj {_num(_exp(g, 'away'))}–{_num(_exp(g, 'home'))}*")

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
        parts.append("## Biggest stat mismatches\n" + "\n".join(stars[:6]))

    parts.append("_Project 54.7 — model estimates, not financial "
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

    parts.append("_Project 54.7 — model estimates, not financial "
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
    lines += ["", "_Project 54.7 — ¼-Kelly staking, model estimates, "
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
