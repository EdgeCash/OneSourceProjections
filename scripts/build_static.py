"""Static-site generator for 360Five.

The heavy modelling already runs offline (the hourly GitHub Action bakes
``data/output/latest.json``); this script turns that JSON into a plain static
site — no server, no per-click re-runs — reusing the *same* renderers the
Streamlit app uses (``app.ui.sharp_sheet_html`` / ``build_best_bets`` and the
shared ``app.theme`` CSS), so the look matches exactly.

Output: ``site/`` — one page per sport that has cards today, plus the
cross-sport PLAYS board, a sidebar nav, native ``<details>`` Sharp Sheets (no
JS needed to expand), and a player-prop drawer that opens when you tap a name
in a lineup. Run:  ``python scripts/build_static.py``

The per-game enrichment (CLV, calibration, starter strip, best odds) is
computed here from the same local sources the app uses — all CI-safe (the
pipeline keeps internal box logs primary because FanGraphs is blocked on CI).
Anything genuinely missing still renders an honest DATA-GAP dash.
"""
from __future__ import annotations

import html
import json
import math
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app import theme, ui  # noqa: E402
from project547 import config, teamstats  # noqa: E402
from project547.names import normalize  # noqa: E402
from project547.sports import SPORTS, default_slate_date  # noqa: E402

OUT = ROOT / "site"
PWA_DIR = ROOT / "app" / "pwa"        # manifest, sw.js, icons — copied into OUT
MIN_EDGE = config.MIN_EDGE
BANKROLL = 1000

# PWA / iOS install: makes the site an installable home-screen app (relative
# paths so it works at a root or project-path Pages URL). See app/pwa/.
PWA_HEAD = (
    '<link rel="manifest" href="manifest.webmanifest">'
    '<meta name="theme-color" content="#faf6ec">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
    '<meta name="apple-mobile-web-app-title" content="360Five">'
    '<link rel="apple-touch-icon" href="apple-touch-icon.png">'
    '<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">'
)
PWA_SW = (
    "<script>if('serviceWorker' in navigator){window.addEventListener('load',"
    "function(){navigator.serviceWorker.register('sw.js').catch(function(){});});}"
    "</script>"
)


def _copy_pwa_assets() -> None:
    """Copy the committed PWA assets (manifest, sw.js, icons) into the site."""
    import shutil as _sh
    if not PWA_DIR.exists():
        return
    for f in PWA_DIR.iterdir():
        if f.is_file():
            _sh.copy2(f, OUT / f.name)

NAV_SPORTS = [s for s in ("MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL", "MLS", "ATP")
              if s in SPORTS]
MATCH_MODEL_SPORTS = {"MLS", "EPL", "ATP", "WTA"}
SPORT_LABELS = {"MLB": "MLB", "WNBA": "WNBA", "NBA": "NBA", "NHL": "NHL",
                "NFL": "NFL", "NCAAF": "NCAAF", "MLS": "Soccer", "ATP": "Tennis"}
# What waits inside each card's "Deep research" disclosure (the summary note).
DEEP_NOTES = {
    "MLB": "lineups · props · splits · CLV",
    "WNBA": "ratings · pace · rest · CLV", "NBA": "ratings · pace · rest · CLV",
    "NHL": "goalies · xG · rest · CLV",
    "NFL": "EPA · splits · rest · CLV", "NCAAF": "EPA · splits · rest · CLV",
    "MLS": "form · xG · priced sides", "ATP": "serve/return · recent matches · sets",
}

# Team primary colors, used as accents (rails/chips) on each game — the
# Lucky-Louie touch. Keyed by normalized full name; unknown teams fall back to
# a deterministic monogram color so every team still gets an accent.
_MLB_COLORS = {
    "Arizona Diamondbacks": "#A71930", "Atlanta Braves": "#CE1141",
    "Baltimore Orioles": "#DF4601", "Boston Red Sox": "#BD3039",
    "Chicago Cubs": "#0E3386", "Chicago White Sox": "#27251F",
    "Cincinnati Reds": "#C6011F", "Cleveland Guardians": "#00385D",
    "Colorado Rockies": "#333366", "Detroit Tigers": "#0C2340",
    "Houston Astros": "#EB6E1F", "Kansas City Royals": "#004687",
    "Los Angeles Angels": "#BA0021", "Los Angeles Dodgers": "#005A9C",
    "Miami Marlins": "#00A3E0", "Milwaukee Brewers": "#12284B",
    "Minnesota Twins": "#002B5C", "New York Mets": "#FF5910",
    "New York Yankees": "#003087", "Athletics": "#005743",
    "Philadelphia Phillies": "#E81828", "Pittsburgh Pirates": "#FDB827",
    "San Diego Padres": "#2F241D", "San Francisco Giants": "#FD5A1E",
    "Seattle Mariners": "#0C2C56", "St. Louis Cardinals": "#C41E3A",
    "Tampa Bay Rays": "#092C5C", "Texas Rangers": "#003278",
    "Toronto Blue Jays": "#134A8E", "Washington Nationals": "#AB0003",
}
_TEAM_COLORS = {normalize(k): v for k, v in _MLB_COLORS.items()}


def _team_color(team: str) -> str:
    if not team:
        return "#8a8472"
    hit = _TEAM_COLORS.get(normalize(team))
    if hit:
        return hit
    from app.assets import monogram
    return monogram(team)[1]


@lru_cache(maxsize=None)
def _gate_table() -> dict:
    try:
        from project547 import edge_gate
        return edge_gate.gate_table()
    except Exception:
        return {}


def _matchup(sport: str, home: str, away: str, asof: str, window: str = "l5") -> dict:
    try:
        return teamstats.matchup(sport, home, away, asof, window=window) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Sharp Sheet enrichment — plain (uncached) ports of the dashboard helpers.
# Every source here reads local files the pipeline already produced, so the
# build never depends on a live API. Failures degrade to DATA-GAP dashes.
# --------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _market_stats() -> dict:
    try:
        from project547 import edge_gate
        return {f"{s}|{m}": v for (s, m), v in edge_gate.market_stats().items()}
    except Exception:
        return {}


@lru_cache(maxsize=None)
def _market_calibration(sport: str) -> dict | None:
    try:
        from project547 import results
        led = [r for r in results.load_ledger()
               if r.get("sport") == sport and r.get("won") is not None]
    except Exception:
        return None
    ms = _market_stats()
    out, any_data = {}, False
    for label, mk in (("Moneyline", "moneyline"), ("Total", "total"), ("Run Line", "spread")):
        rows = [r for r in led if r.get("market") == mk
                and isinstance(r.get("model_prob"), (int, float))
                and pd.notna(r.get("model_prob"))]   # NaN model_prob poisons the mean
        if rows:
            pred = sum(r["model_prob"] for r in rows) / len(rows)
            actual = sum(1 for r in rows if r["won"]) / len(rows)
            out[label] = {"pred": round(pred, 4), "actual": round(actual, 4), "n": len(rows)}
            any_data = True
        else:
            s = ms.get(f"{sport}|{mk}") or {}
            out[label] = {"pred": None, "actual": s.get("win_rate"), "n": s.get("n")}
            any_data = any_data or s.get("win_rate") is not None
    return out if any_data else None


@lru_cache(maxsize=None)
def _pitcher_stats(season: int) -> dict:
    try:
        from project547 import pipeline
        df = pipeline._pitcher_table(season)
        return {r.get("norm_name"): r for r in df.to_dict("records") if r.get("norm_name")}
    except Exception:
        return {}


def _sheet_data(sport: str, g: dict, matchup: dict, date_sel: str) -> dict:
    ms = _market_stats()
    clv = {}
    for mk in ("moneyline", "total", "spread"):
        s = ms.get(f"{sport}|{mk}") or {}
        if s.get("clv_n"):
            clv[mk] = {"avg_clv": s.get("avg_clv"), "clv_n": s.get("clv_n")}
    pitching = None
    if sport == "MLB":
        from project547 import platoon
        # Import these independently: _lookup_float exists, starter_xfip may not.
        # (The dashboard imports both together, so a missing starter_xfip silently
        # stubs _lf → every rate stat blanks to DATA GAP. Split so IP/K9/BB9/xFIP
        # resolve and only the truly-absent starter_xfip fallback no-ops.)
        try:
            from project547.pipeline import _lookup_float as _lf
        except Exception:
            _lf = lambda row, *ks: None           # noqa: E731
        try:
            from project547.pipeline import starter_xfip
        except Exception:
            starter_xfip = lambda *_: None       # noqa: E731
        pstats = _pitcher_stats(int(str(date_sel)[:4]))
        pitching = {}
        for side in ("away", "home"):
            nm, pid = g.get(f"{side}_pitcher"), g.get(f"{side}_pitcher_id")
            if not isinstance(nm, str) or not nm.strip():
                continue
            row = pstats.get(normalize(nm)) or {}
            ip_tot, gs = _lf(row, "IP"), _lf(row, "GS")
            ip = (ip_tot / gs) if ip_tot and gs else None
            k9, bb9 = _lf(row, "K/9", "K9"), _lf(row, "BB/9", "BB9")
            xfip = _lf(row, "xFIP", "FIP")
            if xfip is None:
                try:
                    xfip = starter_xfip(nm)
                except Exception:
                    xfip = None
            try:
                hand = platoon.throws(nm, pid)
            except Exception:
                hand = None
            pitching[f"{side}_sp"] = {
                "name": nm, "id": pid, "hand": hand, "xfip": xfip, "ip": ip,
                "k9": k9, "bb9": bb9, "tto_flag": bool(ip and ip >= 5.8)}
            bp = (matchup or {}).get(f"{side}_bullpen") or {}
            if bp:
                # the fatigue dict has no per-game projection; the bullpen covers
                # whatever the starter doesn't (9 innings), so derive it here.
                proj_ip = bp.get("proj_ip")
                if proj_ip is None and ip:
                    proj_ip = round(max(0.0, 9.0 - ip), 1)
                pitching[f"{side}_bullpen"] = {"fatigue": bp.get("level"),
                                               "proj_ip": proj_ip}
    return {"clv": clv or None, "calibration": _market_calibration(sport),
            "pitching": pitching or None}


