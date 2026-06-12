"""OneSource Projections — research dashboard.

Run locally:  streamlit run app/dashboard.py
Data source:  data/output/latest.json (rewritten hourly by the GitHub
Action). The sidebar "Refresh" re-runs projections live if API keys are set.

Layout: a left sport-nav sidebar, a top bar (section title + search), and a
main panel that shows per-sport game cards / props, the cross-sport PLAYS
board, or the PERFORMANCE tracker.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ui  # noqa: E402
from app.auth import require_password  # noqa: E402
from onesource import config, results  # noqa: E402
from onesource.sports import SPORTS  # noqa: E402

st.set_page_config(page_title="OneSource Projections", page_icon="🎯",
                   layout="wide", initial_sidebar_state="expanded")

require_password()

st.markdown("""
<style>
  .block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1300px; }
  section[data-testid="stSidebar"] { background: #0b0f16; border-right: 1px solid #1c2330; }
  section[data-testid="stSidebar"] .stRadio label { font-size: 0.98rem; padding: 2px 0; }
  [data-testid="stMetric"] {
    background: rgba(40,60,50,0.22); border: 1px solid rgba(80,160,120,0.22);
    border-radius: 10px; padding: 10px 14px;
  }
  [data-testid="stMetricLabel"] { opacity: 0.75; }
  .osp-brand { font-size: 1.35rem; font-weight: 800; letter-spacing: -0.5px;
    margin: 0 0 0.2rem 0; }
  .osp-title { font-size: 1.7rem; font-weight: 800; margin: 0; }
  div[data-testid="stCaptionContainer"] { opacity: 0.65; }
  .stTabs [data-baseweb="tab"] { font-size: 0.95rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def load_data() -> dict | None:
    path = config.OUTPUT_DIR / "latest.json"
    return json.loads(path.read_text()) if path.exists() else None


@st.cache_data(ttl=300)
def load_ledger() -> list[dict]:
    return results.load_ledger()


def refresh():
    from datetime import date, timedelta

    from onesource import pipeline

    today, tomorrow = date.today().isoformat(), (date.today() + timedelta(1)).isoformat()
    with st.spinner("Re-running projections (a couple of minutes)..."):
        slates = {}
        for d in (today, tomorrow):
            slates[d] = pipeline.run(d, write=False)["sports"]
            results.archive_projections(d, slates[d])
        out = {"generated_at": pd.Timestamp.utcnow().isoformat(),
               "primary_date": tomorrow, "dates": [today, tomorrow],
               "slates": slates, "performance": results.performance()}
        (config.OUTPUT_DIR / "latest.json").write_text(json.dumps(out, default=str))
    load_data.clear()
    load_ledger.clear()


def slates_by_date(data: dict) -> dict:
    if "slates" in data:
        return data["slates"]
    if "sports" in data:
        return {data.get("date", "latest"): data["sports"]}
    return {}


def ev_styler(df: pd.DataFrame, ev_cols: list[str]):
    def color(v):
        if not isinstance(v, (int, float)) or pd.isna(v):
            return ""
        if v >= min_edge * 100:
            return "background-color: rgba(34,139,84,0.35); font-weight:600;"
        if v < 0:
            return "color: rgba(255,120,120,0.85);"
        return ""
    fmt = {c: "{:+.1f}%" for c in ev_cols if c in df.columns}
    fmt.update({c: "{:.0f}%" for c in ui.PCT_COLS if c in df.columns})
    fmt.update({c: "{:.2f}" for c in ("Proj", "FP Proj", "BP Proj", "Away Proj",
                                      "Home Proj", "Proj Total", "Kelly")
                if c in df.columns})
    return df.style.map(color, subset=[c for c in ev_cols if c in df.columns]) \
                   .format(fmt, na_rep="—")


# ---------------------------------------------------------------------------
# Sidebar: brand, nav, settings
# ---------------------------------------------------------------------------

data = load_data()
slates = slates_by_date(data) if data else {}

NAV_SPORTS = [s for s in ("MLB", "WNBA", "NBA", "NHL", "NCAAF") if s in SPORTS]

with st.sidebar:
    st.markdown("<div class='osp-brand'>🎯 OneSource</div>", unsafe_allow_html=True)
    st.caption("projections & research")
    section = st.radio("Navigate", NAV_SPORTS + ["PLAYS", "PERFORMANCE"],
                       label_visibility="collapsed", key="nav")
    st.divider()
    min_edge = st.slider("Min edge (EV)", 0.0, 0.15, config.MIN_EDGE, 0.005,
                         format="%.3f")
    bankroll = st.number_input("Bankroll ($)", min_value=0, value=1000, step=100)
    show_all = st.checkbox("Show rows without edges", value=False)
    st.caption(f"Stakes are {config.KELLY_FRACTION:.0%}-Kelly × bankroll.")
    if st.button("↻ Refresh", width="stretch"):
        refresh()
        st.rerun()

if not slates:
    st.title("🎯 OneSource Projections")
    st.info("No data yet. The hourly GitHub Action publishes "
            "data/output/latest.json, or click **↻ Refresh** (needs API keys).")
    st.stop()

dates = data.get("dates") or sorted(slates.keys(), reverse=True)
default_date = data.get("primary_date", dates[0]) if dates else None
gen = str(data.get("generated_at", ""))[:16].replace("T", " ")


# ---------------------------------------------------------------------------
# Top bar: title + search
# ---------------------------------------------------------------------------

def topbar(title: str, with_search: bool = True) -> str:
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"<div class='osp-title'>{title}</div>", unsafe_allow_html=True)
    q = ""
    if with_search:
        with right:
            q = st.text_input("Search", "", placeholder="🔍  team or player…",
                              label_visibility="collapsed")
    st.caption(f"Updated {gen} ET · refreshes hourly · not financial advice")
    return q.strip().lower()


def pick_date() -> str:
    return st.radio("Slate", dates, horizontal=True, label_visibility="collapsed",
                    index=dates.index(default_date) if default_date in dates else 0,
                    key="slate")


# ---------------------------------------------------------------------------
# Sport view: games (cards) + props
# ---------------------------------------------------------------------------

def render_sport(sport: str):
    q = topbar(sport)
    date_sel = pick_date()
    blob = slates.get(date_sel, {}).get(sport, {})
    games = blob.get("games", []) or []
    props = blob.get("props", []) or []
    if blob.get("error"):
        st.warning(f"{sport} pipeline error: {blob['error']}")
    if not games and not props:
        st.info(f"No games scheduled for {sport} on {date_sel}.")
        return

    tab_g, tab_p = st.tabs([f"📅 Games ({len(games)})", f"👤 Props ({len(props)})"])

    with tab_g:
        shown = [g for g in games if not q or q in
                 f"{g.get('home_team','')} {g.get('away_team','')}".lower()]
        if not shown:
            st.info("No games match the search." if q else "No games.")
        cols = st.columns(2)
        for i, g in enumerate(shown):
            with cols[i % 2]:
                st.markdown(ui.game_card_html(sport, g), unsafe_allow_html=True)

    with tab_p:
        render_props(sport, props, q)


def render_props(sport: str, props: list, q: str):
    if not props:
        st.info("No props yet — MLB batter props post once lineups are "
                "confirmed (~2-4h before first pitch).")
        return
    df = pd.DataFrame(props)
    if q and "player" in df.columns:
        df = df[df["player"].str.contains(q, case=False, na=False)]
    ev_like = [c for c in ("ev", "ev_over", "ev_under") if c in df.columns]
    if ev_like:
        df["_best"] = df[ev_like].apply(pd.to_numeric, errors="coerce").max(axis=1)
        if not show_all:
            df = df[df["_best"].notna() & (df["_best"] >= min_edge)]
        df = df.sort_values("_best", ascending=False)
    markets = sorted(ui.short_market(m) for m in df.get("market", pd.Series()).dropna().unique())
    market = st.selectbox("Market", ["All"] + markets) if markets else "All"
    view = ui.prep_props(df.drop(columns=["_best"], errors="ignore"))
    if market != "All" and "Market" in view.columns:
        view = view[view["Market"] == market]
    if view.empty:
        st.info("Nothing matches the current filters.")
        return
    ev_cols = [c for c in ("EV", "Over EV", "Under EV") if c in view.columns]
    cfg = {"Over %": st.column_config.ProgressColumn(
        "Over %", min_value=0, max_value=100, format="%.0f%%")} \
        if "Over %" in view.columns else {}
    st.dataframe(ev_styler(view, ev_cols), width="stretch", hide_index=True,
                 height=560, column_config=cfg)
    st.caption("bp_* columns are BettingPros' own consensus — disagreements "
               "with the model are worth a second look.")


# ---------------------------------------------------------------------------
# PLAYS: cross-sport best bets
# ---------------------------------------------------------------------------

def render_plays():
    q = topbar("Plays")
    date_sel = pick_date()
    board = ui.build_best_bets(slates.get(date_sel, {}), min_edge)
    if q and not board.empty:
        mask = (board["bet"].str.lower().str.contains(q)
                | board["game"].str.lower().str.contains(q))
        board = board[mask]
    if board.empty:
        st.info(f"No bets clear the {min_edge:.1%} edge bar on {date_sel} yet.")
        return
    c = st.columns(4)
    c[0].metric("Edges", len(board))
    c[1].metric("Games", int((board["type"] == "Game").sum()))
    c[2].metric("Props", int((board["type"] == "Prop").sum()))
    c[3].metric("Best EV", f"{board['ev'].max():+.1%}")

    view = board.copy()
    view["price"] = view["price"].map(ui.fmt_american)
    view["model_prob"] = pd.to_numeric(view["model_prob"], errors="coerce") * 100
    view["ev"] = pd.to_numeric(view["ev"], errors="coerce") * 100
    view["stake"] = (pd.to_numeric(view["kelly"], errors="coerce")
                     .fillna((view["ev"] / 100 * config.KELLY_FRACTION).clip(lower=0))
                     * bankroll).round(0)
    view["time"] = view["time"].map(ui.fmt_time_et)
    view = view[["sport", "bet", "game", "time", "price", "model_prob", "ev",
                 "stake"]].rename(columns={
        "sport": "Sport", "bet": "Bet", "game": "Game", "time": "Time",
        "price": "Price", "model_prob": "Model %", "ev": "EV %", "stake": "Stake $"})
    st.dataframe(
        ev_styler(view, ["EV %"]), width="stretch", hide_index=True, height=600,
        column_config={
            "Model %": st.column_config.ProgressColumn(
                "Model %", min_value=0, max_value=100, format="%.0f%%"),
            "Stake $": st.column_config.NumberColumn(format="$%d")})
    st.caption("Every edge across sports for the slate, sorted by EV. "
               "Stake = ¼-Kelly × bankroll.")


# ---------------------------------------------------------------------------
# PERFORMANCE
# ---------------------------------------------------------------------------

def render_performance():
    topbar("Performance", with_search=False)
    perf = (data or {}).get("performance", {})
    overall = perf.get("overall", {})
    ledger = load_ledger()
    if not overall.get("graded_games") and not ledger:
        st.info("No graded results yet — performance accrues as projected "
                "games finish and the hourly job grades them.")
        return
    c = st.columns(5)
    c[0].metric("Graded games", overall.get("graded_games", 0))
    c[1].metric("Model Brier", overall.get("model_brier") or "—",
                help="Win-probability error. 0.25 = coin flip; lower is better.")
    c[2].metric("Bets", overall.get("bets", 0))
    units = overall.get("units", 0)
    c[3].metric("Units", f"{units:+.2f}" if units else "0.00")
    roi = overall.get("roi_pct")
    c[4].metric("ROI", f"{roi:+.1f}%" if roi is not None else "—")

    equity = ui.cumulative_units(ledger)
    if not equity.empty:
        st.line_chart(equity, y="units", height=260)
    by_sport = perf.get("by_sport", {})
    if by_sport:
        st.dataframe(pd.DataFrame(by_sport).T, width="stretch")
    recent = ui.recent_bets(ledger)
    if not recent.empty:
        st.subheader("Recent graded bets")
        st.dataframe(recent, width="stretch", hide_index=True)
    st.caption("Forward-test record at projection-time prices. Brier covers "
               "every projected game; units/ROI cover recommended bets only.")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

if section in NAV_SPORTS:
    render_sport(section)
elif section == "PLAYS":
    render_plays()
elif section == "PERFORMANCE":
    render_performance()
