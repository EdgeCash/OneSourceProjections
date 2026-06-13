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

    def add(market, bet, line, price, prob, ev):
        if ev is not None and price is not None and pd.notna(ev) and pd.notna(price):
            rows.append({"sport": sport, "type": "Game", "market": market,
                         "bet": bet, "game": matchup, "line": line,
                         "price": price, "model_prob": prob, "ev": ev,
                         "kelly": None, "time": g.get("game_time")})

    for side in ("home", "away"):
        add("Moneyline", f"{g.get(f'{side}_team')} ML", None,
            g.get(f"{side}_ml"), g.get(f"{side}_win_prob"),
            g.get(f"{side}_ml_ev", g.get(f"{side}_ev")))
    add("Total", f"Over {g.get('total_line')}", g.get("total_line"),
        g.get("over_odds"), g.get("model_over_prob"), g.get("over_ev"))
    mop = g.get("model_over_prob")
    add("Total", f"Under {g.get('total_line')}", g.get("total_line"),
        g.get("under_odds"), (1 - mop) if mop is not None else None,
        g.get("under_ev"))
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
    market_line = (f"<div style='color:#8b949e;font-size:0.76rem;margin-top:6px;'>"
                   f"{' · '.join(odds_bits)}{_weather_txt(g)}</div>"
                   if (odds_bits or g.get('weather')) else "")

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
        f"{market_line}"
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


