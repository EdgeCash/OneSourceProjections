"""Static-site generator for 360Five.

The heavy modelling already runs offline (the hourly GitHub Action bakes
``data/output/latest.json``); this script turns that JSON into a plain static
site — no server, no per-click re-runs — reusing the *same* renderers the
Streamlit app uses (``app.ui.sharp_sheet_html`` / ``build_best_bets`` and the
shared ``app.theme`` CSS), so the look matches exactly.

Output: ``site/`` — one page per in-season sport plus the cross-sport PLAYS
board, a sidebar nav, and native ``<details>`` Sharp Sheets (no JS needed to
expand). Run:  ``python scripts/build_static.py``

Prototype scope: team-sport Sharp Sheets + the PLAYS board render in full;
per-game CLV/calibration/pitcher enrichment (the Streamlit ``data=`` object)
is deferred to the production build, so those fields show honest DATA-GAP
dashes — never fabricated.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app import theme, ui  # noqa: E402
from project547 import config, teamstats  # noqa: E402
from project547.sports import SPORTS, default_slate_date  # noqa: E402

OUT = ROOT / "site"
MIN_EDGE = config.MIN_EDGE
BANKROLL = 1000

NAV_SPORTS = [s for s in ("MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL", "MLS", "ATP")
              if s in SPORTS]
MATCH_MODEL_SPORTS = {"MLS", "EPL", "ATP", "WTA"}
SPORT_LABELS = {"MLB": "MLB", "WNBA": "WNBA", "NBA": "NBA", "NHL": "NHL",
                "NFL": "NFL", "NCAAF": "NCAAF", "MLS": "Soccer", "ATP": "Tennis"}


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


def _md_bold(s: str) -> str:
    """Render a sheet_headline markdown label as safe HTML (only **bold** + text)."""
    out, last = [], 0
    for m in re.finditer(r"\*\*(.+?)\*\*", s):
        out.append(html.escape(s[last:m.start()]))
        out.append(f"<strong>{html.escape(m.group(1))}</strong>")
        last = m.end()
    out.append(html.escape(s[last:]))
    return "".join(out)


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------

SITE_CSS = """
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--text);
    font-family: var(--font); }
  a { color: inherit; text-decoration: none; }
  .site { display: flex; min-height: 100vh; }
  /* sidebar */
  .sb { width: 232px; flex: 0 0 232px; background: var(--sb1);
    border-right: 1.5px solid var(--line); padding: 18px 14px;
    display: flex; flex-direction: column; position: sticky; top: 0;
    height: 100vh; }
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
  /* main */
  main { flex: 1; min-width: 0; padding: 20px 30px 60px; max-width: 1180px; }
  .topbar { display: flex; align-items: center; gap: 20px; justify-content: space-between; }
  .osp-title { font-family: var(--disp); font-size: 1.9rem; font-weight: 600; }
  .search { background: var(--card); border: 1.5px solid var(--line);
    color: var(--text); border-radius: 999px; padding: 9px 16px; width: 300px;
    font-family: var(--font); font-size: 0.9rem; outline: none; }
  .search:focus { border-color: var(--acc); }
  .sub { color: var(--muted); font-size: 0.82rem; margin: 6px 2px 18px; }
  /* game feed: native details/summary Sharp Sheets */
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
  @media (max-width: 820px) {
    .site { flex-direction: column; }
    .sb { width: 100%; height: auto; position: static; flex-direction: row;
      flex-wrap: wrap; align-items: center; gap: 6px; }
    .osp-logo { margin: 0 12px 0 0; } .sb nav { flex-direction: row; flex-wrap: wrap; }
    .osp-acct { margin: 0 0 0 auto; border: none; padding: 0; }
    main { padding: 16px; }
  }
"""

SEARCH_JS = """
  const box = document.querySelector('.search');
  if (box) box.addEventListener('input', e => {
    const q = e.target.value.trim().toLowerCase();
    document.querySelectorAll('[data-search]').forEach(el => {
      el.style.display = (!q || el.dataset.search.includes(q)) ? '' : 'none';
    });
  });
