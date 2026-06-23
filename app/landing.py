"""Public landing page — the cold open. The storefront a visitor sees before
the password gate, staged as a Law & Order title sequence: a black intertitle
card, the "two separate yet equally important groups" narration (parodied),
the *DUN-DUN*, an honest **conviction record**, and a deliberately **sealed**
preview of today's docket — counts and blurred charges, never an actual pick.

The picks are the product, so nothing here leaks a team, player, line, or a
precise EV. The presentation helpers (``headline_stats``, ``teaser_counts``,
``redacted_rows``) are pure functions over the ``latest.json`` shapes so they
can be unit-tested without Streamlit.

Flow (driven by ``gate``):  landing → password (``auth.login_form``) → app.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app import auth, conviction, theme, ui
from onesource import config

# Sports we advertise on the front (the "jurisdictions"), in marquee order.
_SPORTS = ["MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL"]

# The franchise narration, retried for a betting model: the two separate yet
# equally important groups are the model (the People) and the market (the price).
_NARRATION = (
    "In the sports-wagering system, the bettor is protected by two separate "
    "yet equally important groups: <b>the model</b>, which weighs the evidence, "
    "and <b>the market</b>, which sets the price. These are their stories."
)


# ---------------------------------------------------------------------------
# Pure helpers (testable without Streamlit)
# ---------------------------------------------------------------------------

def headline_stats(perf: dict | None) -> list[dict]:
    """Four credibility tiles — the *conviction record* — drawn from the live
    performance summary. Honest, public-safe proof (calibration + closing-line
    value), never the edges. Missing fields degrade to an em-dash."""
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
        {"label": "Cases tried",
         "value": f"{int(gg):,}" if num(gg) else "—",
         "sub": "games graded against the final whistle"},
        {"label": "Calibration",
         "value": f"{brier:.3f}" if num(brier) else "—",
         "sub": "Brier score · 0.25 = a coin toss"},
        {"label": "Beat the close",
         "value": f"{beat * 100:.0f}%" if num(beat) else "—",
         "sub": f"{int(clv_bets):,} verdicts vs. the closing line"},
        {"label": "Conviction ROI",
         "value": f"{roi:+.1f}%" if num(roi) else "—",
         "sub": f"{int(bets):,} graded bets · ¼-Kelly"},
    ]


def teaser_counts(day_slates: dict | None, min_edge: float) -> dict:
    """Aggregate, leak-free summary of today's docket: how many charges cleared
    the bar, the games/props split, the jurisdictions in session, and a
    *banded* best-EV (e.g. '10%+') so the magnitude shows without the number."""
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
    """Top-EV charges with everything identifying stripped out: sport and market
    *type* survive (MLB · Total); the bet and EV are sealed. Enough to feel the
    docket breathing; nothing a visitor could act on."""
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
  /* the cold-open intertitle */
  .osp-coldopen { background:#000; border:1px solid var(--line);
    border-top:3px solid var(--elec); border-bottom:3px solid var(--vivid);
    border-radius:6px; padding:30px 24px 26px; text-align:center;
    box-shadow: 0 0 0 1px rgba(var(--acc2-rgb),0.18), 0 18px 50px rgba(0,0,0,0.6);
    animation: ospdun 0.8s ease-out; }
  .osp-eyebrow { font-family:var(--mono); font-size:0.72rem; letter-spacing:5px;
    text-transform:uppercase; color:var(--acc); margin-bottom:10px; }
  .osp-logo { font-family:var(--disp); font-weight:700;
    font-size: clamp(2.6rem, 8vw, 4.2rem); letter-spacing:3px; text-transform:uppercase;
    color:#fff; text-shadow: 0 0 22px rgba(var(--acc2-rgb),0.55); margin:0; }
  .osp-logo .amp { color:var(--acc); }
  .osp-est { font-family:var(--mono); font-size:0.8rem; letter-spacing:3px;
    color:var(--text2); margin-top:6px; }
  .osp-narr { font-family:var(--disp); font-style:italic; font-weight:500;
    color:var(--text2); max-width:760px; margin:18px auto 4px; line-height:1.65;
    font-size: clamp(0.95rem, 2.4vw, 1.12rem); }
  .osp-narr b { color:var(--acc); font-style:normal; }
  .osp-chips { text-align:center; margin: 18px 0 4px; }
  .osp-chips .lbl { font-family:var(--mono); font-size:0.66rem; letter-spacing:3px;
    color:var(--faint); text-transform:uppercase; display:block; margin-bottom:8px; }
  .osp-chip { display:inline-block; font-family:var(--mono); font-weight:700;
    font-size:0.78rem; color:var(--text2); padding:5px 13px; margin:4px;
    border-radius:4px; border:1px solid var(--line); background:var(--panel); }
  .osp-sec { font-family:var(--mono); font-size:0.68rem; letter-spacing:4px;
    text-transform:uppercase; color:var(--acc); text-align:center;
    margin: 26px 0 12px; }
  .osp-grid { display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; }
  @media (max-width: 720px){ .osp-grid{ grid-template-columns: repeat(2,1fr); } }
  .osp-stat { position:relative; overflow:hidden; border:1px solid var(--line);
    border-radius:6px; padding:16px 16px 13px 18px; background:
    linear-gradient(160deg, rgba(var(--acc-rgb),0.07), rgba(var(--acc2-rgb),0.04)), var(--card); }
  .osp-stat::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px;
    background: linear-gradient(180deg,var(--acc),var(--acc2)); }
  .osp-stat .v { font-family:var(--disp); font-weight:700; font-size:1.8rem;
    color:var(--text); }
  .osp-stat .l { text-transform:uppercase; letter-spacing:0.7px; font-size:0.7rem;
    font-weight:700; color:var(--muted); margin-top:2px; font-family:var(--mono); }
  .osp-stat .s { color:var(--faint); font-size:0.72rem; margin-top:5px; }
  .osp-board { border:1px solid var(--line); border-radius:8px; background:var(--panel);
    padding:16px 18px; }
  .osp-board-h { display:flex; justify-content:space-between; align-items:center;
    flex-wrap:wrap; gap:8px; margin-bottom:10px; }
  .osp-board-t { font-family:var(--disp); font-weight:700; font-size:1.05rem;
    letter-spacing:1px; text-transform:uppercase; color:var(--text); }
  .osp-live { color:var(--acc); font-size:0.72rem; font-weight:700; font-family:var(--mono);
    letter-spacing:1px; }
  .osp-live .dot { animation: osppulse 1.8s ease-in-out infinite; }
  @keyframes osppulse { 0%,100%{opacity:1;} 50%{opacity:0.35;} }
  .osp-row { display:flex; align-items:center; gap:12px; padding:9px 6px;
    border-top:1px solid var(--line3); font-size:0.9rem; }
  .osp-row .sp { font-family:var(--mono); font-weight:700; font-size:0.72rem;
    color:var(--acc2); width:54px; }
  .osp-row .mk { color:var(--muted); font-size:0.78rem; width:96px; font-family:var(--mono); }
  .osp-blur { flex:1; letter-spacing:2px; color:var(--faint2);
    background:linear-gradient(90deg,var(--blur1),var(--blur2),var(--blur1));
    border-radius:4px; padding:2px 8px; filter: blur(0.6px); user-select:none;
    font-family:var(--mono); }
  .osp-row .seal { font-family:var(--disp); font-weight:700; color:var(--neg);
    font-size:0.7rem; letter-spacing:1px; }
  .osp-row .ev { color:var(--faint2); font-weight:700; font-family:var(--mono); }
  .osp-lockline { text-align:center; color:var(--muted); font-size:0.82rem;
    margin-top:12px; font-family:var(--mono); }
  .osp-how { display:grid; grid-template-columns: repeat(3,1fr); gap:12px; }
  @media (max-width: 720px){ .osp-how{ grid-template-columns: 1fr; } }
  .osp-step { border:1px solid var(--line); border-radius:6px; padding:13px 15px;
    background:var(--panel); }
  .osp-step .n { color:var(--acc); font-weight:800; font-family:var(--disp);
    font-size:1.1rem; letter-spacing:2px; }
  .osp-step .h { font-weight:700; color:var(--text); margin:2px 0 3px; font-family:var(--disp);
    text-transform:uppercase; letter-spacing:0.5px; font-size:0.92rem; }
  .osp-step .b { color:var(--muted); font-size:0.8rem; line-height:1.45; }
  .osp-trial-lede { text-align:center; color:var(--muted); font-size:0.9rem;
    max-width:620px; margin:0 auto 6px; line-height:1.5; }
  .osp-trial-lede b { color:var(--acc); }
  .osp-trial-out { border:1px solid var(--line); border-top:3px solid var(--elec);
    border-bottom:3px solid var(--vivid); border-radius:8px; background:var(--panel);
    padding:18px 22px; margin-top:10px; animation: ospdun 0.7s ease-out; }
  .osp-trial-out .cap { font-family:var(--disp); font-weight:700; font-size:1.05rem;
    letter-spacing:0.5px; text-transform:uppercase; color:var(--text); text-align:center; }
  .osp-trial-out .ring-wrap { text-align:center; margin:14px 0 6px; }
  .osp-trial-out .ring { width:150px; height:150px; border-radius:50%; margin:0 auto;
    display:flex; align-items:center; justify-content:center; }
  .osp-trial-out .hole { width:118px; height:118px; border-radius:50%;
    background:var(--bg); display:flex; flex-direction:column; align-items:center;
    justify-content:center; }
  .osp-trial-out .pct { font-family:var(--mono); font-weight:700; font-size:2.3rem;
    line-height:1; }
  .osp-trial-out .cr { font-family:var(--mono); font-size:0.56rem; letter-spacing:2px;
    color:var(--muted); margin-top:4px; }
  .osp-trial-out .band { font-family:var(--disp); font-weight:700; letter-spacing:1px;
    text-transform:uppercase; font-size:0.9rem; margin-top:10px; }
  .osp-trial-out .ev-h { font-family:var(--mono); font-size:0.66rem; letter-spacing:3px;
    text-transform:uppercase; color:var(--acc); margin-top:6px; }
  .osp-trial-out ul.ev { list-style:none; padding:0; margin:6px 0; font-family:var(--mono);
    font-size:0.84rem; line-height:1.7; color:var(--text2); }
  .osp-trial-out ul.ev li::before { content:'▪ '; color:var(--acc2); }
  .osp-trial-out .disc { font-family:var(--mono); font-size:0.66rem; color:var(--faint);
    border-top:1px dashed var(--line); margin-top:10px; padding-top:8px; line-height:1.5; }
  .osp-firm { border:1px solid var(--line); border-radius:8px; background:var(--panel);
    padding:18px 22px; color:var(--text2); font-size:0.92rem; line-height:1.6; }
  .osp-firm .claim { font-family:var(--disp); font-weight:700; color:var(--acc);
    font-size:1.25rem; letter-spacing:0.5px; text-align:center; margin:0 0 10px; }
  .osp-firm .house { border-top:1px solid var(--line); margin-top:12px; padding-top:10px; }
  .osp-firm .house .hh { font-family:var(--mono); font-size:0.68rem; letter-spacing:3px;
    text-transform:uppercase; color:var(--muted); margin-bottom:4px; }
  .osp-firm .house b { color:var(--text); }
  .osp-foot { text-align:center; color:var(--faint); font-size:0.74rem; margin-top:18px;
    line-height:1.6; font-family:var(--mono); }
  .osp-signoff { text-align:center; font-family:var(--disp); color:var(--text2);
    letter-spacing:1px; margin:18px 0 2px; font-size:0.95rem; }
  .osp-motto { text-align:center; font-family:var(--disp); font-style:italic;
    color:var(--acc); font-size:0.82rem; letter-spacing:0.5px; }
</style>
"""