def _adv_badge(adv: int) -> str:
    """Center advantage marker: filled green chevrons when the offense
    out-ranks the defense it faces, a muted dash otherwise."""
    if not adv:
        return "<span style='color:#39414d;'>·</span>"
    return (f"<span style='color:#0d1117;background:#2ea043;border-radius:5px;"
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
        "<tr style='color:#7d8794;font-size:0.62rem;text-transform:uppercase;"
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
        muted = "text-align:right;padding:3px 5px;color:#7d8794;"
        strong = "text-align:right;padding:3px 5px;font-weight:700;"
        norm = "text-align:right;padding:3px 5px;"
        body.append(
            "<tr style='border-top:1px solid #1c2330;'>"
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
        f"<div style='font-size:0.74rem;color:#58a6ff;font-weight:700;"
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
    return "#3fb950" if score >= 6 else "#e3b341" if score >= 3 else "#f85149"


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
        f"<div style='color:#8b949e;font-size:0.66rem;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.4px;margin-bottom:4px;'>{label}</div>"
        f"<div style='width:62px;height:62px;border-radius:50%;margin:0 auto;"
        f"background:conic-gradient({color} {pct}%, #21262d {pct}% 100%);"
        f"display:flex;align-items:center;justify-content:center;'>"
        f"<div style='width:48px;height:48px;border-radius:50%;background:#0d1117;"
        f"display:flex;align-items:center;justify-content:center;"
        f"font-size:1.15rem;font-weight:800;color:{color};'>{score:g}</div></div>"
        f"<div style='font-size:0.74rem;font-weight:600;margin-top:4px;'>{side}</div>"
        "</div>"
    )


def _form_html(badge: str, team: str, form: dict, align: str) -> str:
    """A team block for the header: badge, name, W-L record + streak, and
    last-5 result chips (green win / red loss)."""
    rec = ""
    if form:
        streak = f" · {form['streak']}" if form.get("streak") else ""
        rec = (f"<div style='color:#8b949e;font-size:0.72rem;'>"
               f"{form.get('w', 0)}-{form.get('l', 0)}{streak}</div>")
    chips = ""
    for r in (form or {}).get("last5", []):
        c = "#2ea043" if r["win"] else "#da3633"
        chips += (f"<span title='{r.get('opp', '')} {r['score']}' "
                  f"style='display:inline-block;width:16px;height:16px;border-radius:4px;"
                  f"background:{c};margin:0 1px;'></span>")
    chips_html = (f"<div style='margin-top:3px;text-align:{align};'>{chips}</div>"
                  if chips else "")
    name_row = (f"<span style='font-weight:700;font-size:1.0rem;'>{team}</span>{badge}"
                if align == "right"
                else f"{badge}<span style='font-weight:700;font-size:1.0rem;'>{team}</span>")
    return (
        f"<div style='flex:1;'>"
        f"<div style='display:flex;align-items:center;gap:8px;"
        f"justify-content:flex-{'end' if align == 'right' else 'start'};'>{name_row}</div>"
        f"<div style='text-align:{align};'>{rec}</div>{chips_html}</div>"
    )


def research_card_html(sport: str, g: dict, matchup: dict, min_edge: float = 0.02) -> str:
    away, home = g.get("away_team", ""), g.get("home_team", "")
    a_badge = assets.team_badge_html(sport, away, 38)
    h_badge = assets.team_badge_html(sport, home, 38)
    n = matchup.get("n_teams", 30)

    # header: team form on each side, matchup facts in the middle
    header = (
        "<div style='display:flex;align-items:flex-start;gap:14px;'>"
        + _form_html(a_badge, away, matchup.get("away_form") or {}, "right")
        + "<span style='color:#8b949e;font-size:0.8rem;padding-top:6px;'>@</span>"
        + _form_html(h_badge, home, matchup.get("home_form") or {}, "left")
        + "</div>"
        f"<div style='text-align:center;color:#8b949e;font-size:0.76rem;margin-top:6px;'>"
        f"{fmt_time_et(g.get('game_time'))} · O/U {_num(g.get('total_line') or g.get('proj_total'))}"
        f" · proj {_num(_exp(g,'away'))}–{_num(_exp(g,'home'))}{_weather_txt(g)}</div>"
    )

    # conviction dials (model): moneyline / run line-spread / total
    conv = market_convictions(g)
    dials = (
        "<div style='display:flex;gap:8px;margin:14px 0 6px;"
        "background:#0d1117;border:1px solid #21262d;border-radius:12px;"
        "padding:10px 8px;'>"
        + "".join(_conviction_dial(label, c["side"], c["score"])
                  for label, c in conv.items())
        + "</div>"
    )

    # stat tables
    off_lbl = ("Batting vs Pitching" if sport == "MLB" else "Offense vs Defense")
    tables = ""
    if matchup.get("away_off_vs_home_def"):
        tables += _stat_table_html(f"{away} {off_lbl}",
                                   matchup["away_off_vs_home_def"], n)
    if matchup.get("home_off_vs_away_def"):
        tables += _stat_table_html(f"{home} {off_lbl}",
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

    lineups = _lineups_html(g)
    analysis = _analysis_html(sport, g, matchup, min_edge)
    return (
        "<div style='background:#161b24;border:1px solid #232a36;border-radius:14px;"
        "padding:16px 18px;margin-bottom:14px;'>"
        f"{header}{dials}{tables}{trends}{lineups}{analysis}</div>"
    )


def _weather_txt(g: dict) -> str:
    w = g.get("weather")
    if not w:
        return ""
    bits = f" · 🌡 {w.get('temp_f')}°F · 💨 {w.get('wind_mph')}mph {w.get('wind_dir', '')}"
    if (w.get("precip_pct") or 0) >= 20:
        bits += f" · 🌧 {w['precip_pct']}%"
    return bits


def _lineups_html(g: dict) -> str:
    lu = g.get("lineups") or {}
    home, away = lu.get("home") or [], lu.get("away") or []
    if not home and not away:
        return ""

    def col(team, names):
        rows = "".join(
            f"<div style='font-size:0.78rem;color:#c9d1d9;'>{i+1}. {n}</div>"
            for i, n in enumerate(names[:9]))
        return (f"<div style='flex:1;'><div style='color:#8b949e;font-size:0.72rem;"
                f"font-weight:700;'>{team}</div>{rows or '—'}</div>")

    return ("<div style='border-top:1px solid #232a36;margin-top:10px;padding-top:8px;'>"
            "<div style='font-size:0.78rem;color:#58a6ff;font-weight:700;"
            "text-transform:uppercase;margin-bottom:4px;'>Confirmed lineups</div>"
            f"<div style='display:flex;gap:14px;'>{col(g.get('away_team',''), away)}"
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
        color = {"PLAY": "#3fb950", "PASS": "#8b949e", "NOTE": "#e3b341"}[r["decision"]]
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
            f"<span style='color:#58a6ff;font-weight:700;'>{r['market']}:</span> "
            f"{r['text']} {conf_html}</div>"
        )
    return (
        "<div style='border-top:1px solid #232a36;margin-top:10px;padding-top:8px;'>"
        "<div style='font-size:0.78rem;color:#58a6ff;font-weight:700;"
        "text-transform:uppercase;margin-bottom:2px;'>📊 Statistical analysis</div>"
        + "".join(items) + "</div>"
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
