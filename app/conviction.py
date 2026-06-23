"""'Put it on trial' — the Conviction Rate engine.

A visitor assembles a play from dropdowns (jurisdiction → case → charge → side)
and the People return a **Conviction Rate**: the model's probability that the
chosen side cashes, plus a short evidence summary. The double meaning is the
point — a conviction (the guilty verdict) and conviction (how sure we are).

Pure functions over the published slate shapes (the same ``latest.json`` the
dashboard already serves) — the probabilities are *already computed*, so there
is no model call and no API cost. Mirrors ``app/ui.py``'s pure-helper style and
is unit-tested without Streamlit.
"""

from __future__ import annotations

import pandas as pd

from app import ui

# Conviction bands — the courtroom flavor on top of the percentage. Ordered
# high→low; the first threshold a rate clears wins.
BANDS = [
    (0.65, "Open-and-shut"),
    (0.58, "Strong case"),
    (0.52, "Circumstantial"),
    (0.48, "Reasonable doubt"),
    (0.00, "The People decline to charge"),
]


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v))


def band(rate: float | None) -> str:
    if not _ok(rate):
        return "Mistrial"
    for thr, label in BANDS:
        if rate >= thr:
            return label
    return BANDS[-1][1]


# ---------------------------------------------------------------------------
# Slate accessors (for building the dropdowns)
# ---------------------------------------------------------------------------

def sports(day: dict) -> list[str]:
    order = ["MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL"]
    have = [s for s, b in (day or {}).items()
            if (b.get("games") or b.get("props"))]
    return [s for s in order if s in have] + [s for s in have if s not in order]


def games(day: dict, sport: str) -> list[dict]:
    return (day or {}).get(sport, {}).get("games", []) or []


def props(day: dict, sport: str) -> list[dict]:
    return (day or {}).get(sport, {}).get("props", []) or []


def game_label(g: dict) -> str:
    return f"{g.get('away_team', '')} @ {g.get('home_team', '')}"


def charges(g: dict) -> list[str]:
    """The game markets the model has priced for this game."""
    out = []
    if _ok(g.get("home_win_prob")):
        out.append("Moneyline")
    sp = g.get("rl_home_line", g.get("spread_home_line"))
    cover = g.get("model_home_rl", g.get("model_home_cover"))
    if _ok(sp) and _ok(cover):
        out.append("Run Line" if "rl_home_line" in g else "Spread")
    if _ok(g.get("total_line")) and _ok(g.get("model_over_prob")):
        out.append("Total")
    return out


def sides(g: dict, charge: str) -> list[tuple[str, str]]:
    """(side_id, display label) options for a charge."""
    away, home = g.get("away_team", ""), g.get("home_team", "")
    if charge == "Moneyline":
        return [("away", away), ("home", home)]
    if charge in ("Run Line", "Spread"):
        line = g.get("rl_home_line", g.get("spread_home_line"))
        return [("away", f"{away} {-line:+g}"), ("home", f"{home} {line:+g}")]
    if charge == "Total":
        line = g.get("total_line")
        return [("over", f"Over {line:g}"), ("under", f"Under {line:g}")]
    return []


# ---------------------------------------------------------------------------
# Conviction + evidence — games
# ---------------------------------------------------------------------------

def _game_prob(g: dict, charge: str, side_id: str) -> float | None:
    if charge == "Moneyline":
        v = g.get(f"{side_id}_win_prob")
    elif charge in ("Run Line", "Spread"):
        cover = g.get("model_home_rl", g.get("model_home_cover"))
        v = cover if not _ok(cover) else (cover if side_id == "home" else 1 - cover)
    elif charge == "Total":
        over = g.get("model_over_prob")
        v = over if not _ok(over) else (over if side_id == "over" else 1 - over)
    else:
        v = None
    return float(min(1.0, max(0.0, v))) if _ok(v) else None


def _side_ev(g: dict, charge: str, side_id: str):
    if charge == "Moneyline":
        return g.get(f"{side_id}_ml_ev", g.get(f"{side_id}_ev"))
    if charge in ("Run Line", "Spread"):
        return g.get(f"rl_{side_id}_ev", g.get(f"spread_{side_id}_ev"))
    if charge == "Total":
        return g.get(f"{side_id}_ev")
    return None


def _side_price(g: dict, charge: str, side_id: str):
    if charge == "Moneyline":
        return g.get(f"{side_id}_ml")
    if charge in ("Run Line", "Spread"):
        return g.get(f"rl_{side_id}_odds", g.get(f"spread_{side_id}_odds"))
    if charge == "Total":
        return g.get(f"{side_id}_odds")
    return None


