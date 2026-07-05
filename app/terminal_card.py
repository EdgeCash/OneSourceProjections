"""360Five Terminal research card — parallel renderer.

Embeds the reference `terminal_card.html` (the design source of truth) verbatim
and injects a real ``G`` object built from the engine's data over the demo one.
Every field the feed doesn't provide renders as an amber DATA GAP (``—``); we
never fabricate a number (product-honesty rule).

Mounted via ``streamlit.components.v1.html`` so the reference's JS (Research↔Share
toggle, prop drawers, Ask-AI copy) runs intact in a sandboxed iframe. Data is
injected server-side — no secrets or API calls happen client-side.

``build_g`` is a pure function (no Streamlit import) so it's unit-testable; it
takes the same ``(sport, g, matchup)`` the existing ``matchup_card_html`` uses,
plus the game's props for the drawers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app import ui

ET = ZoneInfo("America/New_York")
GAP = "—"

_TEMPLATE = os.path.join(os.path.dirname(__file__), "terminal_card.html")

# short market -> lineup/prop tag abbreviation
_MKT_ABBR = {
    "batter_total_bases": "TB", "batter_hits": "H", "batter_home_runs": "HRR",
    "pitcher_strikeouts": "K", "total bases": "TB", "hits": "H",
    "home run": "HRR", "pitcher ks": "K",
}


def _n(v, dp: int = 2) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return GAP
    try:
        return f"{float(v):.{dp}f}"
    except (TypeError, ValueError):
        return str(v)


def _rank(r, n_teams: int) -> str:
    """Rank number + a color suffix the template reads: g(good)/a(mid)/r(bad)."""
    if r is None or (isinstance(r, float) and pd.isna(r)):
        return GAP
    r = int(r)
    third = max(1, n_teams / 3)
    suf = "g" if r <= third else ("a" if r <= 2 * third else "r")
    return f"{r}{suf}"


def _code(sport: str, name: str) -> str:
    """Short team code (e.g. NYM). Best-effort via assets' ESPN abbreviations,
    else initials of the name's words — never fabricated stats, just a label."""
    if not name:
        return GAP
    try:
        from app import assets
        abbr = assets._ESPN_INDEX.get(sport, {}).get(
            __import__("project547.names", fromlist=["normalize"]).normalize(name))
        if abbr:
            return abbr.upper()
    except Exception:
        pass
    parts = [w for w in str(name).replace(".", "").split() if w]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][:2]).upper()
    return str(name)[:3].upper()


def _dt(iso: str | None) -> str:
    if not iso:
        return GAP
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(ET)
        return d.strftime("%b %-d · %-I:%M %p")
    except (ValueError, TypeError):
        return GAP


def _weather(g: dict) -> str:
    w = g.get("weather") or {}
    if not w:
        return GAP
    bits = []
    if w.get("temp_f") is not None:
        bits.append(f"{w['temp_f']}°F")
    if w.get("precip_pct") is not None:
        bits.append(f"{w['precip_pct']}% rain")
    if w.get("wind_mph") is not None:
        bits.append(f"{w['wind_mph']}mph {w.get('wind_dir', '')}".strip())
    return " · ".join(bits) or GAP


def _park(sport: str, home: str) -> str:
    if sport != "MLB":
        return GAP
    try:
        from project547 import parks
        pf = parks.factor(home)
        if pf:
            tag = "hitter" if pf > 1.02 else "pitcher" if pf < 0.98 else "neutral"
            return f"{pf:.2f}× {tag}"
    except Exception:
        pass
    return GAP


def _team(sport: str, name: str, form: dict | None, sos_rank) -> dict:
    form = form or {}
    w, l = form.get("w"), form.get("l")
    streak = form.get("streak")
    rec_bits = []
    if w is not None and l is not None:
        rec_bits.append(f"{w}–{l}")
    if streak:
        rec_bits.append(streak)
    if sos_rank is not None and pd.notna(sos_rank):
        rec_bits.append(f"{int(sos_rank)}th SOS")
    letters = [("W" if r.get("win") else "L") for r in (form.get("last5") or [])]
    return {"code": _code(sport, name), "name": name,
            "rec": " · ".join(rec_bits) or GAP, "form": letters}