def _cold_open_html() -> str:
    return (
        "<div class='osp-coldopen'>"
        "<div class='osp-eyebrow'>Office of the Projections Attorney</div>"
        "<div class='osp-logo'>True<span class='amp'>·</span>Bill</div>"
        "<div class='osp-est'>SPECIAL WAGERS UNIT · EST. 2026</div>"
        f"<div class='osp-narr'>{_NARRATION}</div>"
        "</div>"
    )


def _stat_tiles_html(stats: list[dict]) -> str:
    cells = "".join(
        f"<div class='osp-stat'><div class='v'>{s['value']}</div>"
        f"<div class='l'>{s['label']}</div><div class='s'>{s['sub']}</div></div>"
        for s in stats)
    return f"<div class='osp-grid'>{cells}</div>"


def _board_html(counts: dict, rows: list[dict]) -> str:
    if not counts["total"]:
        body = ("<div class='osp-lockline'>No charges clear the bar on the live "
                "docket right now — the board is re-filed hourly.</div>")
        sub = "awaiting evidence"
    else:
        sports = " · ".join(counts["sports"]) or "—"
        best = counts["best_band"]
        sub = (f"{counts['total']} charges · {counts['games']} games / "
               f"{counts['props']} props · {sports}")
        row_html = "".join(
            f"<div class='osp-row'><span class='sp'>{r['sport']}</span>"
            f"<span class='mk'>{r['market']}</span>"
            f"<span class='osp-blur'>▓▓▓▓▓▓▓▓▓▓▓▓</span>"
            f"<span class='seal'>SEALED</span>"
            f"<span class='ev'>+▓.▓%</span></div>"
            for r in rows)
        lock = (f"<div class='osp-lockline'>🔒 + {max(0, counts['total'] - len(rows))} "
                f"more under seal — the lead charge today sits in the "
                f"<b>{best} EV</b> band. Sign in to unseal the teams, lines, "
                "and prices.</div>")
        body = row_html + lock
    return (
        "<div class='osp-board'>"
        "<div class='osp-board-h'>"
        "<span class='osp-board-t'>Today's Docket</span>"
        f"<span class='osp-live'><span class='dot'>●</span> IN SESSION · {sub}</span>"
        "</div>" + body + "</div>"
    )