@lru_cache(maxsize=None)
def _best_lines(sport: str, date_sel: str) -> dict:
    from project547 import lineshop
    try:
        return {" vs ".join(sorted(k)): v
                for k, v in lineshop.best_lines(sport, date_sel).items()}
    except Exception:
        return {}


def _best_line_for(sport: str, g: dict, date_sel: str) -> dict:
    best = _best_lines(sport, date_sel)
    key = " vs ".join(sorted({normalize(g.get("home_team", "")),
                              normalize(g.get("away_team", ""))}))
    rec = best.get(key)
    if not rec:
        return {}
    out: dict = {}
    ml = rec.get("moneyline") or {}
    for side in ("away_team", "home_team"):
        info = ml.get(normalize(g.get(side, "")))
        if info:
            out[f"{g.get(side, '').split()[-1]} ML"] = {
                "price": info.get("price"), "book": info.get("book")}
    tot = rec.get("total") or {}
    for side in ("over", "under"):
        info = tot.get(side)
        if info:
            ln = f" {info['line']:g}" if info.get("line") is not None else ""
            out[f"{side.title()}{ln}"] = {"price": info.get("price"), "book": info.get("book")}
    return out


# --------------------------------------------------------------------------
# Player-prop drawer index
# --------------------------------------------------------------------------

def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, float):
        return round(v, 3)
    return v


def _norm_key(name: str) -> str:
    """A JS-replicable normalization so a lineup click resolves to prop rows."""
    return re.sub(r"[.\s]+", " ", str(name).lower()).strip()


def _prop_index(sport: str, props: list) -> dict:
    """{norm(player): {name, team, rows:[{market,line,proj,ev,prob,price,pick}]}}."""
    idx: dict = {}
    for p in props or []:
        nm = p.get("player")
        if not isinstance(nm, str) or not nm.strip():
            continue
        edge = None
        try:
            edge = ui._prop_edge(sport, p)
        except Exception:
            edge = None
        row = {"market": ui.short_market(str(p.get("market", ""))),
               "line": _clean(p.get("line")), "proj": _clean(p.get("projection"))}
        if edge:
            ev = edge.get("ev")
            row["ev"] = round(ev * 100, 1) if ev is not None and pd.notna(ev) else None
            mp = edge.get("model_prob")
            row["prob"] = round(mp * 100) if mp is not None and pd.notna(mp) else None
            price = edge.get("price")
            row["price"] = ui.fmt_american(price) if price is not None and pd.notna(price) else ""
            row["pick"] = edge.get("bet")
        key = _norm_key(nm)
        rec = idx.setdefault(key, {"name": nm, "team": p.get("team") or "", "rows": []})
        rec["rows"].append(row)
    # edges first, then projection-only; cap to keep pages light
    for rec in idx.values():
        rec["rows"].sort(key=lambda r: (r.get("ev") is None, -(r.get("ev") or 0)))
        rec["rows"] = rec["rows"][:12]
    return idx


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------