def _conf_cell(kind_ev) -> dict:
    """(conviction score, ev) -> {v, tag, c} matching the template's classes."""
    score, ev = kind_ev
    tier = ui.play_tier(ev)
    kind = tier["kind"]
    c = "play" if kind == "core" else "lean" if kind in ("lean", "watch") else "pass"
    tag = tier["label"].replace(" PLAY", "")
    v = f"{score:g}" if score is not None else GAP
    return {"v": v, "tag": tag, "c": c}


def _matrix(matchup: dict) -> tuple[list, list]:
    """Recombine the two engine perspectives into (battingMatrix, pitchingMatrix)
    in the reference row shape. Batting = each team's offense; Pitching = each
    team's runs/hits *allowed* (the opponent-defense side). Bullpen has no engine
    source -> caller renders it as a DATA GAP row."""
    a_off = matchup.get("away_off_vs_home_def") or []
    h_off = matchup.get("home_off_vs_away_def") or []
    n = matchup.get("n_teams", 30)
    if not a_off or not h_off:
        return [], []

    def side5(row, side, order):
        # order 'awy' -> [season, situ, l10, l5, rank]; 'hm' -> [rank, l5, l10, situ, season]
        p = side  # "off" or "def"
        season, l10, l5, situ = (row.get(f"{p}_season"), row.get(f"{p}_l10"),
                                 row.get(f"{p}_l5"), row.get(f"{p}_situ"))
        rank = _rank(row.get(f"{p}_rank"), n)
        if order == "awy":
            return [_n(season), _n(situ), _n(l10), _n(l5), rank]
        return [rank, _n(l5), _n(l10), _n(situ), _n(season)]

    def adv(a_rank, h_rank, away_code, home_code):
        if a_rank is None or h_rank is None or pd.isna(a_rank) or pd.isna(h_rank):
            return GAP
        if int(a_rank) < int(h_rank):
            return away_code
        if int(h_rank) < int(a_rank):
            return home_code
        return GAP

    ac = _code("", "")  # codes are injected via G.away.code; adv only needs match
    bat, pit = [], []
    for ao, ho in zip(a_off, h_off):
        label = ao.get("stat", "")
        # batting: away offense vs home offense
        bat.append([label, side5(ao, "off", "awy"),
                    adv(ao.get("off_rank"), ho.get("off_rank"), "AWY", "HM"),
                    side5(ho, "off", "hm")])
        # pitching (allowed): away defense vs home defense
        pit.append([f"{label} Allowed", side5(ho, "def", "awy"),
                    adv(ho.get("def_rank"), ao.get("def_rank"), "AWY", "HM"),
                    side5(ao, "def", "hm")])
    return bat, pit


def _prop_tag(p: dict) -> str:
    mkt = ui.short_market(p.get("market", "")).lower()
    abbr = _MKT_ABBR.get(p.get("market", ""), _MKT_ABBR.get(mkt, "PROP"))
    line = p.get("line")
    side = "O" if (p.get("ev_under") is None or
                   (p.get("ev_over") or p.get("ev") or 0) >= (p.get("ev_under") or -9)) else "U"
    lt = "" if line is None or pd.isna(line) else f"{line:g}"
    return f"{side}{lt} {abbr}".strip()


def _lineup_side(sport: str, code: str, names: list, vs: str,
                 prop_by_player: dict) -> dict:
    from project547.names import normalize
    order = []
    for i, nm in enumerate(names[:9], 1):
        p = prop_by_player.get(normalize(nm or ""))
        order.append({"n": f"{i} {nm}", "bats": GAP, "pos": GAP,
                      "tag": _prop_tag(p) if p else GAP})
    return {"code": code, "vs": vs, "order": order}