def _firm_html() -> str:
    """The origin + the honest claim + House Rules (responsible gambling woven
    in as a virtue of the firm, not buried in a footer)."""
    return (
        "<div class='osp-firm'>"
        "<p class='claim'>“We lose. It's on the record.”</p>"
        "<p>True Bill started with one frustration: every \"expert\" sells "
        "winners and buries losers. So we built the opposite — a docket where "
        "every play is charged, timestamped, and entered into a public record "
        "<i>before</i> the game starts. We can't quietly delete the bad ones. "
        "That constraint is the whole product. We live or die on Closing Line "
        "Value, size in units at ¼-Kelly, and we will never tell you to bet "
        "the rent.</p>"
        "<div class='house'>"
        "<div class='hh'>The House Rules</div>"
        "<p>We present evidence; we do not promise verdicts — a strong case "
        "still loses. This is entertainment built on probabilities, not income. "
        "You set your own limits, and we respect them. If the game stops being "
        "a game, court is always in recess: <b>1-800-GAMBLER</b>, free and "
        "confidential, 21+.</p>"
        "</div></div>"
    )


def _how_html() -> str:
    steps = [
        ("I.", "Investigate", "The People (per-sport models — MLB Monte-Carlo "
         "with Statcast and park factors; Elo-primed engines elsewhere) build "
         "the case: a probability for every market."),
        ("II.", "Arraign", "Strip the vig from the best book lines to get a "
         "fair price, then weigh the model against the de-vigged consensus of "
         "15+ books. The charge stands only if it beats the number."),
        ("III.", "Verdict & record", "A standing charge gets a ¼-Kelly stake — "
         "then every verdict is graded against the closing line and the final "
         "score. The conviction record is the whole product."),
    ]
    cells = "".join(
        f"<div class='osp-step'><span class='n'>{n}</span>"
        f"<div class='h'>{h}</div><div class='b'>{b}</div></div>"
        for n, h, b in steps)
    return f"<div class='osp-how'>{cells}</div>"


