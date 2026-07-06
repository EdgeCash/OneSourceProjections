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


def _notna(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v))


def _n(v, dp: int = 2) -> str:
    if not _notna(v):
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


def _matrix(matchup: dict, ac: str, hc: str) -> tuple[list, list]:
    """Recombine the two engine perspectives into (offense, defense) matrices in
    the reference row shape. Offense = each team's own stat; Defense = each team's
    opponent-allowed stat. ``adv`` carries the real team code so the badge colors
    correctly. Works for any team sport (STAT_SPECS drives the stat rows)."""
    a_off = matchup.get("away_off_vs_home_def") or []
    h_off = matchup.get("home_off_vs_away_def") or []
    n = matchup.get("n_teams", 30)
    if not a_off or not h_off:
        return [], []

    def side5(row, p, order):
        season, l10, l5, situ = (row.get(f"{p}_season"), row.get(f"{p}_l10"),
                                 row.get(f"{p}_l5"), row.get(f"{p}_situ"))
        rank = _rank(row.get(f"{p}_rank"), n)
        if order == "awy":
            return [_n(season), _n(situ), _n(l10), _n(l5), rank]
        return [rank, _n(l5), _n(l10), _n(situ), _n(season)]

    def adv(a_rank, h_rank):
        if a_rank is None or h_rank is None or pd.isna(a_rank) or pd.isna(h_rank):
            return GAP
        return ac if int(a_rank) < int(h_rank) else hc if int(h_rank) < int(a_rank) else GAP

    offense, defense = [], []
    for ao, ho in zip(a_off, h_off):
        label = ao.get("stat", "")
        offense.append([label, side5(ao, "off", "awy"),
                        adv(ao.get("off_rank"), ho.get("off_rank")),
                        side5(ho, "off", "hm")])
        if ao.get("def_rank") is not None or ho.get("def_rank") is not None:
            defense.append([label, side5(ho, "def", "awy"),
                            adv(ho.get("def_rank"), ao.get("def_rank")),
                            side5(ao, "def", "hm")])
    return offense, defense


_GAP_ROWS = [[GAP, [GAP] * 5, GAP, [GAP] * 5]]


def _sections(sport: str, matchup: dict, away: str, home: str,
              ac: str, hc: str) -> list:
    """Sport-appropriate stat-matrix sections. MLB keeps batting/pitching/bullpen;
    other team sports get offense/defense. Empty when there's no matchup."""
    off, deff = _matrix(matchup, ac, hc)
    if not off:
        return []
    if sport == "MLB":
        return [{"label": f"Batting — {away} vs {home}", "cls": "bat", "rows": off},
                {"label": f"Pitching allowed — {away} vs {home}", "cls": "",
                 "rows": deff or _GAP_ROWS},
                {"label": f"Bullpen — {away} vs {home}", "cls": "pen", "rows": _GAP_ROWS}]
    return [{"label": f"Offense — {away} vs {home}", "cls": "bat", "rows": off},
            {"label": f"Defense — {away} vs {home}", "cls": "pen",
             "rows": deff or _GAP_ROWS}]


def _meta(sport: str, g: dict, matchup: dict, ac: str, hc: str) -> list:
    def rp(a, h):
        af = f"{ac} {int(a)}th" if a is not None and pd.notna(a) else f"{ac} {GAP}"
        hf = f"{hc} {int(h)}th" if h is not None and pd.notna(h) else f"{hc} {GAP}"
        return f"{af} / {hf}"
    ar, hr = matchup.get("away_rest"), matchup.get("home_rest")
    rest = f"{ac} {ar if ar is not None else GAP} / {hc} {hr if hr is not None else GAP}"
    meta = [{"k": "Date/Time", "v": _dt(g.get("game_time"))}]
    if sport in ("MLB", "NFL", "NCAAF"):
        meta.append({"k": "Weather", "v": _weather(g)})
    if sport == "MLB":
        meta.append({"k": "Park Factor", "v": _park(sport, g.get("home_team", ""))})
    meta.append({"k": "Power Rank",
                 "v": rp(matchup.get("away_power_rank"), matchup.get("home_power_rank"))})
    meta.append({"k": "Days Rest", "v": rest})
    return meta


