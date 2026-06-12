"""OneSource Projections dashboard.

Run locally:  streamlit run app/dashboard.py
Data source:  data/output/latest.json (written by the pipeline / GitHub
Action). The "Refresh now" button re-runs the pipeline live if API keys
are configured in this environment.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import require_password  # noqa: E402
from onesource import config  # noqa: E402

st.set_page_config(page_title="OSP", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

require_password()


@st.cache_data(ttl=600)
def load_data() -> dict | None:
    path = config.OUTPUT_DIR / "latest.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def refresh():
    from onesource import pipeline

    with st.spinner("Running pipeline (this can take a couple minutes)..."):
        pipeline.run()
    load_data.clear()


GAME_COLS = ["game_time", "away_team", "home_team", "away_pitcher", "home_pitcher",
             "away_exp_runs", "home_exp_runs", "away_exp", "home_exp", "proj_total",
             "away_win_prob", "home_win_prob", "away_ml", "home_ml",
             "away_ev", "home_ev", "away_ml_ev", "home_ml_ev",
             "total_line", "over_odds", "model_over_prob", "over_ev"]
PROP_COLS = ["player", "team", "opponent", "market", "projection", "fp_projection",
             "line", "odds", "over_odds", "under_odds", "model_over_prob",
             "ev", "ev_over", "ev_under", "kelly",
             "bp_projection", "bp_ev", "bp_recommended_side", "bp_bet_rating"]
EV_COLS = ["ev", "ev_over", "ev_under", "away_ev", "home_ev",
           "away_ml_ev", "home_ml_ev", "over_ev"]


st.title("OneSource Projections")

col1, col2 = st.columns([5, 1])
data = load_data()
with col2:
    if st.button("Refresh now"):
        refresh()
        data = load_data()

if not data or "sports" not in data:
    st.info("No data yet. Click **Refresh now** (requires API keys) or wait "
            "for the daily GitHub Action to publish data/output/latest.json.")
    st.stop()

with col1:
    st.caption(f"Slate: {data['date']} · generated {data['generated_at'][:16]}Z")

sports_with_data = [k for k, v in data["sports"].items()
                    if v.get("games") or v.get("props")]
if not sports_with_data:
    st.info("Pipeline ran but found no games for any in-season sport.")
    st.stop()

sport = st.radio("Sport", sports_with_data, horizontal=True, label_visibility="collapsed")
blob = data["sports"][sport]
if blob.get("error"):
    st.warning(f"{sport} pipeline error: {blob['error']}")

tab_games, tab_props, tab_settings = st.tabs(["Games", "Props", "Filters"])

with tab_settings:
    min_edge = st.slider("Minimum EV edge", 0.0, 0.15, config.MIN_EDGE, 0.005)
    show_all = st.checkbox("Show rows without market lines / edges", value=False)

games = pd.DataFrame(blob.get("games", []))
props = pd.DataFrame(blob.get("props", []))


def best_ev(df: pd.DataFrame) -> pd.Series:
    cols = [c for c in EV_COLS if c in df.columns]
    if not cols:
        return pd.Series([None] * len(df), index=df.index)
    return df[cols].apply(pd.to_numeric, errors="coerce").max(axis=1)


with tab_games:
    if games.empty:
        st.write("No games on this slate.")
    else:
        cols = [c for c in GAME_COLS if c in games.columns]
        ev_cols = [c for c in EV_COLS if c in cols]
        st.dataframe(
            games[cols].style.map(
                lambda v: "background-color:#0a3d0a"
                if isinstance(v, (int, float)) and v >= min_edge else "",
                subset=ev_cols,
            ),
            use_container_width=True, hide_index=True,
        )

with tab_props:
    if props.empty:
        st.write("No props yet for this sport.")
    else:
        markets = sorted(m for m in props["market"].dropna().unique())
        market = st.selectbox("Market", ["All"] + markets)
        view = props if market == "All" else props[props["market"] == market]
        view = view.copy()
        view["best_ev"] = best_ev(view)
        if not show_all:
            view = view[view["best_ev"].notna() & (view["best_ev"] >= min_edge)]
        view = view.sort_values("best_ev", ascending=False, na_position="last")
        cols = [c for c in PROP_COLS if c in view.columns and view[c].notna().any()]
        st.dataframe(view[cols + ["best_ev"]], use_container_width=True,
                     hide_index=True)
        st.caption("EV is per 1u at the best available price; kelly is the "
                   f"suggested bankroll fraction ({config.KELLY_FRACTION:.0%} "
                   "Kelly). bp_* columns are BettingPros' own consensus — "
                   "rows where you and they disagree deserve a second look.")
