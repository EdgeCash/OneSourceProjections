"""HTML builders for the Edge Card site (pure Python, no template engine).

Reuses the Streamlit-free decision layer from ``app/ui.py`` (market calls,
confidence, props, tiers, formatting, DATA-GAP chips) and emits brand-new
markup styled by ``web/assets/app.css``. Every builder is a pure function of
its inputs and never raises on a single game (falls back to a minimal card).
"""
from __future__ import annotations

import html
import re

from app import ui, assets
from app.ui import (fmt_american, fmt_time_et, _pct, _num, _last, _mcf,
                    _gap, _num_or_gap, lineup_status)

BRAND = "Project 54.7"
TAGLINE = "52.4% pays the house. 54.7% pays you."

# nav: (code, label, href). Sports first (the product), then reference pages.
NAV = [
    ("today", "Today", "index.html"),
    ("MLB", "MLB", "mlb.html"),
    ("WNBA", "WNBA", "wnba.html"),
    ("ATP", "Tennis", "atp.html"),
    ("MLS", "Soccer", "mls.html"),
    ("props", "Props", "props.html"),
    ("performance", "Track Record", "performance.html"),
]

# decision → (emoji, css tone)
_DEC = {"PLAY": ("🟢", "play"), "LEAN": ("🟡", "lean"),
        "VERIFY": ("🟠", "verify"), "PASS": ("⚪", "pass")}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")
    return s or "x"


def game_id(sport: str, g: dict) -> str:
    gid = (g.get("game_pk") or g.get("game_id") or g.get("match_id")
           or f"{g.get('away_team','')}-{g.get('home_team','')}"
           or f"{g.get('player1','')}-{g.get('player2','')}")
    return slug(f"{sport}-{gid}")


def card_href(sport: str, g: dict) -> str:
    return f"edge-card-{game_id(sport, g)}.html"


def is_match_sport(sport: str) -> bool:
    return sport in ("ATP", "WTA", "MLS", "EPL")


def matchup_title(sport: str, g: dict) -> str:
    if sport in ("ATP", "WTA"):
        return f"{g.get('player1','')} vs {g.get('player2','')}"
    return f"{g.get('away_team','')} @ {g.get('home_team','')}"


# --- page shell -------------------------------------------------------------

def page(title: str, body: str, *, active: str = "", subtitle: str = "",
         updated: str = "") -> str:
    nav = "".join(
        f"<a class='nav-link{' active' if code == active else ''}' "
        f"href='{href}'>{esc(label)}</a>"
        for code, label, href in NAV)
    sub = f"<div class='topbar-sub'>{esc(subtitle)}</div>" if subtitle else ""
    upd = f"<span class='upd'>{esc(updated)}</span>" if updated else ""
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="assets/app.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎯</text></svg>">
<script src="assets/gate.js" defer></script>
<script src="assets/app.js" defer></script>
</head>
<body>
<header class="topbar">
  <div class="topbar-in">
    <a class="brand" href="index.html">🎯 {esc(BRAND)}</a>
    <nav class="nav">{nav}</nav>
    <button class="theme-toggle" aria-label="Toggle theme" data-theme-toggle>◐</button>
  </div>
  {sub}
</header>
<main class="wrap">
{body}
</main>
<footer class="foot">
  <span>{esc(TAGLINE)}</span> · {upd} · <span>not financial advice</span>