def _starters(sport: str, g: dict) -> dict | None:
    """The tappable starter strip — pitchers (MLB), goalies (NHL), QBs (NFL/NCAAF);
    None for sports without one (the odds strip then centers)."""
    def one(name):
        return {"name": name, "role": GAP, "line": GAP, "key": name} if name else None
    pick = {"MLB": ("away_pitcher", "home_pitcher"),
            "NHL": ("away_goalie", "home_goalie"),
            "NFL": ("away_qb", "home_qb"), "NCAAF": ("away_qb", "home_qb")}.get(sport)
    if not pick:
        return None
    a, h = one(g.get(pick[0])), one(g.get(pick[1]))
    return {"away": a, "home": h} if (a or h) else None


def _prop_tag(p: dict) -> str:
    """Compact lineup tag: 'O1.5 TB' with a book line, else the projection
    ('1.1 H'), else just the market abbreviation."""
    mkt = ui.short_market(p.get("market", ""))
    abbr = (_MKT_ABBR.get(p.get("market", "")) or _MKT_ABBR.get(mkt.lower())
            or "".join(w[0] for w in mkt.split()[:2]).upper() or "PROP")
    line, proj = p.get("line"), p.get("projection")
    if _notna(line):
        return f"O{line:g} {abbr}"
    if _notna(proj):
        return f"{proj:g} {abbr}"
    return abbr


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
    """Prop drawer from a props-row. Populates every field the feed carries and
    DATA GAPs the rest — never invents. Many slates are projection-only until book
    lines attach on game day, so line/edge legitimately show — until then."""
    ev, mp = p.get("ev"), p.get("model_over_prob")
    line, proj = p.get("line"), p.get("projection")
    tier = ui.play_tier(ev, gate=p.get("gate"))
    edge = f"{ev * 100:+.0f}%" if _notna(ev) else GAP
    market = ui.short_market(p.get("market", ""))

    # only rows the feed actually carries — no invented splits
    splits = [["Split", "Value", "Detail"]]
    if _notna(proj):
        splits.append(["Projection", _n(proj, 1), market.lower()])
    if _notna(mp):
        splits.append(["Model over%", ui._pct(mp),
                       f"line {line:g}" if _notna(line) else GAP])
    plat = p.get("platoon")
    if isinstance(plat, str) and plat.strip():
        splits.append(["Platoon", plat, ""])
    for lbl, key in (("L5", "hr_l5"), ("L10", "hr_l10"), ("Season", "hr_season")):
        if _notna(p.get(key)):
            splits.append([f"HR {lbl}", _n(p[key], 2), "per game"])
    if _notna(p.get("bp_projection")):
        side = str(p.get("bp_recommended_side") or "").upper()
        splits.append(["BettingPros", _n(p["bp_projection"], 1),
                       f"lean {side}" if side else ""])
    if len(splits) == 1:
        splits.append(["—", GAP, GAP])

    note = ("Projection pending in the prop feed." if not _notna(proj) else
            f"Model projects {_n(proj, 1)} {market.lower()}"
            + (f"; {ui._pct(mp)} to clear {line:g}, edge {edge}."
               if _notna(mp) and _notna(line) else
               ". Book line attaches on game day."))
    return {
        "player": p.get("player", GAP), "team": p.get("team", ""),
        "role": f"vs {p.get('opponent')}" if p.get("opponent") else "",
        "market": market,
        "line": f"O {line:g}" if _notna(line) else GAP,
        "price": ui.fmt_american(p.get("odds") or p.get("over_odds")) or GAP,
        "proj": _n(proj, 1),
        "edge": edge, "conf": tier["label"].replace(" PLAY", ""),
        "splits": splits,
        "form": [[GAP, "miss"], [GAP, "miss"], [GAP, "miss"]],
        "note": note,
    }