SITE_CSS = """
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--text);
    font-family: var(--font); }
  a { color: inherit; text-decoration: none; }
  .site { display: flex; min-height: 100vh; }
  .sb { width: 196px; flex: 0 0 196px; background: var(--sb1);
    border-right: 1.5px solid var(--line); padding: 18px 14px;
    display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; }
  .osp-logo { display: flex; align-items: center; gap: 10px; margin: 2px 4px 18px; }
  .osp-logo .mk { width: 30px; height: 30px; border-radius: 8px; flex: 0 0 auto;
    display: flex; align-items: center; justify-content: center; font-size: 1rem;
    color: var(--bg); font-weight: 800;
    background: linear-gradient(135deg, var(--acc), var(--acc2)); }
  .osp-brand { font-family: var(--disp); font-size: 1.35rem; font-weight: 700;
    letter-spacing: 0.04em; }
  .sb nav { display: flex; flex-direction: column; gap: 2px; }
  .sb nav a { border-radius: 9px; padding: 9px 12px; font-weight: 600;
    font-family: var(--disp); font-size: 0.9rem; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--muted);
    transition: background .12s ease, color .12s ease; }
  .sb nav a:hover { background: var(--card); color: var(--text); }
  .sb nav a.active { background: var(--card2); color: var(--text);
    box-shadow: inset 3px 0 0 var(--acc); }
  .osp-acct { display: flex; align-items: center; gap: 10px; margin-top: auto;
    padding: 12px 6px 2px; border-top: 1.5px solid var(--line); }
  .osp-acct .av { width: 30px; height: 30px; border-radius: 50%; flex: 0 0 auto;
    display: flex; align-items: center; justify-content: center; font-weight: 700;
    font-family: var(--disp); font-size: 0.9rem; color: var(--bg); background: var(--acc); }
  .osp-acct .nm { font-family: var(--disp); font-weight: 600; font-size: 0.92rem; }
  main { flex: 1; min-width: 0; padding: 20px 30px 60px; max-width: 1180px; }
  .topbar { display: flex; align-items: center; gap: 20px; justify-content: space-between; }
  .osp-title { font-family: var(--disp); font-size: 1.9rem; font-weight: 600; }
  .tb-right { display: flex; align-items: center; gap: 12px; }
  .search { background: var(--card); border: 1.5px solid var(--line); color: var(--text);
    border-radius: 999px; padding: 9px 16px; width: 300px; font-family: var(--font);
    font-size: 0.9rem; outline: none; }
  .search:focus { border-color: var(--acc); }
  .themetoggle { flex: none; width: 40px; height: 40px; border-radius: 999px;
    border: 1.5px solid var(--line); background: var(--card); color: var(--text);
    font-size: 1.1rem; line-height: 1; cursor: pointer; padding: 0;
    transition: border-color .15s ease, background .15s ease; }
  .themetoggle:hover { border-color: var(--acc); }
  .sub { color: var(--muted); font-size: 0.82rem; margin: 6px 2px 12px; }
  /* live-score banner — horizontal strip of game chips, scores update live */
  .scoreboard { overflow-x: auto; -webkit-overflow-scrolling: touch;
    margin: 0 0 16px; border-top: 1px solid var(--line);
    border-bottom: 1px solid var(--line); }
  .scoreboard::-webkit-scrollbar { display: none; }
  .sb-track { display: flex; gap: 8px; padding: 8px 2px; width: max-content; }
  .sb-chip { flex: none; font-family: var(--mono); font-size: 0.76rem;
    color: var(--text); background: var(--card); border: 1px solid var(--line);
    border-radius: 999px; padding: 5px 12px; white-space: nowrap; }
  .sb-chip.live { border-color: var(--good); color: var(--good); font-weight: 600; }
  details.game { border: 1.5px solid var(--line); border-radius: 12px;
    background: var(--card); margin-bottom: 10px; overflow: hidden; }
  details.game > summary { list-style: none; cursor: pointer; padding: 14px 18px;
    font-size: 0.96rem; display: flex; align-items: center; gap: 8px; }
  details.game > summary::-webkit-details-marker { display: none; }
  details.game > summary::before { content: "›"; color: var(--muted);
    font-size: 1.1rem; transition: transform .15s ease; }
  details.game[open] > summary::before { transform: rotate(90deg); }
  details.game > summary:hover { background: var(--card2); }
  .ssbody { padding: 4px 18px 16px; border-top: 1.5px solid var(--line); }
  .legend { color: var(--muted); font-size: 0.82rem; margin: 4px 2px 14px; }
  .feednote { color: var(--muted); font-size: 0.8rem; margin: 16px 2px; }
  /* track-record scorecard */
  .sc-note { color: var(--muted); font-size: 0.82rem; line-height: 1.5;
    margin: 4px 2px 18px; max-width: 760px; }
  .sc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 14px; }
  .sc-card { border: 1.5px solid var(--line); border-radius: 12px; background: var(--card);
    padding: 14px 16px; }
  .sc-top { display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 8px; }
  .sc-name { font-family: var(--disp); font-weight: 700; font-size: 1.05rem; }
  .sc-pill { font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .04em; padding: 3px 8px; border-radius: 20px; }
  .sc-edge { background: #1f7a4d; color: #fff; }
  .sc-noedge { background: #b23b3b; color: #fff; }
  .sc-building { background: var(--line); color: var(--muted); }
  .sc-r { display: flex; justify-content: space-between; padding: 4px 0;
    border-top: 1px solid var(--line); font-size: 0.86rem; }
  .sc-r:first-of-type { border-top: none; }
  .sc-k { color: var(--muted); }
  .sc-v { font-family: var(--mono); font-variant-numeric: tabular-nums; }
  .sc-v.pos { color: var(--good); font-weight: 700; }
  .sc-v.neg { color: var(--neg); font-weight: 700; }
  .sc-mkt { margin-top: 26px; }
  .sc-mkt-h { font-family: var(--disp); font-weight: 700; font-size: 1.05rem;
    margin-bottom: 10px; }
  .sc-tblwrap { overflow-x: auto; }
  .sc-tbl { border-collapse: collapse; width: 100%; max-width: 620px; font-size: 0.88rem; }
  .sc-tbl th { text-align: left; color: var(--muted); font-weight: 600;
    font-size: 0.78rem; text-transform: uppercase; letter-spacing: .03em;
    padding: 6px 12px 6px 0; border-bottom: 1.5px solid var(--line); }
  .sc-tbl td { padding: 8px 12px 8px 0; border-bottom: 1px solid var(--line);
    white-space: nowrap; }
  .sc-tbl .sc-pill { font-size: 0.64rem; }
  /* lineup names read as tappable */
  a.osp-plink { cursor: pointer; border-bottom: 1px dotted var(--acc); }
  /* player-prop drawer */
  .drawer[hidden] { display: none; }
  .drawer { position: fixed; inset: 0; z-index: 100; }
  .drawer-bg { position: absolute; inset: 0; background: rgba(0,0,0,.6); }
  .drawer-panel { position: absolute; top: 0; right: 0; height: 100%; width: 420px;
    max-width: 92vw; background: var(--card); border-left: 1.5px solid var(--line);
    box-shadow: -20px 0 50px rgba(0,0,0,.5); padding: 20px 22px; overflow-y: auto;
    animation: slidein .18s ease; }
  @keyframes slidein { from { transform: translateX(24px); opacity: .4; } to { transform: none; opacity: 1; } }
  .drawer-x { position: absolute; top: 12px; right: 14px; background: none; border: none;
    color: var(--muted); font-size: 1.6rem; line-height: 1; cursor: pointer; }
  .drawer-x:hover { color: var(--text); }
  .drawer .dn { font-family: var(--disp); font-size: 1.3rem; font-weight: 700; }
  .drawer .dt { color: var(--muted); font-size: 0.85rem; margin-bottom: 4px; }
  .drawer .dsub { color: var(--muted); font-size: 0.75rem; text-transform: uppercase;
    letter-spacing: 0.1em; margin: 16px 0 8px; }
  .drow { border: 1.5px solid var(--line); border-radius: 10px; padding: 11px 13px;
    margin-bottom: 9px; }
  .drow .dm { font-family: var(--disp); font-weight: 600; font-size: 0.95rem; }
  .drow .dmeta { color: var(--muted); font-size: 0.8rem; margin-top: 2px;
    font-variant-numeric: tabular-nums; }
  .drow .dpick { margin-top: 6px; font-family: var(--mono); font-size: 0.85rem; color: var(--text); }
  .drow .dev { display: inline-block; margin-top: 6px; font-family: var(--mono);
    font-weight: 700; font-size: 0.82rem; }
  .drow .dev.g { color: var(--good); } .drow .dev.m { color: var(--mid); }
  .drow .dev.f { color: var(--faint); }
  .dnone { color: var(--muted); font-size: 0.88rem; margin-top: 18px; }
  /* 5-W game header band (team colors as accents) */
  .ghead { display: grid; grid-template-columns: 1fr auto 1fr; gap: 14px;
    align-items: stretch; margin: 6px 0 14px; }
  .gt { position: relative; background: var(--card2); border: 1.5px solid var(--line);
    border-radius: 10px; padding: 12px 14px 12px 16px; overflow: hidden; }
  .gt-rail { position: absolute; left: 0; top: 0; bottom: 0; width: 5px; background: var(--tc); }
  .gt-name { font-family: var(--disp); font-weight: 700; font-size: 1.05rem;
    color: var(--text); border-bottom: 2px solid var(--tc); display: inline-block;
    padding-bottom: 1px; }
  .gt-sp { color: var(--muted); font-size: 0.84rem; margin-top: 4px; }
  .gt-wp { font-family: var(--mono); font-size: 0.8rem; color: var(--muted); margin-top: 2px; }
  .gt-mkt { color: var(--acc); margin-left: 4px; }
  .gm { display: flex; flex-direction: column; align-items: center; justify-content: center;
    text-align: center; min-width: 128px; gap: 3px; }
  .gm-proj { font-family: var(--disp); font-weight: 700; font-size: 1.5rem;
    font-variant-numeric: tabular-nums; }
  .gm-line { color: var(--muted); font-size: 0.74rem; font-family: var(--mono); }
  .gm-mkt { color: var(--acc); font-size: 0.74rem; font-family: var(--mono); }
  .gm-delta { margin-left: 5px; opacity: 0.8; font-weight: 700; }
  .askai { margin-top: 6px; background: var(--acc); color: var(--bg); border: none;
    border-radius: 999px; padding: 7px 15px; font-family: var(--disp); font-weight: 700;
    font-size: 0.8rem; letter-spacing: 0.04em; cursor: pointer;
    box-shadow: 0 1px 0 color-mix(in srgb, var(--acc) 60%, #000); }
  .askai:hover { filter: brightness(1.06); }
  .aimodal .dn { font-family: var(--disp); font-size: 1.3rem; font-weight: 700; }
  .aibrief { width: 100%; height: 62vh; margin-top: 12px; resize: none; border-radius: 10px;
    border: 1.5px solid var(--line); background: var(--card2); color: var(--text);
    font-family: var(--mono); font-size: 0.8rem; padding: 12px; line-height: 1.5; }
  .copybtn { margin-top: 12px; background: var(--text); color: var(--bg); border: none;
    border-radius: 8px; padding: 9px 16px; font-family: var(--disp); font-weight: 700;
    font-size: 0.85rem; cursor: pointer; }
  .copybtn.done { background: var(--good); }
  /* match-sport (tennis/soccer) card */
  .mmeta { font-family: var(--disp); font-size: 0.78rem; text-transform: uppercase;
    letter-spacing: 0.1em; color: var(--muted); margin: 4px 2px 12px; }
  .mticket { border: 1.5px solid var(--acc); border-left-width: 5px; border-radius: 10px;
    background: color-mix(in srgb, var(--acc) 7%, transparent); padding: 12px 15px;
    margin: 4px 0 14px; }
  .mticket .mt-pick { font-family: var(--disp); font-weight: 700; font-size: 1.05rem; }
  .mticket .mt-sub { color: var(--muted); font-size: 0.82rem; margin-top: 3px;
    font-family: var(--mono); }
  .mticket .mt-none { color: var(--muted); font-size: 0.86rem; }
  .mtable { border-collapse: collapse; width: 100%; font-size: 0.86rem; margin-top: 6px; }
  .mtable th, .mtable td { text-align: left; padding: 6px 10px;
    border-bottom: 1px solid var(--line); }
  .mtable th { font-family: var(--disp); font-size: 0.72rem; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--muted); }
  .mtable td { font-family: var(--mono); }
  /* ===== scannable card hero (360Five redesign) — scoped under .osp-hero so
     the generic class names never collide with the rest of the site ===== */
  .osp-hero { --dg: var(--good); }
  .osp-hero .hem { color: var(--faint); }
  .osp-hero .eyebrow { display: flex; align-items: center; gap: 8px;
    font-family: var(--mono); font-size: .72rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: .08em; margin: 4px 0 14px; }
  .osp-hero .eyebrow .dot { width: 7px; height: 7px; border-radius: 50%;
    background: var(--acc); flex: none; }
  .osp-hero .eyebrow .sep { color: var(--faint); }
  .osp-hero .eyebrow .lock { margin-left: auto; color: var(--faint);
    text-transform: none; letter-spacing: 0; font-size: .7rem; }
  .osp-hero .match { display: grid; grid-template-columns: 1fr auto 1fr;
    align-items: center; gap: 10px; margin-bottom: 16px; }
  .osp-hero .team { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .osp-hero .team.away { align-items: flex-start; }
  .osp-hero .team.home { align-items: flex-end; text-align: right; }
  .osp-hero .team .nm { font-family: var(--disp); font-weight: 700;
    font-size: 1.06rem; letter-spacing: -.01em; line-height: 1.1; }
  .osp-hero .team .rec { font-family: var(--mono); font-size: .72rem; color: var(--muted); }
  .osp-hero .at { font-family: var(--mono); color: var(--faint); font-size: .8rem; }
  .osp-hero .play { background: color-mix(in srgb, var(--good) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--good) 32%, transparent);
    border-radius: 13px; padding: 13px 14px; display: flex; align-items: center;
    gap: 12px; margin-bottom: 15px; }
  .osp-hero .play.pass { background: var(--card2); border-color: var(--line); --dg: var(--muted); }
  .osp-hero .play-main { flex: 1; min-width: 0; }
  .osp-hero .play .tier { font-family: var(--mono); font-size: .66rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: .1em; color: var(--good); }
  .osp-hero .play.pass .tier { color: var(--muted); }
  .osp-hero .play .pick { font-family: var(--disp); font-size: 1.32rem; font-weight: 700;
    letter-spacing: -.02em; margin: 1px 0 2px; }
  .osp-hero .play .sub { font-family: var(--mono); font-size: .78rem; color: var(--muted); }
  .osp-hero .play .sub b { color: var(--good); font-weight: 700; }
  .osp-hero .play.pass .sub b { color: var(--muted); }
  .osp-hero .dial { flex: none; width: 58px; height: 58px; border-radius: 50%;
    display: grid; place-items: center; position: relative; }
  .osp-hero .dial::before { content: ""; position: absolute; inset: 0; border-radius: 50%;
    background: conic-gradient(var(--dg) calc(var(--v) * 1%), var(--line) 0);
    -webkit-mask: radial-gradient(circle, transparent 60%, #000 61%);
    mask: radial-gradient(circle, transparent 60%, #000 61%); }
  .osp-hero .dial .val { font-family: var(--disp); font-weight: 700; font-size: 1.02rem; line-height: 1; }
  .osp-hero .dial .cap { font-family: var(--mono); font-size: .52rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: .06em; text-align: center; }
  .osp-hero .lbl { font-family: var(--mono); font-size: .66rem; font-weight: 700;
    color: var(--faint); text-transform: uppercase; letter-spacing: .1em; margin: 0 0 8px; }
  .osp-hero .proj { margin-bottom: 16px; }
  .osp-hero .bar { display: grid; grid-template-columns: 42px 1fr auto; align-items: center;
    gap: 10px; margin-bottom: 6px; }
  .osp-hero .bar .who { font-family: var(--mono); font-size: .74rem; color: var(--muted); font-weight: 600; }
  .osp-hero .bar .track { height: 9px; border-radius: 5px; background: var(--card2); overflow: hidden; }
  .osp-hero .bar .fill { height: 100%; border-radius: 5px; background: var(--acc); opacity: .85; }
  .osp-hero .bar.fav .fill { background: var(--good); opacity: 1; }
  .osp-hero .bar .v { font-family: var(--mono); font-size: .82rem; font-weight: 700;
    font-variant-numeric: tabular-nums; min-width: 30px; text-align: right; }
  .osp-hero .proj .tot { font-family: var(--mono); font-size: .76rem; color: var(--muted);
    margin-top: 7px; display: flex; gap: 6px; align-items: baseline; }
  .osp-hero .proj .tot b { color: var(--text); font-weight: 700; }
  .osp-hero .proj .tot .edge { color: var(--good); font-weight: 700; margin-left: auto; }
  .osp-hero .drivers { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 15px; }
  .osp-hero .chip { background: var(--card2); border: 1px solid var(--line); border-radius: 11px;
    padding: 9px 11px; display: flex; gap: 9px; align-items: flex-start; }
  .osp-hero .chip svg { flex: none; width: 16px; height: 16px; stroke: var(--acc); fill: none;
    stroke-width: 1.7; margin-top: 1px; }
  .osp-hero .chip .k { font-family: var(--mono); font-size: .63rem; color: var(--faint);
    text-transform: uppercase; letter-spacing: .06em; }
  .osp-hero .chip .val { font-family: var(--disp); font-size: .85rem; font-weight: 700;
    line-height: 1.15; margin-top: 1px; }
  .osp-hero .chip .note { font-size: .72rem; color: var(--muted); line-height: 1.2; }
  .osp-hero .odds { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 2px; }
  .osp-hero .oz { flex: 1; min-width: 82px; background: var(--card);
    border: 1px solid var(--line); border-radius: 9px; padding: 7px 9px; text-align: center; }
  .osp-hero .oz .k { font-family: var(--mono); font-size: .6rem; color: var(--faint);
    text-transform: uppercase; letter-spacing: .06em; }
  .osp-hero .oz .v { font-family: var(--mono); font-size: .86rem; font-weight: 700;
    font-variant-numeric: tabular-nums; margin-top: 2px; }
  .osp-hero .oz .v.pos { color: var(--good); }
  .osp-hero .prop { display: flex; align-items: center; gap: 9px; padding: 9px 12px;
    margin-bottom: 15px; border: 1px solid var(--line); border-radius: 11px; background: var(--card);
    cursor: pointer; transition: border-color .15s ease; }
  .osp-hero .prop:hover { border-color: var(--acc); }
  .osp-hero .prop .star { color: var(--acc); font-size: .92rem; flex: none; }
  .osp-hero .prop .pk { font-family: var(--mono); font-size: .6rem; color: var(--faint);
    flex: none; text-transform: uppercase; letter-spacing: .08em; }
  .osp-hero .prop .pick { font-weight: 700; font-size: .88rem; flex: 1; min-width: 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .osp-hero .prop .od { font-family: var(--mono); font-size: .8rem; font-weight: 700; flex: none;
    font-variant-numeric: tabular-nums; }
  .osp-hero .prop .ev { font-family: var(--mono); font-size: .76rem; font-weight: 700;
    color: var(--good); flex: none; }
  .osp-hero .prop .chev { color: var(--faint); flex: none; font-size: .85rem; }
  .osp-hero a.prop { border-bottom: none; }
  .osp-hero .prop.pending { cursor: default; }
  .osp-hero .prop.pending .pick { color: var(--muted); font-weight: 400; font-size: .8rem; }
  /* AI second-opinion badge — the daily curation's take on this game */
  .osp-hero .ai-verdict { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
    margin-top: 14px; padding: 9px 12px; border-radius: 10px; border: 1px solid var(--line);
    background: var(--card); font-size: .8rem; }
  .osp-hero .ai-verdict .ai-tag { font-family: var(--disp); font-weight: 700; font-size: .72rem;
    letter-spacing: .03em; flex: none; text-transform: uppercase; }
  .osp-hero .ai-verdict .ai-why { color: var(--muted); line-height: 1.4; }
  .osp-hero .ai-verdict.ai-agree { border-color: color-mix(in srgb, var(--good) 45%, var(--line)); }
  .osp-hero .ai-verdict.ai-agree .ai-tag { color: var(--good); }
  .osp-hero .ai-verdict.ai-differ { border-color: color-mix(in srgb, #e3b341 45%, var(--line)); }
  .osp-hero .ai-verdict.ai-differ .ai-tag { color: var(--mid, #b8860b); }
  .osp-hero .ai-verdict.ai-pass .ai-tag { color: var(--muted); }
  /* Ask-AI validator chip — a quiet secondary action, not a tout */
  .osp-hero .hero-ai { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    margin-top: 10px; padding-top: 12px; border-top: 1px dashed var(--line); }
  .askai.mini { margin: 0; background: transparent; color: var(--acc);
    border: 1.5px solid var(--acc); border-radius: 999px; padding: 6px 13px;
    font-family: var(--disp); font-weight: 700; font-size: .76rem; letter-spacing: .03em;
    cursor: pointer; box-shadow: none; flex: none; }
  .askai.mini:hover { background: color-mix(in srgb, var(--acc) 12%, transparent); }
  .osp-hero .hero-ai-note { font-family: var(--mono); font-size: .68rem; color: var(--muted);
    line-height: 1.35; flex: 1; min-width: 120px; }
  /* ===== AI-curated section on the Plays page (Claude's read) ===== */
  .ai-sec { border: 1.5px solid var(--acc); border-radius: 14px; padding: 15px 17px;
    margin-top: 20px; background: color-mix(in srgb, var(--acc) 5%, transparent); }
  .ai-sec-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
  .ai-sec-mk { color: var(--acc); font-size: 1rem; }
  .ai-sec-t { font-family: var(--disp); font-weight: 700; font-size: 1.05rem; }
  .ai-sec-tag { font-family: var(--mono); font-size: .64rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: .06em; }
  .ai-sec-note { color: var(--text); font-size: .9rem; line-height: 1.5; margin: 10px 0 6px; }
  .ai-plays { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0 4px; }
  .ai-play { border: 1px solid var(--line); border-radius: 11px; padding: 11px 13px;
    background: var(--card); }
  .ai-play-top { display: flex; align-items: baseline; gap: 8px; margin-bottom: 5px; }
  .ai-play-sport { font-family: var(--mono); font-size: .58rem; font-weight: 700; color: var(--acc);
    text-transform: uppercase; letter-spacing: .06em; flex: none; }
  .ai-play-mu { font-size: .74rem; color: var(--muted); flex: 1; min-width: 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ai-play-conf { font-size: .68rem; color: var(--acc); flex: none; letter-spacing: -1px; }
  .ai-play-pick { font-size: .92rem; margin-bottom: 4px; }
  .ai-play-why { font-size: .76rem; color: var(--muted); line-height: 1.45; }
  .ai-sec-foot { font-size: .7rem; color: var(--muted); line-height: 1.45; margin-top: 12px;
    padding-top: 11px; border-top: 1px dashed var(--line); }
  @media (max-width: 620px) { .ai-plays { grid-template-columns: 1fr; } }
  /* deep-research disclosure (holds the full, unchanged research card) */
  details.deep { border-top: 1.5px solid var(--line); margin: 4px -18px 0; }
  details.deep > summary { list-style: none; cursor: pointer; padding: 13px 18px;
    display: flex; align-items: center; gap: 9px; font-family: var(--disp); font-weight: 700;
    font-size: .9rem; color: var(--text); }
  details.deep > summary::-webkit-details-marker { display: none; }
  details.deep .caret { transition: transform .2s ease; color: var(--acc); font-size: .8rem; }
  details.deep[open] .caret { transform: rotate(90deg); }
  details.deep .deepnote { font-family: var(--mono); font-size: .68rem; color: var(--muted);
    font-weight: 400; margin-left: auto; text-transform: none; }
  details.deep > summary:hover { background: var(--card2); }
  .deepbody { padding: 2px 18px 10px; }

  @media (max-width: 820px) {
    .site { flex-direction: column; }
    .sb { width: 100%; height: auto; position: static; flex-direction: row;
      flex-wrap: wrap; align-items: center; gap: 6px; }
    .osp-logo { margin: 0 12px 0 0; } .sb nav { flex-direction: row; flex-wrap: wrap; }
    .osp-acct { margin: 0 0 0 auto; border: none; padding: 0; }
    main { padding: 16px; } .drawer-panel { width: 100%; }
  }
  /* phone: single-column, a card fits one screen, nothing overflows sideways */
  @media (max-width: 720px) {
    html, body { overflow-x: hidden; }
    main { padding: 12px 12px 48px; max-width: 100%; }
    .topbar { flex-wrap: wrap; gap: 10px; }
    .search { width: 100%; }
    .sb nav a { padding: 7px 10px; font-size: 0.82rem; }
    details.game > summary { padding: 12px 14px; font-size: 0.9rem; }
    .ssbody { padding: 4px 14px 14px; }
    details.deep, details.deep > summary { margin-left: -14px; margin-right: -14px; }
    .deepbody { padding: 2px 14px 10px; }
    .osp-hero .match { gap: 8px; }
    .osp-hero .team .nm { font-size: 0.98rem; }
    .osp-hero .play .pick { font-size: 1.18rem; }
    /* keep the 4 "why" chips 2x2 on phones (compact) so the hero stays ~one
       screen rather than a tall single-column stack */
    .osp-hero .drivers { gap: 6px; }
    .osp-hero .chip { padding: 8px 9px; }
    .osp-hero .odds { flex-wrap: nowrap; }
    .osp-hero .oz { min-width: 0; }
    .osp-hero, .deepbody, .ssbody { overflow-x: hidden; }
    .deepbody table, .deepbody .osp-ss-mtab { display: block; overflow-x: auto; }
  }
"""

