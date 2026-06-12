"""OneSource Projections dashboard.

Run locally:  streamlit run app/dashboard.py
Data source:  data/output/latest.json (rewritten hourly by the GitHub
Action). The "Refresh" button re-runs projections live if API keys are
configured in this environment.
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

st.set_page_config(page_title="OneSource Projections", page_icon="🎯",
                   layout="wide", initial_sidebar_state="expanded")

require_password()

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; padding-bottom: 2rem; }
  [data-testid="stMetric"] {
    background: rgba(40, 60, 50, 0.25);
    border: 1px solid rgba(80, 160, 120, 0.25);
    border-radius: 10px; padding: 10px 14px;
  }
  [data-testid="stMetricLabel"] { opacity: 0.75; }
  h1 { font-size: 1.9rem !important; margin-bottom: 0 !important; }
  .stTabs [data-baseweb="tab"] { font-size: 1.0rem; padding: 0.4rem 1.1rem; }
  div[data-testid="stCaptionContainer"] { opacity: 0.65; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def load_data() -> dict | None:
    path = config.OUTPUT_DIR / "latest.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


@st.cache_data(ttl=300)
def load_ledger() -> list[dict]:
    return results.load_ledger()


def refresh():
    """Manual refresh: re-project today+tomorrow and rewrite latest.json in
    the hourly format (skips the snapshot/grading the scheduled job does)."""
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
    """Normalize hourly multi-date format and legacy single-date format."""
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
            return "background-color: rgba(34,139,84,0.35); font-weight: 600;"
        if v < 0:
            return "color: rgba(255,120,120,0.85);"
        return ""
    return df.style.map(color, subset=[c for c in ev_cols if c in df.columns]) \
                   .format({c: "{:+.1f}%" for c in ev_cols if c in df.columns},
                           na_rep="—") \
                   .format({c: "{:.0f}%" for c in ui.PCT_COLS if c in df.columns},
                           na_rep="—") \
                   .format({c: "{:.2f}" for c in ("Proj", "FP Proj", "BP Proj",
                                                  "Away Proj", "Home Proj",
                                                  "Proj Total", "Kelly")
                            if c in df.columns}, na_rep="—")


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

data = load_data()

head_l, head_r = st.columns([6, 1])
with head_l:
    st.title("🎯 OneSource Projections")
with head_r:
    if st.button("↻ Refresh", width="stretch"):
        refresh()
        data = load_data()

slates = slates_by_date(data) if data else {}
if not slates:
    st.info("No data yet. The hourly GitHub Action publishes "
            "data/output/latest.json, or click **↻ Refresh** (needs API keys).")
    st.stop()

gen = str(data.get("generated_at", ""))[:16].replace("T", " ")
st.caption(f"Updated {gen} ET · refreshes hourly · "
           "EV per 1u at best available price · not financial advice")

# Sidebar controls
with st.sidebar:
    st.subheader("Settings")
    min_edge = st.slider("Minimum edge (EV)", 0.0, 0.15, config.MIN_EDGE, 0.005,
                         format="%.3f")
    bankroll = st.number_input("Bankroll ($)", min_value=0, value=1000, step=100)
    show_all = st.checkbox("Show rows without edges", value=False)
    st.caption(f"Stakes shown are {config.KELLY_FRACTION:.0%}-Kelly × bankroll.")

dates = data.get("dates") or sorted(slates.keys(), reverse=True)
default = data.get("primary_date", dates[0]) if dates else None
date_sel = st.radio("Slate", dates, horizontal=True,
                    index=dates.index(default) if default in dates else 0)
day = slates.get(date_sel, {})
sports_with_data = [k for k, v in day.items() if v.get("games") or v.get("props")]

tab_board, tab_games, tab_props, tab_perf = st.tabs(
    ["🔥 Best Bets", "📅 Games", "👤 Props", "📈 Performance"])

# ---------------------------------------------------------------------------
# Best Bets — every edge across sports for the selected date
# ---------------------------------------------------------------------------

with tab_board:
    board = ui.build_best_bets(day, min_edge)
    if board.empty:
        st.info(f"No bets clear the {min_edge:.1%} edge bar on {date_sel} yet. "
                "Lines fill in as books post them — check back after the next "
                "hourly run.")
    else:
        n_game = int((board["type"] == "Game").sum())
        n_prop = int((board["type"] == "Prop").sum())
        c = st.columns(4)
        c[0].metric("Edges found", len(board))
        c[1].metric("Game bets", n_game)
        c[2].metric("Prop bets", n_prop)
        c[3].metric("Best EV", f"{board['ev'].max():+.1%}")

        view = board.copy()
        view["price"] = view["price"].map(ui.fmt_american)
        view["model_prob"] = pd.to_numeric(view["model_prob"], errors="coerce") * 100
        view["ev"] = pd.to_numeric(view["ev"], errors="coerce") * 100
        view["stake"] = (pd.to_numeric(view["kelly"], errors="coerce")
                         .fillna((view["ev"] / 100 * config.KELLY_FRACTION).clip(lower=0))
                         * bankroll).round(0)
        view["time"] = view["time"].map(ui.fmt_time_et)
        view = view[["sport", "bet", "game", "time", "price", "model_prob",
                     "ev", "stake"]].rename(columns={
            "sport": "Sport", "bet": "Bet", "game": "Game", "time": "Time",
            "price": "Price", "model_prob": "Model %", "ev": "EV %",
            "stake": "Stake $"})
        st.dataframe(
            ev_styler(view, ["EV %"]),
            width="stretch", hide_index=True, height=600,
            column_config={
                "Model %": st.column_config.ProgressColumn(
                    "Model %", min_value=0, max_value=100, format="%.0f%%"),
                "Stake $": st.column_config.NumberColumn(format="$%d"),
            })
        st.caption("Sorted by EV. Stake = suggested ¼-Kelly fraction of the "
                   "sidebar bankroll. Model % is our win/over probability for "
                   "the listed side.")

# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------

with tab_games:
    if not sports_with_data:
        st.info(f"No games for any in-season sport on {date_sel}.")
    for sport in sports_with_data:
        blob = day[sport]
        games = pd.DataFrame(blob.get("games", []))
        st.subheader(sport)
        if blob.get("error"):
            st.warning(f"{sport} pipeline error: {blob['error']}")
        if games.empty:
            st.write("No games.")
            continue
        view = ui.prep_games(games)
        ev_cols = [c for c in ("Away EV", "Home EV", "Over EV") if c in view.columns]
        prob_cfg = {c: st.column_config.ProgressColumn(
            c, min_value=0, max_value=100, format="%.0f%%")
            for c in ("Away Win", "Home Win", "Over %") if c in view.columns}
        st.dataframe(ev_styler(view, ev_cols), width="stretch",
                     hide_index=True, column_config=prob_cfg)

# ---------------------------------------------------------------------------
# Props
# ---------------------------------------------------------------------------

with tab_props:
    sport_p = st.radio("Sport", sports_with_data or ["—"], horizontal=True,
                       key="props_sport", label_visibility="collapsed")
    props = pd.DataFrame(day.get(sport_p, {}).get("props", []))
    if props.empty:
        st.info("No props yet for this sport — MLB batter props post once "
                "lineups are confirmed (~2-4h before first pitch).")
    else:
        f1, f2 = st.columns([2, 3])
        with f1:
            markets = sorted(ui.short_market(m)
                             for m in props["market"].dropna().unique())
            market = st.selectbox("Market", ["All"] + markets)
        with f2:
            search = st.text_input("Player search", "",
                                   placeholder="start typing a name…")

        view_raw = props.copy()
        ev_like = [c for c in ("ev", "ev_over", "ev_under") if c in view_raw.columns]
        if ev_like:
            view_raw["_best"] = view_raw[ev_like].apply(
                pd.to_numeric, errors="coerce").max(axis=1)
            if not show_all:
                view_raw = view_raw[view_raw["_best"].notna()
                                    & (view_raw["_best"] >= min_edge)]
            view_raw = view_raw.sort_values("_best", ascending=False)

        view = ui.prep_props(view_raw.drop(columns=["_best"], errors="ignore"))
        if market != "All" and "Market" in view.columns:
            view = view[view["Market"] == market]
        if search and "Player" in view.columns:
            view = view[view["Player"].str.contains(search, case=False, na=False)]

        if view.empty:
            st.info("Nothing matches the current filters.")
        else:
            ev_cols = [c for c in ("EV", "Over EV", "Under EV") if c in view.columns]
            cfg = {}
            if "Over %" in view.columns:
                cfg["Over %"] = st.column_config.ProgressColumn(
                    "Over %", min_value=0, max_value=100, format="%.0f%%")
            st.dataframe(ev_styler(view, ev_cols), width="stretch",
                         hide_index=True, height=560, column_config=cfg)
            st.caption("bp_* columns are BettingPros' own consensus — rows "
                       "where the model and their lean disagree deserve a "
                       "second look before betting.")

# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

with tab_perf:
    perf = (data or {}).get("performance", {})
    overall = perf.get("overall", {})
    ledger = load_ledger()
    if not overall.get("graded_games") and not ledger:
        st.info("No graded results yet — performance accrues automatically "
                "as projected games finish and the hourly job grades them.")
    else:
        c = st.columns(5)
        c[0].metric("Graded games", overall.get("graded_games", 0))
        c[1].metric("Model Brier", overall.get("model_brier") or "—",
                    help="Mean squared error of win probabilities. "
                         "0.25 = coin flip; lower is better.")
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
        st.caption("Forward-test record at projection-time prices "
                   "(data/track/results.jsonl). Brier covers every projected "
                   "game; units/ROI cover model-recommended bets only.")