def build_g(sport: str, g: dict, matchup: dict | None = None,
            props: list | None = None) -> dict:
    """Build the Terminal card's ``G`` from real engine data. Pure; safe on empty
    matchup/props. Tennis (player vs player) routes to a match variant; other team
    sports share the offense/defense path."""
    if sport in ("ATP", "WTA"):
        return _build_g_tennis(sport, g)
    matchup = matchup or {}
    props = props or []
    away, home = g.get("away_team", ""), g.get("home_team", "")
    ac, hc = _code(sport, away), _code(sport, home)

    conv = ui.market_convictions(g)
    ml = conv.get("Moneyline", {})
    rl = conv.get("Run Line", conv.get("Spread", {}))
    tot = conv.get("Total", {})
    conf = {"ml": _conf_cell((ml.get("score"), ml.get("ev"))),
            "rl": _conf_cell((rl.get("score"), rl.get("ev"))),
            "tot": _conf_cell((tot.get("score"), tot.get("ev")))}

    from project547.names import normalize
    prop_by_player: dict = {}
    for p in props:
        key = normalize(p.get("player", "") or "")
        if key and (key not in prop_by_player or
                    (p.get("ev") or -9) > (prop_by_player[key].get("ev") or -9)):
            prop_by_player[key] = p

    lu = g.get("lineups") or {}
    ap, hp = g.get("away_pitcher") or "", g.get("home_pitcher") or ""
    lineups = {
        "away": _lineup_side(sport, ac, lu.get("away") or [],
                             f"vs {hp}" if hp else "", prop_by_player),
        "home": _lineup_side(sport, hc, lu.get("home") or [],
                             f"vs {ap}" if ap else "", prop_by_player)}
    props_map: dict = {}
    for side in (lineups["away"], lineups["home"]):
        for b in side["order"]:
            nm = b["n"].split(" ", 1)[1] if " " in b["n"] else b["n"]
            p = prop_by_player.get(normalize(nm))
            if p:
                props_map[b["n"]] = _prop_drawer(sport, p)
    for nm in (ap, hp):
        p = prop_by_player.get(normalize(nm or ""))
        if p:
            props_map[nm] = _prop_drawer(sport, p)

    cal = _calibration_receipt(g)
    tags = f"ML {conf['ml']['tag']} · RL {conf['rl']['tag']} · TOT {conf['tot']['tag']}"
    # Share headline = the best *plausible* play. Skip VERIFY (edge too big, market
    # knows something) and PASS so the social card never leads with a tout-looking
    # implausible edge — an honest "pass" is preferable.
    cand = [(lbl, m) for lbl, m in (("Moneyline", ml), ("Run Line", rl), ("Total", tot))
            if _notna(m.get("ev")) and ui.play_tier(m.get("ev"))["kind"] in ("core", "lean", "watch")]
    if cand:
        blabel, bm = max(cand, key=lambda t: t[1].get("ev"))
        best_ev = bm.get("ev")
        grade = ui.play_tier(best_ev)["letter"]
        share = {
            "side": bm.get("side", GAP),
            "mk": (f"{blabel} · confidence {bm.get('score'):g}/10"
                   if bm.get("score") is not None else blabel),
            "price": f"edge {best_ev * 100:+.1f}%",
            "why": (f"Model's top play is {bm.get('side', '')} ({blabel}, "
                    f"{best_ev * 100:+.1f}% EV). Other markets grade lower — no forced play."),
            "calLine": f"{cal['h']} · {cal['s']}", "tags": tags}
    else:
        grade = GAP
        share = {"side": "No clean edge", "mk": "Pass — nothing cleared the bar",
                 "price": GAP,
                 "why": "No market cleared the edge bar today. A pass is a position.",
                 "calLine": f"{cal['h']} · {cal['s']}", "tags": tags}

    return {
        "league": sport, "grade": grade or GAP,
        "date": _dt(g.get("game_time")), "loc": matchup.get("venue") or GAP,
        "away": _team(sport, away, matchup.get("away_form"), matchup.get("away_sos_rank")),
        "home": _team(sport, home, matchup.get("home_form"), matchup.get("home_sos_rank")),
        "odds": {"awayML": ui.fmt_american(g.get("away_ml")) or GAP,
                 "ou": _n(g.get("total_line"), 1),
                 "homeML": ui.fmt_american(g.get("home_ml")) or GAP},
        "meta": _meta(sport, g, matchup, ac, hc),
        "starters": _starters(sport, g),
        "sections": _sections(sport, matchup, away, home, ac, hc),
        "lineups": lineups,
        "lineupLabel": ("Lineups — tap any hitter for prop projection" if sport == "MLB"
                        else "Lineups — tap any player for prop projection"),
        "conf": conf,
        "keyEdges": _key_edges(matchup, ac, hc),
        "receipts": {"cal": cal,
                     "stress": {"h": GAP, "s": "stress test not yet wired"},
                     "line": {"h": tags, "s": "per-market grade from live EV"}},
        "share": share, "props": props_map,
    }