def _short(name: str) -> str:
    return str(name).split()[-1] if name else name


def game_evidence(g: dict, charge: str, side_id: str) -> list[str]:
    out = []
    a, h = g.get("away_team", ""), g.get("home_team", "")
    ax, hx = ui._exp(g, "away"), ui._exp(g, "home")
    if _ok(ax) and _ok(hx):
        out.append(f"The People project {_short(a)} {ui._num(ax)}–"
                   f"{ui._num(hx)} {_short(h)}.")
    if charge == "Total":
        pt, line = g.get("proj_total"), g.get("total_line")
        if _ok(pt) and _ok(line):
            gap = float(pt) - float(line)
            lean = "over" if gap > 0 else "under"
            out.append(f"Projected total {ui._num(pt)} vs the {line:g} line "
                       f"({gap:+.1f} toward the {lean}).")
    price = _side_price(g, charge, side_id)
    ev = _side_ev(g, charge, side_id)
    if _ok(price):
        bit = f"Best price {ui.fmt_american(price)}"
        if _ok(ev):
            bit += f" — model edge {ev:+.1%}"
        out.append(bit + ".")
    ap, hp = g.get("away_pitcher"), g.get("home_pitcher")
    if ap and hp:
        out.append(f"On the mound: {ap} vs {hp}.")
    return out[:3]


def game_verdict(g: dict, charge: str, side_id: str) -> dict:
    label = dict(sides(g, charge)).get(side_id, side_id)
    caption = f"{label} ML" if charge == "Moneyline" else label
    rate = _game_prob(g, charge, side_id)
    return {"ok": _ok(rate), "caption": caption, "charge": charge,
            "rate": rate, "band": band(rate),
            "evidence": game_evidence(g, charge, side_id)}


# ---------------------------------------------------------------------------
# Conviction + evidence — player props
# ---------------------------------------------------------------------------

def game_props(props_list: list[dict], g: dict) -> list[dict]:
    """Props whose team is one of the two clubs in this game."""
    from onesource.names import normalize
    teams = {normalize(g.get("home_team", "")), normalize(g.get("away_team", ""))}
    return [p for p in props_list if normalize(p.get("team", "")) in teams] or props_list


def prop_players(props_list: list[dict]) -> list[str]:
    seen = {}
    for p in props_list:
        n = p.get("player")
        if n:
            seen.setdefault(n, True)
    return list(seen)


def player_markets(props_list: list[dict], player: str) -> list[str]:
    out = []
    for p in props_list:
        if p.get("player") == player and p.get("market") and p["market"] not in out:
            out.append(p["market"])
    return out


def prop_row(props_list: list[dict], player: str, market: str) -> dict | None:
    return next((p for p in props_list
                 if p.get("player") == player and p.get("market") == market), None)


def prop_sides(p: dict) -> list[tuple[str, str]]:
    line = p.get("line")
    suffix = f" {line:g}" if _ok(line) else ""
    return [("over", f"Over{suffix}"), ("under", f"Under{suffix}")]


def prop_evidence(p: dict, side_id: str) -> list[str]:
    out = []
    proj, line = p.get("projection"), p.get("line")
    if _ok(proj) and _ok(line):
        out.append(f"The People project {ui._num(proj, 1)} vs the {line:g} line.")
    for lbl, key in (("the last 5", "hr_l5"), ("the last 10", "hr_l10"),
                     ("the season", "hr_season")):
        v = p.get(key)
        if _ok(v):
            over_rate = v if side_id == "over" else 1 - v
            out.append(f"Cleared this side {over_rate:.0%} of {lbl}.")
            break
    rk = p.get("opp_rank")
    if _ok(rk):
        out.append(f"Opponent ranks #{int(rk)} defending this stat.")
    return out[:3]


def prop_verdict(p: dict, side_id: str) -> dict:
    over = p.get("model_over_prob")
    rate = None
    if _ok(over):
        rate = float(over) if side_id == "over" else float(1 - over)
    line = p.get("line")
    suffix = f" {line:g}" if _ok(line) else ""
    caption = (f"{p.get('player', '')} {side_id.title()}{suffix} "
               f"{ui.short_market(p.get('market', ''))}").strip()
    return {"ok": _ok(rate), "caption": caption, "charge": "Prop",
            "rate": rate, "band": band(rate),
            "evidence": prop_evidence(p, side_id)}
