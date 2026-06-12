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


st.title("OneSource Projections")

col1, col2 = st.columns([5, 1])
data = load_data()
with col2:
    if st.button("Refresh now"):
        refresh()
        data = load_data()

if not data:
    st.info("No data yet. Click **Refresh now** (requires API keys) or wait "
            "for the daily GitHub Action to publish data/output/latest.json.")
    st.stop()

with col1:
    st.caption(f"Slate: {data['date']} · generated {data['generated_at'][:16]}Z")

tab_games, tab_props, tab_settings = st.tabs(["Games", "Props", "Filters"])

with tab_settings:
    min_edge = st.slider("Minimum EV edge", 0.0, 0.15, config.MIN_EDGE, 0.005)
    show_all = st.checkbox("Show rows without market lines", value=False)

games = pd.DataFrame(data["games"])
props = pd.DataFrame(data["props"])

with tab_games:
    if games.empty:
        st.write("No games on this slate.")
    else:
        cols = [c for c in ("game_time", "away_team", "home_team", "away_pitcher",
                            "home_pitcher", "away_exp_runs", "home_exp_runs",
                            "proj_total", "away_win_prob", "home_win_prob",
                            "away_ml", "home_ml", "away_ev", "home_ev")
                if c in games.columns]
        view = games[cols].copy()
        st.dataframe(
            view.style.map(
                lambda v: "background-color:#0a3d0a"
                if isinstance(v, (int, float)) and v >= min_edge else "",
                subset=[c for c in ("away_ev", "home_ev") if c in view.columns],
            ),
            use_container_width=True, hide_index=True,
        )

with tab_props:
    if props.empty:
        st.write("No props yet (lineups may not be posted).")
    else:
        market = st.selectbox("Market", sorted(props["market"].unique()))
        view = props[props["market"] == market].copy()
        if "ev" in view.columns and not show_all:
            view = view[view["ev"].notna() & (view["ev"] >= min_edge)]
            view = view.sort_values("ev", ascending=False)
        cols = [c for c in ("player", "team", "opponent", "projection", "line",
                            "odds", "model_over_prob", "ev", "kelly")
                if c in view.columns]
        st.dataframe(view[cols], use_container_width=True, hide_index=True)
        if "ev" in props.columns and props["ev"].notna().any():
            st.caption("EV is per 1u at the best available over price. "
                       "Kelly is the suggested fraction of bankroll "
                       f"({config.KELLY_FRACTION:.0%} Kelly).")
        else:
            st.caption("No market lines attached — BettingPros keys missing "
                       "or market IDs need adjusting (see README).")