# ESPN public scoreboard paths per sport, for the live-score banner (fetched
# client-side so scores stay current without a rebuild). Tennis has no team
# scoreboard in this shape, so ATP is omitted.
SB_LEAGUE_PATHS = {
    "MLB": "baseball/mlb", "WNBA": "basketball/wnba", "NBA": "basketball/nba",
    "NFL": "football/nfl", "NCAAF": "football/college-football",
    "NHL": "hockey/nhl", "MLS": "soccer/usa.1",
}

SCOREBOARD_JS = """
  (function(){
    var el = document.getElementById('scoreboard');
    if(!el || !window.SB_LEAGUES || !window.SB_LEAGUES.length) return;
    function chip(ev){
      try{
        var c = ev.competitions[0], cs = c.competitors;
        var home = cs.filter(function(x){return x.homeAway==='home';})[0];
        var away = cs.filter(function(x){return x.homeAway==='away';})[0];
        var st = (ev.status && ev.status.type) || {};
        var det = st.shortDetail || '';
        var live = st.state === 'in', pre = st.state === 'pre';
        var ab = function(t){ return (t.team.abbreviation||t.team.shortDisplayName||'').toUpperCase(); };
        var body = pre
          ? ab(away)+' @ '+ab(home)+' · '+det
          : ab(away)+' '+(away.score||'0')+'  '+ab(home)+' '+(home.score||'0')+' · '+det;
        return '<span class="sb-chip'+(live?' live':'')+'">'+body+'</span>';
      }catch(e){ return ''; }
    }
    function load(){
      var chips = [], pending = window.SB_LEAGUES.length;
      window.SB_LEAGUES.forEach(function(lg){
        fetch('https://site.api.espn.com/apis/site/v2/sports/'+lg+'/scoreboard')
          .then(function(r){return r.json();})
          .then(function(d){ (d.events||[]).forEach(function(ev){ chips.push(chip(ev)); }); })
          .catch(function(){})
          .then(function(){
            if(--pending===0){
              var html = chips.filter(Boolean).join('');
              if(html){ el.innerHTML = '<div class="sb-track">'+html+'</div>'; el.style.display=''; }
              else { el.style.display='none'; }
            }
          });
      });
    }
    load();
    setInterval(load, 60000);   // refresh live scores each minute
  })();
"""