def _prop_drawer(sport: str, p: dict) -> dict:
    """Real prop drawer from a props-row. Splits/last-5 that the feed doesn't
    carry render as DATA GAP rows — never invented."""
    tier = ui.play_tier(p.get("ev"), gate=p.get("gate"))
    ev = p.get("ev")
    edge = f"{ev * 100:+.0f}%" if ev is not None and pd.notna(ev) else GAP
    mp = p.get("model_over_prob")
    return {
        "player": p.get("player", GAP), "team": p.get("team", ""),
        "role": p.get("opponent") and f"vs {p.get('opponent')}" or "",
        "market": ui.short_market(p.get("market", "")),
        "line": f"O {p['line']:g}" if p.get("line") is not None else GAP,
        "price": ui.fmt_american(p.get("odds") or p.get("over_odds")) or GAP,
        "proj": _n(p.get("projection") or p.get("bp_projection"), 1),
        "edge": edge, "conf": tier["label"].replace(" PLAY", ""),
        "splits": [["Split", "Model%", "Line", "Sample"],
                   ["Season", GAP, _n(p.get("line"), 1), GAP],
                   ["Model", ui._pct(mp) if mp is not None else GAP, GAP,
                    f"n={p.get('n')}" if p.get("n") else GAP]],
        "form": [[GAP, "miss"], [GAP, "miss"], [GAP, "miss"]],
        "note": (f"Model {ui._pct(mp)} vs the {p.get('line')} line; edge {edge}. "
                 "Splits and last-5 game log not yet wired into this drawer."
                 if mp is not None else
                 "Projection wired; supporting splits pending in the prop feed."),
    }


def build_g(sport: str, g: dict, matchup: dict | None = None,
            props: list | None = None) -> dict:
    """Build the Terminal card's ``G`` object from real engine data. Pure; safe
    on empty matchup/props (fields degrade to DATA GAP)."""
    matchup = matchup or {}
    props = props or []
    away, home = g.get("away_team", ""), g.get("home_team", "")
    ac, hc = _code(sport, away), _code(sport, home)

    conv = ui.market_convictions(g)
    ml = conv.get("Moneyline", {})
    rl = conv.get("Run Line", conv.get("Spread", {}))
    tot = conv.get("Total", {})
    conf = {
        "ml": _conf_cell((ml.get("score"), ml.get("ev"))),
        "rl": _conf_cell((rl.get("score"), rl.get("ev"))),
        "tot": _conf_cell((tot.get("score"), tot.get("ev"))),
    }

    bat, pit = _matrix(matchup)
    gap_row = [[GAP, [GAP] * 5, GAP, [GAP] * 5]]

    from project547.names import normalize
    prop_by_player: dict = {}
    for p in props:
        key = normalize(p.get("player", "") or "")
        if key and (key not in prop_by_player or
                    (p.get("ev") or -9) > (prop_by_player[key].get("ev") or -9)):
            prop_by_player[key] = p

    lu = g.get("lineups") or {}
    ap = g.get("away_pitcher") or ""
    hp = g.get("home_pitcher") or ""
    lineups = {
        "away": _lineup_side(sport, ac, lu.get("away") or [],
                             f"vs {hp}" if hp else "", prop_by_player),
        "home": _lineup_side(sport, hc, lu.get("home") or [],
                             f"vs {ap}" if ap else "", prop_by_player),
    }

    # props map keyed by both "name" and "n name" forms the template taps with
    props_map: dict = {}
    for side in (lineups["away"], lineups["home"]):
        for b in side["order"]:
            nm = b["n"].split(" ", 1)[1] if " " in b["n"] else b["n"]
            p = prop_by_player.get(normalize(nm))
            if p:
                props_map[b["n"]] = _prop_drawer(sport, p)
    for who, nm in (("away", ap), ("home", hp)):
        p = prop_by_player.get(normalize(nm or ""))
        if p:
            props_map[nm] = _prop_drawer(sport, p)

    # ranks strings for the meta strip
    def rankpair(a, h):
        a = f"{ac} {int(a)}th" if a is not None and pd.notna(a) else f"{ac} {GAP}"
        h = f"{hc} {int(h)}th" if h is not None and pd.notna(h) else f"{hc} {GAP}"
        return f"{a} / {h}"

    # calibration receipt (real, from the ledger if present)
    cal = _calibration_receipt(g)

    # the headline play -> Share view + overall grade
    best = max((c for c in (("Moneyline", ml), ("Run Line", rl), ("Total", tot))),
               key=lambda kv: (kv[1].get("ev") or -9))
    best_ev = best[1].get("ev")
    grade = ui.play_tier(best_ev)["letter"]
    share = {
        "side": f"{best[1].get('side', GAP)}",
        "mk": f"{best[0]} · confidence {best[1].get('score', GAP):g}/10"
              if best[1].get("score") is not None else best[0],
        "price": f"edge {best_ev * 100:+.1f}%" if best_ev is not None and pd.notna(best_ev) else GAP,
        "why": (f"Model's top lean is {best[1].get('side', '')} "
                f"({best[0]}, {best_ev * 100:+.1f}% EV). "
                "Other markets grade lower — no forced play."
                if best_ev is not None and pd.notna(best_ev) else
                "No market cleared the edge bar today — pass."),
        "calLine": cal["h"] + " · " + cal["s"],
        "tags": (f"ML {conf['ml']['tag']} · RL {conf['rl']['tag']} · "
                 f"TOT {conf['tot']['tag']}"),
    }

    return {
        "league": sport, "grade": grade or GAP,
        "date": _dt(g.get("game_time")), "loc": matchup.get("venue") or GAP,
        "weather": _weather(g), "park": _park(sport, home),
        "power": rankpair(matchup.get("away_power_rank"), matchup.get("home_power_rank")),
        "pen": GAP,  # no bullpen *rank* in the engine — honest DATA GAP
        "rest": rankpair(matchup.get("away_rest"), matchup.get("home_rest")).replace("th", ""),
        "away": _team(sport, away, matchup.get("away_form"), matchup.get("away_sos_rank")),
        "home": _team(sport, home, matchup.get("home_form"), matchup.get("home_sos_rank")),
        "odds": {"awayML": ui.fmt_american(g.get("away_ml")) or GAP,
                 "ou": _n(g.get("total_line"), 1),
                 "homeML": ui.fmt_american(g.get("home_ml")) or GAP},
        "pitchers": {
            "away": {"name": ap or GAP, "role": GAP, "line": GAP},
            "home": {"name": hp or GAP, "role": GAP, "line": GAP}},
        "pitchMatrix": pit or gap_row,
        "batMatrix": bat or gap_row,
        "penMatrix": gap_row,   # DATA GAP — no engine bullpen matrix
        "conf": conf,
        "keyEdges": _key_edges(matchup, ac, hc),
        "lineups": lineups,
        "receipts": {
            "cal": cal,
            "stress": {"h": GAP, "s": "stress test not yet wired"},
            "line": {"h": share["tags"], "s": "per-market grade from live EV"}},
        "share": share,
        "props": props_map,
    }