def _band_color(b: str) -> str:
    # electric blue for a strong case, gold for the toss-up, bright red for doubt
    if b in ("Open-and-shut", "Strong case"):
        return "var(--elec)"
    if b == "Circumstantial":
        return "var(--acc)"
    return "var(--vivid)"


def _verdict_card_html(res: dict) -> str:
    """The returned verdict: a conviction-rate ring, the band, and the
    evidence summary — typewritten like a logged finding."""
    rate = res.get("rate")
    pct = max(0.0, min(100.0, rate * 100)) if isinstance(rate, (int, float)) else 0.0
    color = _band_color(res.get("band", ""))
    big = f"{pct:.0f}%" if res.get("ok") else "—"
    ev = "".join(f"<li>{b}</li>" for b in res.get("evidence", [])) or \
        "<li>The People can't price this one — try another charge.</li>"
    return (
        "<div class='osp-trial-out'>"
        f"<div class='cap'>⚖ The People v. {res.get('caption', 'your play')}</div>"
        "<div class='ring-wrap'>"
        f"<div class='ring' style='background:conic-gradient({color} {pct}%,"
        f"var(--line) {pct}% 100%);'><div class='hole'>"
        f"<div class='pct' style='color:{color};'>{big}</div>"
        "<div class='cr'>CONVICTION<br>RATE</div></div></div>"
        f"<div class='band' style='color:{color};'>— {res.get('band','')} —</div>"
        "</div>"
        f"<div class='ev-h'>The Evidence</div><ul class='ev'>{ev}</ul>"
        "<div class='disc'>Model estimate of how often this side cashes — not a "
        "guarantee. Even an open-and-shut case can be overturned on appeal. "
        "21+ · 1-800-GAMBLER.</div></div>"
    )