THEME_JS = """
  (function(){
    var root = document.documentElement, btn = document.getElementById('themebtn');
    var meta = document.querySelector('meta[name=theme-color]');
    function tint(){ if(meta) meta.setAttribute('content',
        root.getAttribute('data-theme')==='dark' ? '#000000' : '#faf6ec'); }
    function icon(){ return root.getAttribute('data-theme')==='dark' ? '\\u2600' : '\\u263e'; }
    if(btn){ btn.textContent = icon();
      btn.addEventListener('click', function(){
        var next = root.getAttribute('data-theme')==='dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        try{ localStorage.setItem('theme', next); }catch(e){}
        btn.textContent = icon(); tint();
      });
    }
    tint();  // match the iOS status-bar tint to the theme on load
  })();
"""

DRAWER_JS = """
  const _nk = s => s.toLowerCase().replace(/[.\\s]+/g,' ').trim();
  const drawer = document.getElementById('drawer');
  function openDrawer(name){
    const rec = (window.PROPS||{})[_nk(name)];
    const head = drawer.querySelector('.drawer-head');
    const body = drawer.querySelector('.drawer-body');
    if(!rec || !rec.rows || !rec.rows.length){
      head.innerHTML = `<div class="dn">${name}</div>`;
      body.innerHTML = '<div class="dnone">No props for this player on this slate.</div>';
    } else {
      head.innerHTML = `<div class="dn">${rec.name}</div><div class="dt">${rec.team||''}</div>`;
      body.innerHTML = '<div class="dsub">Props · model edges</div>' + rec.rows.map(r => {
        const meta = [r.line!=null?`line ${r.line}`:'', r.proj!=null?`proj ${r.proj}`:'']
          .filter(Boolean).join(' · ') + (r.prob!=null?` · model ${r.prob}%`:'');
        const pick = r.pick ? `<div class="dpick">▸ ${r.pick} ${r.price||''}</div>` : '';
        const ev = (r.ev!=null) ? `<span class="dev ${r.ev>=4?'g':r.ev>0?'m':'f'}">${r.ev>0?'+':''}${r.ev}% EV</span>` : '';
        return `<div class="drow"><div class="dm">${r.market}</div>`+
               `<div class="dmeta">${meta}</div>${pick}${ev}</div>`;
      }).join('');
    }
    drawer.hidden = false; document.body.style.overflow = 'hidden';
  }
  const aimodal = document.getElementById('aimodal');
  function closeAll(){ drawer.hidden = true; if(aimodal) aimodal.hidden = true;
    document.body.style.overflow=''; }
  function openAI(gid){
    const txt = (window.BRIEFS||{})[gid];
    const ta = document.getElementById('aitext');
    ta.value = txt || 'No prompt available for this game.';
    const btn = document.getElementById('aicopy'); btn.textContent='⧉ Copy prompt'; btn.classList.remove('done');
    aimodal.hidden = false; document.body.style.overflow='hidden';
  }
  document.addEventListener('click', e => {
    const ai = e.target.closest('.askai');
    if(ai){ openAI(ai.dataset.gid); return; }
    const cp = e.target.closest('#aicopy');
    if(cp){ const ta=document.getElementById('aitext'); ta.select();
      const done=()=>{cp.textContent='✓ Copied';cp.classList.add('done');};
      (navigator.clipboard ? navigator.clipboard.writeText(ta.value).then(done).catch(()=>{document.execCommand('copy');done();})
        : (document.execCommand('copy'), done())); return; }
    const a = e.target.closest('a.osp-plink');
    if(a){ e.preventDefault();
      const u = new URL(a.getAttribute('href'), location.href);
      const nm = u.searchParams.get('player');
      if(nm) openDrawer(nm); return; }
    if(e.target.closest('.drawer-bg') || e.target.closest('.drawer-x')) closeAll();
  });
  document.addEventListener('keydown', e => { if(e.key==='Escape') closeAll(); });
  const box = document.querySelector('.search');
  if(box) box.addEventListener('input', e => {
    const q = e.target.value.trim().toLowerCase();
    document.querySelectorAll('[data-search]').forEach(el => {
      el.style.display = (!q || el.dataset.search.includes(q)) ? '' : 'none';
    });
  });
"""


def _nav(active: str, active_sports: list) -> str:
    items = [("plays", "Plays", "plays.html"), ("record", "Track Record", "record.html")]
    for s in active_sports:
        items.append((s.lower(), SPORT_LABELS.get(s, s), f"{s.lower()}.html"))
    links = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">'
        f'{html.escape(label)}</a>'
        for key, label, href in items)
    return (f'<aside class="sb"><div class="osp-logo"><span class="mk">◈</span>'
            f'<span class="osp-brand">360Five</span></div><nav>{links}</nav>'
            f'<div class="osp-acct"><span class="av">E</span>'
            f'<span class="nm">EdgeCash</span></div></aside>')