</footer>
</body>
</html>"""


# --- small pieces -----------------------------------------------------------

def decision_pill(decision: str) -> str:
    emoji, tone = _DEC.get(decision, ("⚪", "pass"))
    return f"<span class='pill pill-{tone}'>{emoji} {esc(decision)}</span>"


def conf_meter(conf: dict) -> str:
    if not conf:
        return ""
    score = conf.get("score") or 0
    label = conf.get("label", "")
    cap = " · capped LEAN (key driver is a DATA GAP)" if conf.get("cap") == "lean" else ""
    d = conf.get("drivers", {})
    drivers = (f"lineups {d.get('lineups',0):.2f} · edge {d.get('edge',0):.2f} · "
               f"calibration {d.get('calibration',0):.2f} · completeness "
               f"{d.get('completeness',0):.2f}") if d else ""
    return (f"<div class='conf'>"
            f"<div class='conf-head'><span class='conf-label'>Confidence "
            f"<b>{esc(label)}</b></span><span class='conf-score'>{score:.2f}</span></div>"
            f"<div class='conf-track'><span class='conf-fill' style='width:{int(score*100)}%'></span></div>"
            f"<div class='conf-drivers'>{esc(drivers)}{esc(cap)}</div></div>")


def _clv_for(calls_market: str, data: dict) -> str:
    clv = (data or {}).get("clv") or {}
    key = {"Moneyline": "moneyline", "Total": "total",
           "Run Line": "spread", "Spread": "spread"}.get(calls_market)
    s = clv.get(key) if key else None
    if not s or s.get("clv_n") in (None, 0):
        return _gap("no CLV yet")
    avg = s.get("avg_clv")
    tone = "pos" if (avg or 0) > 0 else "neg"
    return f"<span class='clv clv-{tone}'>{avg:+.1f}%</span> <small>n={s.get('clv_n')}</small>"


def market_card(c: dict, data: dict) -> str:
    """One market: pick, model %, edge, decision pill, price/line, stake, CLV."""
    emoji, tone = _DEC.get(c.get("decision"), ("⚪", "pass"))
    prob = _pct(c.get("prob"))
    ev = c.get("ev")
    edge = (f"{ev*100:+.1f}%" if isinstance(ev, (int, float)) else None)
    price = fmt_american(c.get("price")) or _gap("no line")
    line = c.get("line")
    line_txt = f"{line:g}" if isinstance(line, (int, float)) else ""
    stake = (f"{c['stake_units']:g}u" if c.get("stake_units") else "—")
    edge_html = (f"<span class='mc-edge edge-{'pos' if ev and ev>0 else 'neg' if ev and ev<0 else 'flat'}'>{edge}</span>"
                 if edge else _gap("unpriced"))
    return (f"<div class='mcard mcard-{tone}'>"
            f"<div class='mc-top'><span class='mc-label'>{esc(c.get('label',''))}"
            f"{(' '+line_txt) if line_txt else ''}</span>{decision_pill(c.get('decision'))}</div>"
            f"<div class='mc-prob'>{prob}</div>"
            f"<div class='mc-pick'>{esc(c.get('pick',''))}</div>"
            f"<div class='mc-row'><span>Edge</span>{edge_html}</div>"
            f"<div class='mc-row'><span>Price</span><b>{price}</b></div>"
            f"<div class='mc-row'><span>Stake</span><b>{esc(stake)}</b></div>"
            f"<div class='mc-row'><span>CLV</span>{_clv_for(c.get('label',''), data)}</div>"
            f"</div>")


# --- projection hero (sport-adaptive) ---------------------------------------

def _proj_hero(sport: str, g: dict) -> str:
    if sport in ("ATP", "WTA"):
        q1, q2 = _mcf(g.get("player1_win_prob")), _mcf(g.get("player2_win_prob"))
        return (f"<div class='hero'><div class='hero-k'>Model win probability</div>"
                f"<div class='hero-big'>{_last(g.get('player1',''))} {_pct(q1)}"
                f" · {_last(g.get('player2',''))} {_pct(q2)}</div>"
                f"<div class='hero-sub'>surface-aware Elo · "
                f"{g.get('p1_matches','—')}/{g.get('p2_matches','—')} matches</div></div>")
    if sport == "MLS":
        return (f"<div class='hero'><div class='hero-k'>Projected goals</div>"
                f"<div class='hero-big'>{_num(g.get('home_exp'))}–{_num(g.get('away_exp'))}</div>"
                f"<div class='hero-sub'>1X2 {_pct(g.get('home_win_prob'))} / "
                f"{_pct(g.get('draw_prob'))} / {_pct(g.get('away_win_prob'))} · "
                f"O2.5 {_pct(g.get('over_2_5'))}</div></div>")
    # team sports (MLB / generic)
    at = _last(g.get("away_team", "")); ht = _last(g.get("home_team", ""))
    ax = g.get("away_exp_runs", g.get("away_exp"))
    hx = g.get("home_exp_runs", g.get("home_exp"))
    edge_team = ht if (_mcf(g.get("home_win_prob")) or 0) >= 0.5 else at
    return (f"<div class='hero'><div class='hero-k'>Score projection</div>"
            f"<div class='hero-big'>{esc(at)} {_num(ax)} · {esc(ht)} {_num(hx)}</div>"
            f"<div class='hero-sub'>Total {_num(g.get('proj_total'))} · "
            f"{esc(edge_team)} {_pct(max(_mcf(g.get('home_win_prob')) or 0, _mcf(g.get('away_win_prob')) or 0))} "
            f"to win</div></div>")


def _card_header(sport: str, g: dict) -> str:
    if sport in ("ATP", "WTA"):
        left = f"<div class='team'><div class='team-name'>{esc(g.get('player1',''))}</div></div>"
        right = f"<div class='team home'><div class='team-name'>{esc(g.get('player2',''))}</div></div>"
        meta = [f"🎾 {esc(g.get('tournament',''))}",
                f"court {esc(str(g.get('surface') or '—').title())}",
                fmt_time_et(g.get("match_time"))]
    else:
        away, home = g.get("away_team", ""), g.get("home_team", "")
        left = (f"<div class='team'>{assets.team_badge_html(sport, away, 40)}"
                f"<div class='team-name'>{esc(away)}</div></div>")
        right = (f"<div class='team home'><div class='team-name'>{esc(home)}</div>"
                 f"{assets.team_badge_html(sport, home, 40)}</div>")
        wx = g.get("weather") or {}
        meta = [fmt_time_et(g.get("game_time"))]
        if sport == "MLB" and wx:
            t = wx.get("temp_f"); w = wx.get("wind_mph")
            if t is not None:
                meta.append(f"🌡 {t}°F")
            if w is not None:
                meta.append(f"💨 {w}mph {esc(wx.get('wind_dir',''))}")
        meta.append("🔒 odds lock at first pitch")
    metahtml = "".join(f"<span>{m}</span>" for m in meta)
    return (f"<div class='ec-head'><div class='ec-teams'>{left}"
            f"<div class='ec-hero'>{_proj_hero(sport, g)}</div>{right}</div>"
            f"<div class='ec-meta'>{metahtml}</div></div>")


def _section(title: str, hint: str, body: str) -> str:
    if not body:
        return ""
    h = f"<span class='sec-hint'>{esc(hint)}</span>" if hint else ""
    return (f"<section class='sec'><h2 class='sec-h'>{esc(title)}{h}</h2>{body}</section>")


# --- MLB / team extras ------------------------------------------------------

def _pitching(g: dict, pitching: dict | None) -> str:
    if not pitching:
        return f"<div class='card'>{_gap('probable starters not posted')}</div>"

    def col(side, align):
        sp = pitching.get(f"{side}_sp")
        if not sp:
            return f"<div class='pit-col {align}'>{_gap('TBD starter')}</div>"
        tto = "<span class='flag'>3rd-time penalty</span>" if sp.get("tto_flag") else ""
        hand = f"<small>{esc(sp.get('hand') or '')}HP</small>" if sp.get("hand") else ""
        bp = pitching.get(f"{side}_bullpen") or {}
        bptxt = (f"Bullpen <b>{esc(bp.get('fatigue') or '—')}</b>"
                 if bp else _gap("bullpen"))
        return (f"<div class='pit-col {align}'>"
                f"<div class='pit-name'>{esc(sp.get('name',''))} {hand} {tto}</div>"
                f"<div class='pit-line'>IP {_num_or_gap(sp.get('ip'),'{:.1f}')} · "
                f"K/9 {_num_or_gap(sp.get('k9'),'{:.1f}')} · "
                f"BB/9 {_num_or_gap(sp.get('bb9'),'{:.1f}')} · "
                f"xFIP {_num_or_gap(sp.get('xfip'),'{:.2f}')}</div>"
                f"<div class='pit-bp'>{bptxt}</div></div>")

    return (f"<div class='card pit'>{col('away','')}{col('home','right')}</div>")


def _context(sport: str, g: dict, data: dict) -> str:
    if sport != "MLB":
        return ""
    ump = g.get("umpire") or {}
    wx = g.get("weather") or {}
    park = None
    try:
        from project547 import parks
        park = parks.factor(g.get("home_team", ""))
    except Exception:
        park = None
    cells = []
    cells.append(f"<div class='ctx'><span class='ctx-k'>Park</span>"
                 f"{_num_or_gap(park,'{:.2f}×')}</div>")
    if wx.get("wind_mph") is not None:
        cells.append(f"<div class='ctx'><span class='ctx-k'>Weather</span>"
                     f"{_num_or_gap(wx.get('temp_f'),'{:.0f}°F')} · "
                     f"{_num_or_gap(wx.get('wind_mph'),'{:.0f}mph')}</div>")
    else:
        cells.append(f"<div class='ctx'><span class='ctx-k'>Weather</span>{_gap('no wx')}</div>")
    if ump.get("name"):
        cells.append(f"<div class='ctx'><span class='ctx-k'>Umpire</span>"
                     f"{esc(ump['name'])} · K {_num_or_gap(ump.get('k_idx') or ump.get('k_factor'),'{:.2f}×')} · "
                     f"R {_num_or_gap(ump.get('runs_idx') or ump.get('runs_factor'),'{:.2f}×')}</div>")
    else:
        cells.append(f"<div class='ctx'><span class='ctx-k'>Umpire</span>{_gap('unassigned')}</div>")
    return f"<div class='ctx-grid'>{''.join(cells)}</div>"


def _lineups(sport: str, g: dict) -> str:
    if sport != "MLB":
        return ""
    lu = g.get("lineups") or {}
    away, home = lu.get("away") or [], lu.get("home") or []
    if not away and not home:
        return f"<div class='card'>{_gap('lineups not posted')}</div>"

    def col(names, team):
        rows = "".join(f"<tr><td class='ord'>{i}</td><td>{esc(n)}</td></tr>"
                       for i, n in enumerate(names, 1))
        return (f"<div class='lu'><div class='lu-t'>{esc(team)}</div>"
                f"<table>{rows}</table></div>")
    return (f"<div class='lu-grid'>{col(away, g.get('away_team',''))}"
            f"{col(home, g.get('home_team',''))}</div>")


def _props(sport: str, props: list, g: dict) -> str:
    try:
        top = ui.top_game_props(sport, props, g.get("away_team", ""),
                                g.get("home_team", "")) if props else []
    except Exception:
        top = []
    if not top:
        return ""
    chips = []
    for p in top:
        mp = _pct(p.get("model_prob") or p.get("projection"))
        ev = p.get("ev")
        evtxt = (f"{ev*100:+.1f}%" if isinstance(ev, (int, float)) else "proj")
        chips.append(
            f"<div class='prop'><div class='prop-b'>{esc(p.get('bet') or (esc(p.get('player',''))+' '+esc(p.get('market',''))))}</div>"
            f"<div class='prop-m'>{fmt_american(p.get('price'))} · model {mp} · {evtxt}</div></div>")
    return f"<div class='props'>{''.join(chips)}</div>"


def _bet_ticket(calls: list) -> str:
    if not calls:
        return ""
    rows = []
    for c in calls:
        _, tone = _DEC.get(c.get("decision"), ("", "pass"))
        ev = c.get("ev")
        evtxt = (f"{ev*100:+.1f}% EV" if isinstance(ev, (int, float)) else "no priced edge")
        stake = f" · {c['stake_units']:g}u" if c.get("stake_units") else ""
        rows.append(
            f"<div class='bt-row'><span class='bt-mk bt-{tone}'>{esc(c.get('label',''))}</span>"
            f"<span class='bt-body'>{esc(c.get('pick',''))} — "
            f"<b class='bt-{tone}'>{esc(c.get('decision',''))}</b> "
            f"<small>({evtxt}, conf {c.get('conf',0):.1f}/10{stake})</small></span></div>")
    return f"<div class='card bt'>{''.join(rows)}</div>"


# --- match-sport markets (tennis/soccer) ------------------------------------

def _match_markets(sport: str, g: dict) -> list:
    """Structured 'calls' for match sports, from model probs (no team-sport
    market fields). Priced EV shown where present."""
    calls = []
    if sport in ("ATP", "WTA"):
        for who, pk, ev, price in (("player1", "player1_win_prob", "p1_ev", "p1_price"),
                                    ("player2", "player2_win_prob", "p2_ev", "p2_price")):
            calls.append({"label": "Match", "pick": _last(g.get(who, "")),
                          "prob": _mcf(g.get(pk)), "ev": _mcf(g.get(ev)),
                          "price": _mcf(g.get(price)), "line": None,
                          "decision": "PASS", "conf": 0, "stake_units": None})
    elif sport == "MLS":
        for lbl, pk in (("Home", "home_win_prob"), ("Draw", "draw_prob"),
                        ("Away", "away_win_prob"), ("Over 2.5", "over_2_5")):
            calls.append({"label": lbl, "pick": lbl, "prob": _mcf(g.get(pk)),
                          "ev": None, "price": None, "line": None,
                          "decision": "PASS", "conf": 0, "stake_units": None})
    return calls


# --- the Edge Card ----------------------------------------------------------

def edge_card(sport: str, g: dict, mu: dict, data: dict, best_line: dict,
              *, min_edge: float, gate_table: dict, bankroll: float,
              props: list, full: bool = True) -> str:
    """The full one-page Edge Card. `full` controls the standalone page vs an
    embedded summary. Never raises."""
    try:
        if is_match_sport(sport):
            calls = _match_markets(sport, g)
        else:
            calls = ui._mc_market_calls(sport, g, min_edge,
                                        gate_table=gate_table, bankroll=bankroll)
        conf = ui._confidence(sport, g, calls, data or {})
        if conf.get("cap") == "lean":
            for c in calls:
                if c.get("decision") == "PLAY":
                    c["decision"] = "LEAN"

        head = _card_header(sport, g)
        confh = conf_meter(conf)
        market_cards = "".join(market_card(c, data) for c in calls) if calls else _gap("no priced markets")
        markets = _section("Markets & CLV",
                           "model fair % vs the market · edge · realized CLV",
                           f"<div class='mcards'>{market_cards}</div>")
        ticket = _section("Bet Ticket", "the call, the why, the ¼-Kelly stake",
                          _bet_ticket(calls))
        pit = _section("Starting Pitching", "hand · IP · K/9 · BB/9 · xFIP · TTO",
                       _pitching(g, (data or {}).get("pitching"))) if sport == "MLB" else ""
        ctx = _section("Context — the levers", "park · weather · umpire",
                       _context(sport, g, data)) if sport == "MLB" else ""
        lu = _section("Lineups", "", _lineups(sport, g)) if sport == "MLB" else ""
        pr = _section("Top Props", "best model edges in this game", _props(sport, props, g))

        body = f"{head}{confh}{markets}{ticket}{pit}{ctx}{lu}{pr}"
        return body
    except Exception:
        ui.log.exception("edge_card failed for %s", sport) if hasattr(ui, "log") else None
        return (f"<div class='card'><b>{esc(matchup_title(sport, g))}</b><br>"
                f"{_gap('card failed to build')}</div>")


# --- slate summary row ------------------------------------------------------

def slate_row(sport: str, g: dict, headline: tuple[str, bool]) -> str:
    label, _auto = headline
    return (f"<a class='srow' href='{card_href(sport, g)}'>"
            f"<span class='srow-label'>{_render_headline(label)}</span>"
            f"<span class='srow-go'>→</span></a>")


def _render_headline(label: str) -> str:
    # sheet_headline returns markdown-ish text with ** for bold; convert.
    s = esc(label)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s