def _build_g_tennis(sport: str, g: dict) -> dict:
    """Match-sport variant: player vs player, one market (match winner), no stat
    matrix or lineups — renders through the same template (empty sections degrade
    cleanly to a lightweight card)."""
    p1, p2 = g.get("player1", ""), g.get("player2", "")
    c1, c2 = _code(sport, p1), _code(sport, p2)
    p1_ev, p2_ev = g.get("p1_ev"), g.get("p2_ev")
    evs = [e for e in (p1_ev, p2_ev) if e is not None and pd.notna(e)]
    best_ev = max(evs) if evs else None
    fav = p1 if (p1_ev is not None and (p2_ev is None or (p1_ev or -9) >= (p2_ev or -9))) else p2
    fav_c = c1 if fav == p1 else c2
    score = ui._conviction(best_ev)
    tier = ui.play_tier(best_ev)
    c = "play" if tier["kind"] == "core" else "lean" if tier["kind"] in ("lean", "watch") else "pass"
    ml = {"v": f"{score:g}" if score is not None else GAP,
          "tag": f"{tier['label'].replace(' PLAY', '')} {fav_c}", "c": c}
    gap_cell = {"v": GAP, "tag": GAP, "c": "pass"}

    def player(nm, matches, prob):
        rec = f"{matches} matches" if matches else GAP
        if prob is not None and pd.notna(prob):
            rec += f" · win {ui._pct(prob)}"
        return {"code": _code(sport, nm), "name": nm, "rec": rec, "form": []}

    tags = f"MATCH {ml['tag']}"
    share = {
        "side": f"{fav} to win",
        "mk": f"Match winner · confidence {score:g}/10" if score is not None else "Match winner",
        "price": f"edge {best_ev * 100:+.1f}%" if best_ev is not None and pd.notna(best_ev) else GAP,
        "why": (f"Model favours {fav} ({best_ev * 100:+.1f}% EV on the moneyline)."
                if best_ev is not None and pd.notna(best_ev) else "No priced edge in this match."),
        "calLine": GAP, "tags": tags}
    return {
        "league": sport, "grade": tier["letter"] or GAP,
        "date": _dt(g.get("match_time")), "loc": g.get("tournament") or GAP,
        "away": player(p1, g.get("p1_matches"), g.get("player1_win_prob")),
        "home": player(p2, g.get("p2_matches"), g.get("player2_win_prob")),
        "odds": {"awayML": ui.fmt_american(g.get("p1_price")) or GAP, "ou": GAP,
                 "homeML": ui.fmt_american(g.get("p2_price")) or GAP},
        "meta": [{"k": "Date/Time", "v": _dt(g.get("match_time"))},
                 {"k": "Tournament", "v": g.get("tournament") or GAP},
                 {"k": "Surface", "v": str(g.get("surface") or "").title() or GAP},
                 {"k": "Format", "v": "Best of 3"}],
        "starters": None, "sections": [],
        "lineups": {"away": {"code": c1, "vs": "", "order": []},
                    "home": {"code": c2, "vs": "", "order": []}},
        "lineupLabel": "", "conf": {"ml": ml, "rl": gap_cell, "tot": gap_cell},
        "keyEdges": [],
        "receipts": {"cal": {"h": GAP, "s": "tennis calibration pending"},
                     "stress": {"h": GAP, "s": GAP},
                     "line": {"h": tags, "s": "match-winner grade from EV"}},
        "share": share, "props": {}}


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