"""


def _nav(active: str) -> str:
    items = [("plays", "Plays", "plays.html")]
    for s in NAV_SPORTS:
        items.append((s.lower(), SPORT_LABELS.get(s, s), f"{s.lower()}.html"))
    links = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">'
        f'{html.escape(label)}</a>'
        for key, label, href in items)
    return (f'<aside class="sb"><div class="osp-logo"><span class="mk">◈</span>'
            f'<span class="osp-brand">360Five</span></div><nav>{links}</nav>'
            f'<div class="osp-acct"><span class="av">E</span>'
            f'<span class="nm">EdgeCash</span></div></aside>')


def _page(active: str, title: str, gen: str, body: str) -> str:
    return (
        f"<!doctype html><html data-theme=\"dark\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>360Five — {html.escape(title)}</title>"
        f"{theme.theme_css('dark')}<style>{SITE_CSS}</style></head><body>"
        f"<div class=\"site\">{_nav(active)}<main>"
        f"<div class=\"topbar\"><div class=\"osp-title\">{html.escape(title)}</div>"
        f"<input class=\"search\" placeholder=\"\U0001f50d  team or player…\"></div>"
        f"<div class=\"sub\">Updated {html.escape(gen)} ET · refreshes hourly · "
        f"not financial advice</div>{body}</main></div>"
        f"<script>{SEARCH_JS}</script></body></html>")


# --------------------------------------------------------------------------
# Content builders
# --------------------------------------------------------------------------

def _plays_board(day: dict) -> str:
    board = ui.build_best_bets(day, MIN_EDGE)
    if not board.empty:
        board = board[pd.to_numeric(board["ev"], errors="coerce") < 0.30]
    sports = [s for s in NAV_SPORTS if s in day]
    rows = ["<div class='pl-board'>",
            "<div class='pl-head'><span>Matchup</span><span>Play</span></div>"]
    for sport in sports:
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


def _sport_feed(sport: str, day: dict, date_sel: str) -> str:
    blob = day.get(sport, {})
    games = blob.get("games", []) or []
    if not games:
        return "<div class='feednote'>No games scheduled for this slate.</div>"
    gt = _gate_table()
    out = ["<div class='legend'>🟢 play · 🟡 lean · ⚪ pass — click a game for its "
           "full Sharp Sheet.</div>"]
    for g in games:
        try:
            label, auto = ui.sheet_headline(sport, g, min_edge=MIN_EDGE,
                                            gate_table=gt, bankroll=BANKROLL)
        except Exception:
            label, auto = f"{g.get('away_team','')} @ {g.get('home_team','')}", False
        search = _md_bold(label).lower()
        search = re.sub("<[^>]+>", "", search)
        try:
            if sport in MATCH_MODEL_SPORTS:
                body = _match_body(sport, g)
            else:
                m = _matchup(sport, g.get("home_team", ""), g.get("away_team", ""), date_sel)
                body = ui.sharp_sheet_html(sport, g, m, window="l5", min_edge=MIN_EDGE,
                                           gate_table=gt, bankroll=BANKROLL,
                                           props=blob.get("props") or [])
        except Exception as e:  # never let one game blank the feed
            body = f"<div class='feednote'>Sheet unavailable ({html.escape(str(e))}).</div>"
        out.append(
            f"<details class='game'{' open' if auto else ''} "
            f"data-search=\"{html.escape(search, quote=True)}\">"
            f"<summary>{_md_bold(label)}</summary>"
            f"<div class='ssbody'>{body}</div></details>")
    return "".join(out)


def _match_body(sport: str, g: dict) -> str:
    """Lightweight body for soccer/tennis (no team offense/defense splits)."""
    try:
        edges = ui.match_edge_table(sport, g)
    except Exception:
        edges = pd.DataFrame()
    parts = []
    if sport in ("ATP", "WTA"):
        parts.append(
            f"<p><strong>{html.escape(str(g.get('player1','P1')))}</strong> "
            f"{ui._pct(g.get('player1_win_prob'))} · "
            f"<strong>{html.escape(str(g.get('player2','P2')))}</strong> "
            f"{ui._pct(g.get('player2_win_prob'))}</p>")
    else:
        parts.append(
            f"<p>Home {ui._pct(g.get('home_win_prob'))} · Draw "
            f"{ui._pct(g.get('draw_prob'))} · Away {ui._pct(g.get('away_win_prob'))}</p>")
    if not edges.empty:
        parts.append(edges.to_html(index=False, border=0, classes="mtable"))
    else:
        parts.append("<div class='feednote'>No market prices yet for this match.</div>")
    return "".join(parts)


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

    OUT.mkdir(exist_ok=True)
    pages = 0

    plays_html = _page("plays", "Plays", gen, _plays_board(day))
    (OUT / "plays.html").write_text(plays_html)
    (OUT / "index.html").write_text(plays_html)
    pages += 1

    for sport in NAV_SPORTS:
        if sport not in day:
            continue
        body = _sport_feed(sport, day, date_sel)
        (OUT / f"{sport.lower()}.html").write_text(
            _page(sport.lower(), SPORT_LABELS.get(sport, sport), gen, body))
        pages += 1

    print(f"built {pages} pages → {OUT}  (slate {date_sel})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
