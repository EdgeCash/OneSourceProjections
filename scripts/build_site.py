#!/usr/bin/env python3
"""Build the Edge Card static site into ``docs/`` from ``data/output/latest.json``.

Pure generator: loads the (NaN-containing) JSON with Python, reuses the
Streamlit-free decision layer, and writes hand-designed HTML. Run by the hourly
GitHub Action after the data refresh; also runnable locally for previews.

    python scripts/build_site.py [--out docs] [--data data/output/latest.json]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ui                       # noqa: E402
from web import data as D, render as R   # noqa: E402

# sports we render (team sports use the market-call path; match sports the
# model-prob path). Order = display order on the Today board.
TEAM_SPORTS = ["MLB", "WNBA", "NBA", "NHL", "NFL", "NCAAF"]
MATCH_SPORTS = ["ATP", "WTA", "MLS", "EPL"]
MIN_EDGE = 0.02
BANKROLL = 1000.0


def _headline(sport, g, gate):
    try:
        return ui.sheet_headline(sport, g, min_edge=MIN_EDGE,
                                 gate_table=gate, bankroll=BANKROLL)
    except Exception:
        return (R.matchup_title(sport, g), False)


def build(data_path: Path, out: Path) -> dict:
    blob = D.load_latest(str(data_path))
    dates = blob.get("dates") or []
    primary = blob.get("primary_date") or (dates[0] if dates else None)
    slates = blob.get("slates") or {}
    gate = D.gate_table()
    updated = str(blob.get("generated_at", ""))[:16].replace("T", " ") + " ET"
    credits = blob.get("odds_api_credits")

    out.mkdir(parents=True, exist_ok=True)
    # assets
    assets_src = ROOT / "web" / "assets"
    assets_dst = out / "assets"
    if assets_dst.exists():
        shutil.rmtree(assets_dst)
    shutil.copytree(assets_src, assets_dst)

    written = {"pages": 0, "cards": 0}
    slate = slates.get(primary, {}) if primary else {}

    # --- per-sport slate pages + edge cards ---
    top_plays = []   # (ev, sport, g, headline_label, href)
    for sport in TEAM_SPORTS + MATCH_SPORTS:
        sblob = slate.get(sport) or {}
        games = sblob.get("games") or []
        err = sblob.get("error")
        props = sblob.get("props") or []
        rows = []
        for g in games:
            hl = _headline(sport, g, gate)
            rows.append(R.slate_row(sport, g, hl))
            # standalone edge card page
            mu = ({} if R.is_match_sport(sport)
                  else D.matchup(sport, g.get("home_team", ""),
                                 g.get("away_team", ""), primary))
            sdata = ({} if R.is_match_sport(sport)
                     else D.sheet_data(sport, g, mu, primary))
            bl = D.best_line_for(sport, g, primary) if not R.is_match_sport(sport) else {}
            body = R.edge_card(sport, g, mu, sdata, bl, min_edge=MIN_EDGE,
                               gate_table=gate, bankroll=BANKROLL, props=props)
            title = f"{R.matchup_title(sport, g)} — Edge Card"
            (out / R.card_href(sport, g)).write_text(
                R.page(title, f"<a class='back' href='{sport.lower()}.html'>← {sport}</a>{body}",
                       active=sport, updated=updated))
            written["cards"] += 1
            # collect top plays
            _collect_top_plays(sport, g, gate, props, top_plays)

        # slate page
        if err:
            inner = f"<div class='notice'>⚠️ {R.esc(sport)} unavailable — {R.esc(err)}</div>"
        elif not rows:
            inner = f"<div class='notice'>No {R.esc(sport)} games for {R.esc(primary)}.</div>"
        else:
            inner = (f"<div class='legend'>🟢 play · 🟡 lean · 🟠 verify · ⚪ pass — "
                     f"tap a game for its Edge Card.</div><div class='slate'>{''.join(rows)}</div>")
        (out / f"{sport.lower()}.html").write_text(
            R.page(f"{sport} — {R.esc(primary or '')}", inner, active=sport,
                   subtitle=f"{sport} slate · {primary or ''}", updated=updated))
        written["pages"] += 1

    # --- Today board ---
    (out / "index.html").write_text(_today_page(primary, updated, credits, top_plays))
    written["pages"] += 1
    # --- props + performance ---
    (out / "props.html").write_text(_props_page(slate, primary, updated))
    (out / "performance.html").write_text(_performance_page(blob.get("performance") or {}, updated))
    written["pages"] += 2
    return written


def _collect_top_plays(sport, g, gate, props, sink):
    if R.is_match_sport(sport):
        return
    try:
        calls = ui._mc_market_calls(sport, g, MIN_EDGE, gate_table=gate, bankroll=BANKROLL)
    except Exception:
        return
    for c in calls:
        ev = c.get("ev")
        if c.get("decision") in ("PLAY", "LEAN") and isinstance(ev, (int, float)):
            sink.append((ev, sport, g, c))


def _today_page(primary, updated, credits, top_plays):
    top_plays.sort(key=lambda t: -t[0])
    cards = []
    for ev, sport, g, c in top_plays[:12]:
        _, tone = R._DEC.get(c.get("decision"), ("", "pass"))
        cards.append(
            f"<a class='play play-{tone}' href='{R.card_href(sport, g)}'>"
            f"<div class='play-top'><span class='play-sport'>{R.esc(sport)}</span>"
            f"{R.decision_pill(c.get('decision'))}</div>"
            f"<div class='play-mk'>{R.esc(c.get('label',''))} · {R.esc(c.get('pick',''))}</div>"
            f"<div class='play-game'>{R.esc(R.matchup_title(sport, g))}</div>"
            f"<div class='play-ev'>{ev*100:+.1f}% edge"
            f"{(' · '+format(c['stake_units'],'g')+'u') if c.get('stake_units') else ''}</div></a>")
    if not cards:
        board = "<div class='notice'>No qualifying plays on the board yet — edges firm up as lineups and odds post.</div>"
    else:
        board = f"<div class='plays'>{''.join(cards)}</div>"
    cred = f" · Odds API {int(credits):,} credits" if credits is not None else ""
    body = (f"<section class='sec'><h1 class='today-h'>Today's Edge</h1>"
            f"<div class='today-sub'>{R.esc(primary or '')}{cred}</div>{board}</section>")
    return R.page("Today's Edge — Project 54.7", body, active="today", updated=updated)


def _props_page(slate, primary, updated):
    props = (slate.get("MLB") or {}).get("props") or []
    if not props:
        inner = "<div class='notice'>No priced props yet for this slate.</div>"
    else:
        rows = []
        for p in props[:60]:
            rows.append(
                f"<tr><td>{R.esc(p.get('player',''))}</td><td>{R.esc(p.get('market',''))}</td>"
                f"<td>{R._num(p.get('projection'))}</td>"
                f"<td>{R.fmt_american(p.get('odds')) or R._gap('unpriced')}</td></tr>")
        inner = (f"<div class='legend'>MLB pitcher props · model projection vs the book.</div>"
                 f"<table class='ptab'><tr><th>Player</th><th>Market</th><th>Proj</th><th>Line</th></tr>"
                 f"{''.join(rows)}</table>")
    return R.page("Props — Project 54.7", inner, active="props",
                  subtitle=f"Props · {primary or ''}", updated=updated)


def _performance_page(perf, updated):
    ov = perf.get("overall") or {}
    def stat(k, fmt="{}"):
        v = ov.get(k)
        return fmt.format(v) if v is not None else "—"
    tiles = [
        ("Graded games", stat("graded_games")),
        ("Bets", stat("bets")),
        ("Win rate", (f"{ov.get('bet_win_rate')*100:.1f}%" if ov.get('bet_win_rate') is not None else "—")),
        ("Units", stat("units", "{:+.2f}")),
        ("ROI", (f"{ov.get('roi_pct'):+.2f}%" if ov.get('roi_pct') is not None else "—")),
        ("Avg CLV", (f"{ov.get('avg_clv_pct'):+.2f}%" if ov.get('avg_clv_pct') is not None else "—")),
    ]
    tilehtml = "".join(f"<div class='tile'><div class='tile-v'>{R.esc(v)}</div>"
                       f"<div class='tile-k'>{R.esc(k)}</div></div>" for k, v in tiles)
    by = perf.get("by_sport") or {}

    def _row(s, d):
        roi = ("%+.2f%%" % d["roi_pct"]) if d.get("roi_pct") is not None else "—"
        clv = ("%+.2f%%" % d["avg_clv_pct"]) if d.get("avg_clv_pct") is not None else "—"
        return (f"<tr><td>{R.esc(s)}</td><td>{d.get('bets', '—')}</td>"
                f"<td>{roi}</td><td>{clv}</td></tr>")
    rows = "".join(_row(s, d) for s, d in by.items())
    bytab = (f"<table class='ptab'><tr><th>Sport</th><th>Bets</th><th>ROI</th>"
             f"<th>Avg CLV</th></tr>{rows}</table>" if rows else "")
    body = (f"<section class='sec'><h1 class='today-h'>Track Record</h1>"
            f"<div class='tiles'>{tilehtml}</div>"
            f"<h2 class='sec-h'>By sport</h2>{bytab}</section>")
    return R.page("Track Record — Project 54.7", body, active="performance", updated=updated)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data/output/latest.json"))
    ap.add_argument("--out", default=str(ROOT / "docs"))
    args = ap.parse_args()
    w = build(Path(args.data), Path(args.out))
    print(f"built {w['pages']} pages + {w['cards']} edge cards → {args.out}")


if __name__ == "__main__":
    main()