def _page(active: str, title: str, gen: str, body: str, active_sports: list,
          props: dict | None = None, briefs: dict | None = None) -> str:
    props_json = json.dumps(props or {}, allow_nan=False).replace("</", "<\\/")
    briefs_json = json.dumps(briefs or {}, allow_nan=False).replace("</", "<\\/")
    # ESPN scoreboard paths for the sports in season on this build (live-score banner)
    sb_leagues = json.dumps([SB_LEAGUE_PATHS[s] for s in active_sports
                             if s in SB_LEAGUE_PATHS])
    drawer = ('<div id="drawer" class="drawer" hidden><div class="drawer-bg"></div>'
              '<div class="drawer-panel"><button class="drawer-x" aria-label="Close">×</button>'
              '<div class="drawer-head"></div><div class="drawer-body"></div></div></div>')
    ai_modal = ('<div id="aimodal" class="drawer" hidden><div class="drawer-bg"></div>'
                '<div class="drawer-panel aimodal"><button class="drawer-x" aria-label="Close">×</button>'
                '<div class="dn">Ask AI</div><div class="dt">Copy this prompt into ChatGPT, '
                'Claude, or any chatbot — it uses your own subscription.</div>'
                '<button class="copybtn" id="aicopy">⧉ Copy prompt</button>'
                '<textarea class="aibrief" id="aitext" readonly></textarea></div></div>')
    return (
        f"<!doctype html><html data-theme=\"light\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, "
        f"viewport-fit=cover\">"
        # set the saved theme before first paint (no flash)
        f"<script>try{{var t=localStorage.getItem('theme')||'light';"
        f"document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}</script>"
        f"<title>360Five — {html.escape(title)}</title>"
        f"{PWA_HEAD}"
        f"{theme.theme_css_both()}<style>{SITE_CSS}</style></head><body>"
        f"<div class=\"site\">{_nav(active, active_sports)}<main>"
        f"<div class=\"topbar\"><div class=\"osp-title\">{html.escape(title)}</div>"
        f"<div class=\"tb-right\">"
        f"<button id=\"themebtn\" class=\"themetoggle\" aria-label=\"Toggle dark mode\">"
        f"☾</button>"
        f"<input class=\"search\" placeholder=\"\U0001f50d  team or player…\"></div></div>"
        f"<div class=\"sub\">Updated {html.escape(gen)} ET · refreshes hourly · "
        f"not financial advice</div>"
        f"<div id=\"scoreboard\" class=\"scoreboard\" style=\"display:none\"></div>"
        f"{body}</main></div>{drawer}{ai_modal}"
        f"<script>window.PROPS={props_json};window.BRIEFS={briefs_json};"
        f"window.SB_LEAGUES={sb_leagues};</script>"
        f"<script>{DRAWER_JS}</script><script>{THEME_JS}</script>"
        f"<script>{SCOREBOARD_JS}</script>{PWA_SW}</body></html>")


# --------------------------------------------------------------------------
# Content builders
# --------------------------------------------------------------------------

def _has_cards(day: dict, sport: str) -> bool:
    return bool((day.get(sport) or {}).get("games"))


def _scorecard_page(perf: dict) -> str:
    """Honest track record per sport: the headline is CLV (did our *bets* beat the
    closing line — the leading edge indicator), shown next to record/ROI and the
    projection-vs-market Brier. Small samples are labelled, not over-read."""
    by = perf.get("by_sport", {})
    order = ["overall"] + sorted(by, key=lambda s: -(by[s].get("bets") or 0))

    def _verdict(p: dict) -> tuple[str, str]:
        n_games, n_bets = p.get("graded_games") or 0, p.get("bets") or 0
        clv = p.get("avg_clv_pct")
        if n_games < 40 or n_bets < 25 or clv is None:
            return ("building", "Building sample")
        return ("edge", "Beating the close") if clv > 0 else ("noedge", "No edge yet")

    def _row(label, val, cls=""):
        return (f"<div class='sc-r'><span class='sc-k'>{label}</span>"
                f"<span class='sc-v {cls}'>{val}</span></div>")

    def _pct(v, dp=1):
        return f"{v:+.{dp}f}%" if isinstance(v, (int, float)) else "—"

    cards = []
    for key in order:
        p = perf.get("overall", {}) if key == "overall" else by[key]
        name = "All sports" if key == "overall" else SPORT_LABELS.get(key, key)
        cls, label = _verdict(p)
        clv = p.get("avg_clv_pct")
        beat = p.get("clv_beat_rate")
        bvm = p.get("brier_vs_market")
        wr = p.get("bet_win_rate")
        units = p.get("units")
        body = "".join([
            _row("Avg CLV", _pct(clv),
                 "pos" if isinstance(clv, (int, float)) and clv > 0 else
                 ("neg" if isinstance(clv, (int, float)) and clv < 0 else "")),
            _row("CLV beat rate", f"{beat*100:.0f}%" if isinstance(beat, (int, float)) else "—"),
            _row("Bets graded", str(p.get("bets") or 0)),
            _row("Win rate", f"{wr*100:.0f}%" if isinstance(wr, (int, float)) else "—"),
            _row("Units", f"{units:+.1f}" if isinstance(units, (int, float)) else "—",
                 "pos" if isinstance(units, (int, float)) and units > 0 else
                 ("neg" if isinstance(units, (int, float)) and units < 0 else "")),
            _row("ROI", _pct(p.get("roi_pct")),
                 "pos" if isinstance(p.get("roi_pct"), (int, float)) and p["roi_pct"] > 0 else
                 ("neg" if isinstance(p.get("roi_pct"), (int, float)) and p["roi_pct"] < 0 else "")),
            _row("Proj vs market", f"{bvm:+.4f}" if isinstance(bvm, (int, float)) else "—",
                 "pos" if isinstance(bvm, (int, float)) and bvm > 0 else
                 ("neg" if isinstance(bvm, (int, float)) and bvm < 0 else "")),
        ])
        cards.append(
            f"<div class='sc-card'><div class='sc-top'>"
            f"<span class='sc-name'>{html.escape(name)}</span>"
            f"<span class='sc-pill sc-{cls}'>{label}</span></div>{body}</div>")

    note = ("<div class='sc-note'>The number that matters is <b>Avg CLV</b> — how "
            "much our graded bets beat the market's closing line. Positive CLV is the "
            "leading sign of a real edge, ahead of noisy win/loss. <b>Proj vs market</b> "
            "is whether our published win probability out-calibrates the closing line "
            "(&gt;0 = we do). Samples under ~40 games are labelled <i>building</i> — "
            "don't read them yet. Not financial advice.</div>")
    return (f"<div class='sc-wrap'>{note}<div class='sc-grid'>{''.join(cards)}</div>"
            f"{_market_table()}</div>")


def _market_table() -> str:
    """Per-(sport, market) edge status from the demonstrated-edge gate: which
    specific markets have proven CLV (cleared), are still gathering it
    (building), or are suppressed as proven losers (gated). This is where the
    headline sport-level CLV actually comes from."""
    gt = _gate_table()
    if not gt:
        return ""
    STATUS = {"cleared": ("sc-edge", "Cleared"),
              "probation": ("sc-building", "Building"),
              "gated": ("sc-noedge", "Gated")}
    rows = []
    for (sp, mk), v in sorted(gt.items()):
        cls, label = STATUS.get(v.get("status"), ("sc-building", str(v.get("status"))))
        clv = v.get("avg_clv")
        clv_txt = f"{clv * 100:+.1f}%" if isinstance(clv, (int, float)) else "—"
        clv_cls = ("pos" if isinstance(clv, (int, float)) and clv > 0
                   else "neg" if isinstance(clv, (int, float)) and clv < 0 else "")
        rows.append(
            f"<tr><td>{html.escape(SPORT_LABELS.get(sp, sp))}</td>"
            f"<td>{html.escape(str(mk))}</td>"
            f"<td><span class='sc-pill {cls}'>{label}</span></td>"
            f"<td class='sc-v {clv_cls}'>{clv_txt}</td>"
            f"<td class='sc-v'>{v.get('clv_n') or 0}</td></tr>")
    return (f"<div class='sc-mkt'><div class='sc-mkt-h'>By market — where the edge "
            f"is proven</div><div class='sc-tblwrap'><table class='sc-tbl'><thead><tr>"
            f"<th>Sport</th><th>Market</th><th>Status</th><th>Avg CLV</th>"
            f"<th>CLV&nbsp;n</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
            f"</div></div>")


def _load_ai_curation(date_sel: str) -> dict:
    """The AI curation for this slate date, or ``{}`` if the cache is missing (no
    API key configured / job hasn't run). Never raises — a bad file degrades to
    'no AI layer' rather than breaking the build."""
    path = config.OUTPUT_DIR / "ai_curation.json"
    if not path.exists():
        return {}
    try:
        cache = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(cache, dict):
        return {}
    entry = cache.get(date_sel)
    return entry if isinstance(entry, dict) else {}


_AI_CONF = {5: "★★★★★", 4: "★★★★☆", 3: "★★★☆☆", 2: "★★☆☆☆", 1: "★☆☆☆☆"}
_AI_MODEL_LABEL = {"claude-opus-4-8": "Claude Opus 4.8",
                   "claude-opus-4-7": "Claude Opus 4.7",
                   "claude-sonnet-5": "Claude Sonnet 5",
                   "claude-haiku-4-5": "Claude Haiku 4.5"}


def _ai_model_name(model: str) -> str:
    return _AI_MODEL_LABEL.get(str(model), "Claude")


def _ai_conf_stars(val) -> str:
    """Confidence 1-5 → stars, tolerant of a float/str/garbage value from a
    hand-edited or schema-drifted cache (defaults to 3)."""
    try:
        n = max(1, min(5, int(float(val))))
    except (TypeError, ValueError):
        n = 3
    return _AI_CONF[n]