def render_trial(day: dict) -> None:
    """'Put it on trial' — dropdowns → a Conviction Rate. Pure lookups over the
    live slate; no model call, no API."""
    st.markdown("<div class='osp-land'><div class='osp-sec'>"
                "— Put Your Play On Trial —</div>"
                "<div class='osp-trial-lede'>Assemble a play. The People return a "
                "<b>Conviction Rate</b> — the model's read on how often it "
                "cashes — with the evidence.</div></div>", unsafe_allow_html=True)

    sport_opts = conviction.sports(day)
    if not sport_opts:
        st.info("Court is in recess — no cases on today's docket to try. "
                "Check back once lines post.")
        return

    res = None
    with st.container():
        c1, c2 = st.columns(2)
        sport = c1.selectbox("Jurisdiction", sport_opts, key="trial_sport")
        gms = conviction.games(day, sport)
        glabels = [conviction.game_label(g) for g in gms]
        prps = conviction.props(day, sport)

        # charge types available: the game markets + (props, when present)
        if glabels:
            case = c2.selectbox("The Case", glabels, key="trial_game")
            g = gms[glabels.index(case)]
        else:
            g = None
            c2.selectbox("The Case", ["— no games; props only —"], key="trial_game")

        c3, c4 = st.columns(2)
        charge_opts = (conviction.charges(g) if g else [])
        pool = conviction.game_props(prps, g) if (g and prps) else prps
        if pool:
            charge_opts = charge_opts + ["Player prop"]
        if not charge_opts:
            st.info("No priced charges for this case yet.")
            return
        charge = c3.selectbox("The Charge", charge_opts, key="trial_charge")

        if charge == "Player prop":
            players = conviction.prop_players(pool)
            player = c4.selectbox("The Accused", players, key="trial_player")
            mkts = conviction.player_markets(pool, player)
            c5, c6 = st.columns(2)
            mkt = c5.selectbox("The Count", mkts, key="trial_market",
                               format_func=ui.short_market)
            p = conviction.prop_row(pool, player, mkt)
            sd = conviction.prop_sides(p) if p else [("over", "Over")]
            side = c6.selectbox("The Side", [s[1] for s in sd], key="trial_pside")
            side_id = sd[[s[1] for s in sd].index(side)][0]
            if p:
                res = conviction.prop_verdict(p, side_id)
        else:
            sd = conviction.sides(g, charge)
            side = c4.selectbox("The Side", [s[1] for s in sd], key="trial_side")
            side_id = sd[[s[1] for s in sd].index(side)][0]
            res = conviction.game_verdict(g, charge, side_id)

    if st.button("⚖  Return the verdict", key="trial_submit", type="primary"):
        st.session_state["trial_open"] = True
    if st.session_state.get("trial_open") and res:
        st.markdown("<div class='osp-land'>" + _verdict_card_html(res) + "</div>",
                    unsafe_allow_html=True)