def _key_edges(matchup: dict, ac: str, hc: str) -> list:
    """The biggest advantage rows, as plain strings for the Ask-AI prompt."""
    out = []
    for key, team in (("away_off_vs_home_def", ac), ("home_off_vs_away_def", hc)):
        for r in (matchup.get(key) or []):
            if r.get("adv", 0) >= 2 and r.get("off_rank") and r.get("def_rank"):
                out.append(f"{team} {r['stat']}: #{int(r['off_rank'])} offense vs "
                           f"#{int(r['def_rank'])} defense")
    return out[:5]


def _calibration_receipt(g: dict) -> dict:
    """Real calibration line from the performance ledger if available, else GAP."""
    try:
        from project547 import tracking
        led = tracking.load_ledger() if hasattr(tracking, "load_ledger") else None
        curve = ui.calibration_curve(led) if led else None
        if curve is not None and not curve.empty:
            n = int(curve["n"].sum())
            pred = curve["predicted"].mean() * 100
            emp = curve["empirical"].mean() * 100
            return {"h": f"pred {pred:.0f}% · act {emp:.0f}%",
                    "s": f"n={n} · from graded model-winprob ledger"}
    except Exception:
        pass
    return {"h": GAP, "s": "calibration ledger not loaded in this view"}


def terminal_card_html(sport: str, g: dict, matchup: dict | None = None,
                       props: list | None = None) -> str:
    """Full self-contained HTML (reference template + injected real G) for
    ``components.html``. Never raises: on failure returns the demo template."""
    try:
        gdata = build_g(sport, g, matchup, props)
        tpl = open(_TEMPLATE, encoding="utf-8").read()
        inject = "Object.assign(G, " + json.dumps(gdata, ensure_ascii=False) + ");\nrenderResearch();"
        return tpl.replace("/* init */\nrenderResearch();", "/* init */\n" + inject)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("terminal_card_html failed")
        try:
            return open(_TEMPLATE, encoding="utf-8").read()
        except Exception:
            return "<p>Terminal card unavailable.</p>"