def _ai_price(odds) -> str:
    """American odds → display, tolerant of a non-numeric value (returns '')."""
    if odds is None:
        return ""
    try:
        return ui.fmt_american(odds) or ""
    except (TypeError, ValueError):
        return ""


def _ai_curated_section(ai_cur: dict) -> str:
    """The 'Claude's read' block for the Plays page: an honest slate note plus
    the AI's curated top plays, shown *beside* the model's board as a second
    opinion. Empty string when there's no curation for the slate.

    Defensive by contract: the curation file is written normalized, but this is
    the *consumer* — a malformed / hand-edited / partially-written cache must
    degrade to 'no AI section', never abort the whole site build."""
    if not isinstance(ai_cur, dict) or not ai_cur:
        return ""
    try:
        plays = [p for p in (ai_cur.get("top_plays") or []) if isinstance(p, dict)]
        note = str(ai_cur.get("slate_note") or "")
        when = str(ai_cur.get("generated_at") or "")[:16].replace("T", " ")
        rows = ["<div class='ai-sec'>",
                "<div class='ai-sec-head'><span class='ai-sec-mk'>◆</span>"
                "<span class='ai-sec-t'>Claude's read</span>"
                "<span class='ai-sec-tag'>second opinion · not the model</span></div>"]
        if note:
            rows.append(f"<div class='ai-sec-note'>{html.escape(note)}</div>")
        if not plays:
            rows.append("<div class='pl-none'>No AI plays on this slate — a pass is "
                        "a valid read.</div>")
        else:
            rows.append("<div class='ai-plays'>")
            for p in plays:
                conf = _ai_conf_stars(p.get("confidence"))
                price = _ai_price(p.get("odds"))
                line = p.get("line")
                line_txt = f" {line:g}" if isinstance(line, (int, float)) else ""
                pick = f"{p.get('side', '')}{line_txt} {price}".strip()
                rows.append(
                    f"<div class='ai-play'>"
                    f"<div class='ai-play-top'><span class='ai-play-sport'>"
                    f"{html.escape(str(p.get('sport', '')))}</span>"
                    f"<span class='ai-play-mu'>{html.escape(str(p.get('matchup', '')))}</span>"
                    f"<span class='ai-play-conf'>{conf}</span></div>"
                    f"<div class='ai-play-pick'>{html.escape(str(p.get('market', '')))}: "
                    f"<b>{html.escape(pick)}</b></div>"
                    f"<div class='ai-play-why'>{html.escape(str(p.get('rationale', '')))}</div>"
                    f"</div>")
            rows.append("</div>")
        foot = (f"<div class='ai-sec-foot'>Curated by "
                f"{html.escape(_ai_model_name(ai_cur.get('model')))}"
                f"{f' · {html.escape(when)} ET' if when else ''} · graded on "
                f"closing-line value like every play. A validator, never an "
                f"automatic tail.</div>")
        rows.append(foot + "</div>")
        return "".join(rows)
    except Exception:
        return ""  # never let the AI layer break the build


def _plays_board(day: dict, active_sports: list) -> str:
    board = ui.build_best_bets(day, MIN_EDGE)
    if not board.empty:
        board = board[pd.to_numeric(board["ev"], errors="coerce") < 0.30]
    rows = ["<div class='pl-board'>",
            "<div class='pl-head'><span>Matchup</span><span>Play</span></div>"]
    for sport in active_sports:
        sub = (board[board["sport"] == sport] if not board.empty else board.iloc[0:0])
        rows.append(f"<div class='pl-grp'>{html.escape(SPORT_LABELS.get(sport, sport))}</div>")
        if sub.empty:
            rows.append("<div class='pl-none'>No Plays</div>")
            continue
        for _, r in sub.reset_index(drop=True).iterrows():
            price = ui.fmt_american(r["price"]) if pd.notna(r.get("price")) else ""
            play = f"{r['bet']} {price}".strip()
            key = f"{r['game']} {play}".lower()
            rows.append(
                f"<div class='pl-row' data-search=\"{html.escape(key, quote=True)}\">"
                f"<span class='m'>{html.escape(str(r['game']))}</span>"
                f"<span class='p'>{html.escape(play)}</span></div>")
    rows.append("</div>")
    rows.append("<div class='sub'>The model's edges, plain — passes shown too.</div>")
    return "".join(rows)


def _priced_sides(sport: str, g: dict) -> str:
    try:
        edges = ui.match_edge_table(sport, g)
    except Exception:
        edges = pd.DataFrame()
    if edges.empty:
        return ""
    return ("<div style='overflow-x:auto'>"
            + edges.to_html(index=False, border=0, classes="mtable") + "</div>")


def _tennis_ticket(sport: str, g: dict) -> str:
    cands = []
    for pl, ev, price, wp in (
        ("player1", "p1_ev", "p1_price", "player1_win_prob"),
        ("player2", "p2_ev", "p2_price", "player2_win_prob")):
        e = g.get(ev)
        if e is not None and pd.notna(e):
            cands.append((g.get(pl, ""), e, g.get(price), g.get(wp)))
    if not cands:
        return ("<div class='mticket'><div class='mt-none'>Market price attaches at "
                "match time — the surface-aware Elo read above stands on its own "
                "until then.</div></div>")
    name, ev, price, wp = max(cands, key=lambda c: c[1])
    try:
        from project547 import edge_gate
        status = edge_gate.status_for(sport, "tennis_moneyline", _gate_table())
    except Exception:
        status = None
    tier = ui.play_tier(ev, MIN_EDGE, gate=status)
    kelly = g.get("kelly")
    stake = (f" · {kelly:.1f}u" if isinstance(kelly, (int, float)) and pd.notna(kelly)
             else "")
    return (f"<div class='mticket'><div class='mt-pick'>{html.escape(str(name))} "
            f"{ui.fmt_american(price)} — {html.escape(tier['label'])}</div>"
            f"<div class='mt-sub'>model {ui._pct(wp)} · {ev * 100:+.1f}% EV{stake}</div></div>")


def _tennis_card(sport: str, g: dict) -> str:
    """A real two-player match card: surface-Elo win%, sample, bet ticket, and
    the priced sides — built entirely from the row (no pipeline dependency)."""
    p1, p2 = str(g.get("player1", "")), str(g.get("player2", ""))
    q1, q2 = g.get("player1_win_prob"), g.get("player2_win_prob")
    fav1 = (q1 or 0) >= (q2 or 0)
    surface = str(g.get("surface") or "—").title()
    tour = str(g.get("tournament", ""))
    slam = any(s in tour.lower() for s in
               ("wimbledon", "roland garros", "french open", "us open", "australian open"))
    best_of = "Best of 5" if (slam and sport == "ATP") else "Best of 3"
    meta = (f"<div class='mmeta'>{html.escape(tour)}{' · ' if tour else ''}"
            f"{html.escape(surface)} (surface-inferred) · {best_of}</div>")

    def panel(name, q, n, fav):
        rail = "var(--acc)" if fav else "var(--line)"
        sub = (f"{int(n)} matches" if isinstance(n, (int, float)) and pd.notna(n) else "—")
        return (f"<div class='gt' style='--tc:{rail}'><div class='gt-rail'></div>"
                f"<div class='gt-name'>{html.escape(name)}</div>"
                f"<div class='gt-wp'>win {ui._pct(q)}</div>"
                f"<div class='gt-sp'>surface Elo · {sub}</div></div>")
    panels = (f"<div class='ghead'>{panel(p1, q1, g.get('p1_matches'), fav1)}"
              f"<div class='gm'><div class='gm-proj'>vs</div></div>"
              f"{panel(p2, q2, g.get('p2_matches'), not fav1)}</div>")
    return meta + panels + _tennis_ticket(sport, g) + _priced_sides(sport, g)


def _match_body(sport: str, g: dict) -> str:
    try:
        edges = ui.match_edge_table(sport, g)
    except Exception:
        edges = pd.DataFrame()
    parts = []
    if sport in ("ATP", "WTA"):
        parts.append(
            f"<p><strong>{html.escape(str(g.get('player1', 'P1')))}</strong> "
            f"{ui._pct(g.get('player1_win_prob'))} · "
            f"<strong>{html.escape(str(g.get('player2', 'P2')))}</strong> "
            f"{ui._pct(g.get('player2_win_prob'))}</p>")
    else:
        parts.append(
            f"<p>Home {ui._pct(g.get('home_win_prob'))} · Draw "
            f"{ui._pct(g.get('draw_prob'))} · Away {ui._pct(g.get('away_win_prob'))}</p>")
    if not edges.empty:
        parts.append(edges.to_html(index=False, border=0))
    else:
        parts.append("<div class='feednote'>No market prices yet for this match.</div>")
    return "".join(parts)


def _market_proj(g: dict) -> dict:
    """Market-implied projection from the de-vigged prices: each side's win% (a
    two-way moneyline de-vig) and the posted total line. The market is the
    strongest public estimate — showing it beside ours makes the gap explicit
    (roadmap: the model currently trails the close, so this is honest)."""
    from project547 import odds
    out: dict = {}
    aml, hml = g.get("away_ml"), g.get("home_ml")
    if (isinstance(aml, (int, float)) and isinstance(hml, (int, float))
            and not (math.isnan(aml) or math.isnan(hml))):
        try:
            fa, fh = odds.devig_two_way(odds.implied_prob(aml), odds.implied_prob(hml))
            out["away_wp"], out["home_wp"] = fa, fh
        except (ValueError, ZeroDivisionError):
            pass
    tl = g.get("total_line")
    if isinstance(tl, (int, float)) and not math.isnan(tl):
        out["total"] = float(tl)
    return out