def render_landing(data: dict | None) -> None:
    """Paint the full cold open, then expose the single CTA into the gate."""
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
    updated = f" · case file updated {gen} ET" if gen else ""

    st.markdown("<div class='osp-land'>" + _cold_open_html() + "</div>",
                unsafe_allow_html=True)

    # the literal "dun-dun" — mutable, nothing autoplays
    c1, c2, c3 = st.columns([1, 1, 1])
    with c2:
        theme.dun_dun_component()

    st.markdown(
        "<div class='osp-land'>"
        f"<div class='osp-chips'><span class='lbl'>Jurisdictions</span>{chips}</div>"
        "<div class='osp-sec'>— The Conviction Record —</div>"
        + _stat_tiles_html(stats)
        + "<div class='osp-sec'>— In Session —</div>"
        + _board_html(counts, rows)
        + "</div>",
        unsafe_allow_html=True)

    # interactive: put your own play on trial
    render_trial(day)

    st.markdown(
        "<div class='osp-land'>"
        "<div class='osp-sec'>— Due Process —</div>"
        + _how_html()
        + "<div class='osp-sec'>— The Firm —</div>"
        + _firm_html()
        + "</div>",
        unsafe_allow_html=True)

    st.markdown(
        f"<div class='osp-land'><div class='osp-signoff'>{theme.SIGNOFF}</div>"
        f"<div class='osp-motto'>{theme.MOTTO}</div></div>",
        unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        if st.button("⚖️  ENTER THE COURTROOM  →", width="stretch", type="primary"):
            st.session_state["osp_show_login"] = True
            st.rerun()

    st.markdown(
        "<div class='osp-foot'>For research and entertainment only — "
        "<b>not financial advice</b>. The People's estimates carry no guarantee; "
        "even a strong case loses. 21+. If gambling stops being fun, call "
        f"1-800-GAMBLER.{updated}</div>",
        unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Gate orchestration:  landing  →  password  →  app
# ---------------------------------------------------------------------------

def gate(data: dict | None) -> None:
    """Public-front gate. Returns (lets the app render) only when the visitor
    is authenticated; otherwise shows the cold open, or — once they click
    *Enter the courtroom* — the password form, and stops the script."""
    if auth.is_authenticated():
        return

    if st.session_state.get("osp_show_login"):
        if st.button("←  Back to the cold open"):
            st.session_state["osp_show_login"] = False
            st.rerun()
        auth.login_form()  # st.stop()s until the password is correct
        return

    render_landing(data)
    st.stop()
