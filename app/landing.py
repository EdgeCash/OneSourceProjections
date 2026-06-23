"""Public landing page — the storefront a visitor sees *before* the password
gate. It shows the brand, the sports covered, honest forward-test credibility
stats (Brier, CLV, ROI — the proof, not the picks), and a deliberately
**redacted** teaser of today's board: how many edges the model found and on
which sports, with the actual bets blurred behind the sign-in.

The picks are the product, so nothing here leaks a team, player, line, or a
precise EV. The presentation helpers (``headline_stats``, ``teaser_counts``,
``redacted_rows``) are pure functions over the ``latest.json`` shapes so they
can be unit-tested without Streamlit, mirroring ``app/ui.py``.

Flow (driven by ``gate``):  landing  →  password (``auth.login_form``)  →  app.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app import auth, ui
from onesource import config

# Sports we advertise on the front, in marquee order.
_SPORTS = ["MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL"]


# ---------------------------------------------------------------------------
# Pure helpers (testable without Streamlit)
# ---------------------------------------------------------------------------

def headline_stats(perf: dict | None) -> list[dict]:
    """Four credibility tiles drawn from the live performance summary. These
    are the honest, public-safe proof points — model calibration and
    closing-line value — never the edges themselves. Missing fields degrade
    to an em-dash so the front never looks broken on a cold start."""
    overall = (perf or {}).get("overall", {}) or {}

    def num(v):
        return v is not None and not (isinstance(v, float) and pd.isna(v))

    gg = overall.get("graded_games")
    brier = overall.get("model_brier")
    beat = overall.get("clv_beat_rate")
    clv_bets = overall.get("clv_bets", 0) or 0
    roi = overall.get("roi_pct")
    bets = overall.get("bets", 0) or 0
    return [
        {"label": "Games forward-tested",
         "value": f"{int(gg):,}" if num(gg) else "—",
         "sub": "graded against final scores"},
        {"label": "Model Brier",
         "value": f"{brier:.3f}" if num(brier) else "—",
         "sub": "win-prob error · 0.25 = coin flip"},
        {"label": "Beat the close",
         "value": f"{beat * 100:.0f}%" if num(beat) else "—",
         "sub": f"{int(clv_bets):,} bets vs. closing line"},
        {"label": "Forward-test ROI",
         "value": f"{roi:+.1f}%" if num(roi) else "—",
         "sub": f"{int(bets):,} graded bets · ¼-Kelly"},
    ]


def teaser_counts(day_slates: dict | None, min_edge: float) -> dict:
    """Aggregate, leak-free summary of today's board: how many edges cleared
    the bar, the games/props split, which sports are live, and a *banded*
    best-EV (e.g. '10%+') so the magnitude shows without the number."""
    board = ui.build_best_bets(day_slates or {}, min_edge)
    if board.empty:
        return {"total": 0, "games": 0, "props": 0, "sports": [], "best_band": None}
    ev = pd.to_numeric(board["ev"], errors="coerce")
    best = float(ev.max()) if ev.notna().any() else 0.0
    band = "20%+" if best >= 0.20 else "10%+" if best >= 0.10 else "5%+"
    return {
        "total": int(len(board)),
        "games": int((board["type"] == "Game").sum()),
        "props": int((board["type"] == "Prop").sum()),
        "sports": [s for s in _SPORTS if s in set(board["sport"])],
        "best_band": band,
    }


def redacted_rows(day_slates: dict | None, min_edge: float, n: int = 6) -> list[dict]:
    """Top-EV rows with everything identifying stripped out: sport and market
    *type* survive (MLB · Total), the bet and EV become blocks. Enough to feel
    the board breathing; nothing a visitor could act on."""
    board = ui.build_best_bets(day_slates or {}, min_edge)
    rows = []
    for _, r in board.head(n).iterrows():
        rows.append({
            "sport": str(r.get("sport", "")),
            "kind": str(r.get("type", "")),
            "market": str(r.get("market", "")),
        })
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_LANDING_CSS = """
<style>
  /* full-width storefront: hide the app chrome until the visitor signs in */
  section[data-testid="stSidebar"] { display: none; }
  .osp-land { max-width: 1040px; margin: 0 auto; }
  .osp-hero-wrap { text-align:center; padding: 18px 0 6px; }
  .osp-logo { font-family: var(--disp, 'Space Grotesk', sans-serif);
    font-size: clamp(2.4rem, 7vw, 3.6rem); font-weight: 700; letter-spacing: -1px;
    margin: 0; background: linear-gradient(90deg,#00e676,#22d3ee);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent; }
  .osp-tag { font-family: var(--disp, 'Space Grotesk', sans-serif);
    font-size: clamp(1.05rem, 3.2vw, 1.5rem); font-weight: 600; color:#e6edf3;
    margin: 6px 0 2px; letter-spacing:-0.3px; }
  .osp-sub { color:#8b949e; font-size: 0.95rem; max-width: 620px;
    margin: 6px auto 0; line-height: 1.5; }
  .osp-chips { text-align:center; margin: 16px 0 8px; }
  .osp-chip { display:inline-block; font-family: var(--disp,'Space Grotesk',sans-serif);
    font-weight:600; font-size:0.82rem; color:#c9d1d9; padding:5px 13px; margin:4px;
    border-radius:999px; border:1px solid #1e2636; background:#0e131d; }
  .osp-grid { display:grid; grid-template-columns: repeat(4, 1fr); gap:12px;
    margin: 20px 0 6px; }
  @media (max-width: 720px){ .osp-grid{ grid-template-columns: repeat(2,1fr); } }
  .osp-stat { position:relative; overflow:hidden; border:1px solid #1e2636;
    border-radius:14px; padding:16px 16px 13px 18px; background:
    linear-gradient(160deg, rgba(0,230,118,0.08), rgba(34,211,238,0.04)), #121826; }
  .osp-stat::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px;
    background: linear-gradient(180deg,#00e676,#22d3ee); }
  .osp-stat .v { font-family: var(--disp,'Space Grotesk',sans-serif); font-weight:700;
    font-size:1.8rem; letter-spacing:-1px; color:#fff; }
  .osp-stat .l { text-transform:uppercase; letter-spacing:0.6px; font-size:0.7rem;
    font-weight:600; color:#8b949e; margin-top:2px; }
  .osp-stat .s { color:#6e7781; font-size:0.72rem; margin-top:5px; }
  .osp-board { border:1px solid #1e2636; border-radius:16px; background:#0e131d;
    padding:16px 18px; margin: 18px 0 6px; }
  .osp-board-h { display:flex; justify-content:space-between; align-items:center;
    flex-wrap:wrap; gap:8px; margin-bottom:10px; }
  .osp-board-t { font-family: var(--disp,'Space Grotesk',sans-serif); font-weight:700;
    font-size:1.05rem; color:#e6edf3; }
  .osp-live { color:#00e676; font-size:0.74rem; font-weight:700; }
  .osp-live .dot { animation: osppulse 1.8s ease-in-out infinite; }
  @keyframes osppulse { 0%,100%{opacity:1;} 50%{opacity:0.35;} }
  .osp-row { display:flex; align-items:center; gap:12px; padding:9px 6px;
    border-top:1px solid #161d29; font-size:0.9rem; }
  .osp-row .sp { font-family:var(--disp,'Space Grotesk',sans-serif); font-weight:700;
    font-size:0.72rem; color:#22d3ee; width:54px; }
  .osp-row .mk { color:#8b949e; font-size:0.78rem; width:96px; }
  .osp-blur { flex:1; letter-spacing:2px; color:#39414d;
    background:linear-gradient(90deg,#1b2230,#222b3c,#1b2230); border-radius:6px;
    padding:2px 8px; filter: blur(0.6px); user-select:none; }
  .osp-row .ev { color:#39414d; font-weight:700; }
  .osp-lockline { text-align:center; color:#8b949e; font-size:0.82rem;
    margin-top:12px; }
  .osp-how { display:grid; grid-template-columns: repeat(3,1fr); gap:12px; margin:20px 0; }
  @media (max-width: 720px){ .osp-how{ grid-template-columns: 1fr; } }
  .osp-step { border:1px solid #1e2636; border-radius:12px; padding:13px 15px;
    background:#0e131d; }
  .osp-step .n { color:#00e676; font-weight:800; font-family:var(--disp,'Space Grotesk',sans-serif); }
  .osp-step .h { font-weight:600; color:#e6edf3; margin:2px 0 3px; }
  .osp-step .b { color:#8b949e; font-size:0.8rem; line-height:1.45; }
  .osp-foot { text-align:center; color:#6e7781; font-size:0.74rem; margin-top:18px;
    line-height:1.6; }
</style>
"""


def _stat_tiles_html(stats: list[dict]) -> str:
    cells = "".join(
        f"<div class='osp-stat'><div class='v'>{s['value']}</div>"
        f"<div class='l'>{s['label']}</div><div class='s'>{s['sub']}</div></div>"
        for s in stats)
    return f"<div class='osp-grid'>{cells}</div>"


def _board_html(counts: dict, rows: list[dict]) -> str:
    if not counts["total"]:
        body = ("<div class='osp-lockline'>No edges clear the bar on the live "
                "slate right now — the board refreshes hourly.</div>")
        sub = "waiting on lines"
    else:
        sports = " · ".join(counts["sports"]) or "—"
        best = counts["best_band"]
        sub = (f"{counts['total']} edges · {counts['games']} games / "
               f"{counts['props']} props · {sports}")
        row_html = "".join(
            f"<div class='osp-row'><span class='sp'>{r['sport']}</span>"
            f"<span class='mk'>{r['market']}</span>"
            f"<span class='osp-blur'>▓▓▓▓▓▓▓▓▓▓▓▓</span>"
            f"<span class='ev'>+▓.▓%</span></div>"
            for r in rows)
        lock = (f"<div class='osp-lockline'>🔒 + {max(0, counts['total'] - len(rows))} "
                f"more — best edge today lands in the <b>{best} EV</b> band. "
                "Sign in to see the teams, lines, and prices.</div>")
        body = row_html + lock
    return (
        "<div class='osp-board'>"
        "<div class='osp-board-h'>"
        "<span class='osp-board-t'>Today's board</span>"
        f"<span class='osp-live'><span class='dot'>●</span> LIVE · {sub}</span>"
        "</div>" + body + "</div>"
    )


def _how_html() -> str:
    steps = [
        ("1", "Model the game", "Per-sport models (MLB Monte-Carlo with Statcast "
         "+ park factors; Elo-primed engines elsewhere) produce a probability "
         "for every market."),
        ("2", "Price the market", "Strip the vig from the best available book "
         "lines to get a fair price, then measure the edge against it — and "
         "against the de-vigged consensus of 15+ books."),
        ("3", "Size & forward-test", "Positive-EV spots get a ¼-Kelly stake, "
         "then every pick is graded against the closing line and the final "
         "score. The track record is the product."),
    ]
    cells = "".join(
        f"<div class='osp-step'><span class='n'>{n}</span>"
        f"<div class='h'>{h}</div><div class='b'>{b}</div></div>"
        for n, h, b in steps)
    return f"<div class='osp-how'>{cells}</div>"


def render_landing(data: dict | None) -> None:
    """Paint the full public front, then expose the single CTA into the gate."""
    st.markdown(_LANDING_CSS, unsafe_allow_html=True)

    slates = {}
    if data:
        slates = data.get("slates") or (
            {data.get("date", "latest"): data["sports"]} if "sports" in data else {})
    primary = (data or {}).get("primary_date")
    if primary not in slates:
        primary = next(iter(slates), None)
    day = slates.get(primary, {}) if primary else {}

    stats = headline_stats((data or {}).get("performance"))
    counts = teaser_counts(day, config.MIN_EDGE)
    rows = redacted_rows(day, config.MIN_EDGE)

    chips = "".join(f"<span class='osp-chip'>{s}</span>" for s in _SPORTS)
    gen = str((data or {}).get("generated_at", ""))[:16].replace("T", " ")
    updated = f" · updated {gen} ET" if gen else ""

    st.markdown(
        "<div class='osp-land'>"
        "<div class='osp-hero-wrap'>"
        "<div class='osp-logo'>🎯 OneSource</div>"
        "<div class='osp-tag'>The multi-sport model that beats the close.</div>"
        "<div class='osp-sub'>Game and player-prop projections across six "
        "sports, priced against the market and forward-tested every hour. "
        "Edges, not vibes.</div>"
        f"</div><div class='osp-chips'>{chips}</div>"
        + _stat_tiles_html(stats)
        + _board_html(counts, rows)
        + _how_html()
        + "</div>",
        unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1, 1.1, 1])
    with c2:
        if st.button("Enter the model  →", width="stretch", type="primary"):
            st.session_state["osp_show_login"] = True
            st.rerun()

    st.markdown(
        "<div class='osp-foot'>For research and entertainment only — "
        "<b>not financial advice</b>. Model estimates carry no guarantee. "
        "21+. If gambling stops being fun, call 1-800-GAMBLER."
        f"{updated}</div>",
        unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Gate orchestration:  landing  →  password  →  app
# ---------------------------------------------------------------------------

def gate(data: dict | None) -> None:
    """Public-front gate. Returns (lets the app render) only when the visitor
    is authenticated; otherwise shows the landing page, or — once they click
    *Enter* — the password form, and stops the script."""
    if auth.is_authenticated():
        return

    if st.session_state.get("osp_show_login"):
        if st.button("←  Back to overview"):
            st.session_state["osp_show_login"] = False
            st.rerun()
        auth.login_form()  # st.stop()s until the password is correct
        return

    render_landing(data)
    st.stop()