def _team_block(sport: str, g: dict, side: str, mkt: dict) -> str:
    """One team's colored header cell: name (team-color rail), starter, and both
    win probabilities — our model's and the market-implied."""
    team = g.get(f"{side}_team", "")
    color = _team_color(team)
    wp = ui._pct(g.get(f"{side}_win_prob"))
    mwp = mkt.get(f"{side}_wp")
    mkt_wp = (f"<span class='gt-mkt'>mkt {ui._pct(mwp)}</span>"
              if mwp is not None else "")
    starter = g.get(f"{side}_pitcher") if sport == "MLB" else None
    sp = (f"<div class='gt-sp'>⚾ {html.escape(str(starter))}</div>"
          if isinstance(starter, str) and starter.strip() else "")
    return (f"<div class='gt' style='--tc:{color}'><div class='gt-rail'></div>"
            f"<div class='gt-name'>{html.escape(str(team))}</div>{sp}"
            f"<div class='gt-wp'>model {wp} {mkt_wp}</div></div>")


def _game_header(sport: str, g: dict, gid: str) -> str:
    """The 5-W answer band above a Sharp Sheet: WHO (teams, colored, starters) ·
    WHAT (our projected line AND the market's, side by side) · Ask-AI chip.
    Showing both numbers is deliberate — the market is the sharper estimate today,
    and the gap is the story."""
    mkt = _market_proj(g)
    away, home = _team_block(sport, g, "away", mkt), _team_block(sport, g, "home", mkt)
    proj = f"{ui._num(ui._exp(g, 'away'))}–{ui._num(ui._exp(g, 'home'))}"
    mtot = g.get("proj_total")
    ktot = mkt.get("total")
    # model total row + market total row, with the delta when both exist
    delta = ""
    if isinstance(mtot, (int, float)) and ktot is not None:
        d = float(mtot) - ktot
        delta = f"<span class='gm-delta'>{d:+.1f}</span>"
    mkt_line = (f"<div class='gm-mkt'>market total {ui._num(ktot)}{delta}</div>"
                if ktot is not None else "")
    mid = (f"<div class='gm'><div class='gm-proj'>{proj}</div>"
           f"<div class='gm-line'>model total {ui._num(mtot)}</div>{mkt_line}"
           f"<button class='askai' data-gid='{html.escape(gid, quote=True)}'>"
           f"◆ Ask AI</button></div>")
    return f"<div class='ghead'>{away}{mid}{home}</div>"


def _sport_feed(sport: str, day: dict, date_sel: str,
                ai_cur: dict | None = None) -> tuple[str, dict, dict]:
    blob = day.get(sport, {})
    games = blob.get("games", []) or []
    props = blob.get("props") or []
    if not games:
        return "<div class='feednote'>No games scheduled for this slate.</div>", {}, {}
    gt = _gate_table()
    verdicts = (ai_cur or {}).get("verdicts")
    if not isinstance(verdicts, dict):
        verdicts = {}
    briefs: dict = {}
    out = ["<div class='legend'>🟢 play · 🟡 lean · ⚪ pass — open a game for the full "
           "research card, tap any lineup name for that player's props, or ◆ Ask AI "
           "to copy a prompt for your chatbot.</div>"]
    for i, g in enumerate(games):
        try:
            label, auto = ui.sheet_headline(sport, g, min_edge=MIN_EDGE,
                                            gate_table=gt, bankroll=BANKROLL)
        except Exception:
            label, auto = f"{g.get('away_team','')} @ {g.get('home_team','')}", False
        search = re.sub(r"[*_]", "", label).lower()
        gid = str(g.get("game_pk") or f"{sport}-{i}")
        # Each card = a scannable HERO (the play + the why, one screen) followed
        # by the FULL existing research card, collapsed one tap away in "Deep
        # research". The hero replaces the old 5-W game header (and its N/A odds
        # boxes); the deep body is the unchanged detailed renderer.
        hero = ""
        try:
            if sport in ("ATP", "WTA"):
                try:
                    briefs[gid] = ui.ai_brief_tennis(sport, g)
                except Exception:
                    briefs.pop(gid, None)
                hero = ui.hero_html(sport, g, {}, gate_table=gt, min_edge=MIN_EDGE,
                                    props=props, gid=gid if gid in briefs else None,
                                    ai_verdict=verdicts.get(gid))
                body = _tennis_card(sport, g)
            elif sport in MATCH_MODEL_SPORTS:
                try:
                    briefs[gid] = ui.ai_brief_game(sport, g, None, min_edge=MIN_EDGE)
                except Exception:
                    briefs.pop(gid, None)
                hero = ui.hero_html(sport, g, {}, gate_table=gt, min_edge=MIN_EDGE,
                                    props=props, gid=gid if gid in briefs else None,
                                    ai_verdict=verdicts.get(gid))
                body = _match_body(sport, g)
            else:
                m = _matchup(sport, g.get("home_team", ""), g.get("away_team", ""), date_sel)
                try:
                    data = _sheet_data(sport, g, m, date_sel)
                except Exception:
                    data = {}
                try:
                    best_line = _best_line_for(sport, g, date_sel)
                except Exception:
                    best_line = {}
                try:
                    briefs[gid] = ui.ai_brief_game(sport, g, m, min_edge=MIN_EDGE)
                except Exception:
                    briefs.pop(gid, None)
                hero = ui.hero_html(sport, g, m, data=data, gate_table=gt,
                                    min_edge=MIN_EDGE, bankroll=BANKROLL,
                                    best_line=best_line, props=props,
                                    gid=gid if gid in briefs else None,
                                    ai_verdict=verdicts.get(gid))
                body = ui.sharp_sheet_html(sport, g, m, window="l5", min_edge=MIN_EDGE,
                                           gate_table=gt, bankroll=BANKROLL, props=props,
                                           best_line=best_line, data=data)
        except Exception as e:
            body = f"<div class='feednote'>Sheet unavailable ({html.escape(str(e))}).</div>"
        deep_note = DEEP_NOTES.get(sport, "lineups · stats · splits · CLV")
        deep = (f"<details class='deep'><summary><span class='caret'>▸</span> "
                f"Deep research <span class='deepnote'>{deep_note}</span></summary>"
                f"<div class='deepbody'>{body}</div></details>")
        out.append(
            f"<details class='game'{' open' if auto else ''} "
            f"data-search=\"{html.escape(search, quote=True)}\">"
            f"<summary>{_md_bold(label)}</summary>"
            f"<div class='ssbody'>{hero}{deep}</div></details>")
    return "".join(out), _prop_index(sport, props), briefs


def _md_bold(s: str) -> str:
    out, last = [], 0
    for m in re.finditer(r"\*\*(.+?)\*\*", s):
        out.append(html.escape(s[last:m.start()]))
        out.append(f"<strong>{html.escape(m.group(1))}</strong>")
        last = m.end()
    out.append(html.escape(s[last:]))
    return "".join(out)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def main() -> int:
    path = config.OUTPUT_DIR / "latest.json"
    if not path.exists():
        print("no latest.json — run the pipeline first", file=sys.stderr)
        return 1
    data = json.loads(path.read_text())
    slates = data.get("slates") or {data.get("date", "latest"): data.get("sports", {})}
    dates = data.get("dates") or sorted(slates.keys(), reverse=True)
    date_sel = default_slate_date(dates, slates) or data.get("primary_date", dates[0])
    day = slates.get(date_sel, {})
    gen = str(data.get("generated_at", ""))[:16].replace("T", " ")

    # Daily AI slate curation (scripts/ai_curation.py) — optional; absent when
    # the API key isn't configured, in which case the site renders exactly as
    # before (no badges, no AI list).
    ai_cur = _load_ai_curation(date_sel)

    # Only sports that actually have cards today appear in the nav / get a page.
    active_sports = [s for s in NAV_SPORTS if _has_cards(day, s)]

    import shutil
    shutil.rmtree(OUT, ignore_errors=True)  # never leave a dropped sport's stale page
    OUT.mkdir(parents=True, exist_ok=True)
    _copy_pwa_assets()   # manifest, sw.js, icons -> installable home-screen app
    plays_body = _plays_board(day, active_sports) + _ai_curated_section(ai_cur)
    plays_html = _page("plays", "Plays", gen, plays_body, active_sports)
    (OUT / "plays.html").write_text(plays_html)
    (OUT / "index.html").write_text(plays_html)
    (OUT / "record.html").write_text(
        _page("record", "Track Record", gen,
              _scorecard_page(data.get("performance") or {}), active_sports))
    pages = 2

    for sport in active_sports:
        body, prop_idx, briefs = _sport_feed(sport, day, date_sel, ai_cur)
        (OUT / f"{sport.lower()}.html").write_text(
            _page(sport.lower(), SPORT_LABELS.get(sport, sport), gen, body,
                  active_sports, props=prop_idx, briefs=briefs))
        pages += 1

    print(f"built {pages} pages → {OUT}  (slate {date_sel}; "
          f"active: {', '.join(active_sports) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
