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
    """Manual refresh: re-project today+tomorrow and rewrite latest.json in
    the hourly format (skips the snapshot/grading the scheduled job does)."""
    import json as _json
    from datetime import date, timedelta

    from onesource import pipeline, results

    today, tomorrow = date.today().isoformat(), (date.today() + timedelta(1)).isoformat()
    with st.spinner("Re-running projections (a couple of minutes)..."):
        slates = {}
        for d in (today, tomorrow):
            slates[d] = pipeline.run(d, write=False)["sports"]
            results.archive_projections(d, slates[d])
        out = {"generated_at": pd.Timestamp.utcnow().isoformat(),
               "primary_date": tomorrow, "dates": [today, tomorrow],
               "slates": slates, "performance": results.performance()}
        (config.OUTPUT_DIR / "latest.json").write_text(_json.dumps(out, default=str))
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


def slates_by_date(data: dict) -> dict:
    """Normalize both the hourly multi-date format and the legacy
    single-date format to {date: sports_blob}."""
    if "slates" in data:
        return data["slates"]
    if "sports" in data:
        return {data.get("date", "latest"): data["sports"]}
    return {}


st.title("OneSource Projections")

col1, col2 = st.columns([5, 1])
data = load_data()
with col2:
    if st.button("Refresh now"):
        refresh()
        data = load_data()

slates = slates_by_date(data) if data else {}
if not slates:
    st.info("No data yet. The hourly GitHub Action publishes "
            "data/output/latest.json, or click **Refresh now** (needs API keys).")
    st.stop()

with col1:
    gen = data.get("generated_at", "")[:16]
    st.caption(f"Generated {gen} · {len(slates)} slate(s)")

# date selector (default to the primary/upcoming slate)
dates = data.get("dates") or sorted(slates.keys(), reverse=True)
default = data.get("primary_date", dates[0]) if dates else None
date_sel = st.radio("Slate date", dates, horizontal=True,
                    index=dates.index(default) if default in dates else 0,
                    key="date_sel")
day = slates.get(date_sel, {})

sports_with_data = [k for k, v in day.items() if v.get("games") or v.get("props")]
if not sports_with_data:
    st.info(f"No games found for any in-season sport on {date_sel}.")
    st.stop()

sport = st.radio("Sport", sports_with_data, horizontal=True, label_visibility="collapsed")
blob = day[sport]
if blob.get("error"):
    st.warning(f"{sport} pipeline error: {blob['error']}")

tab_games, tab_props, tab_perf, tab_settings = st.tabs(
    ["Games", "Props", "Performance", "Filters"])

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

with tab_perf:
    perf = data.get("performance", {})
    overall = perf.get("overall", {})
    if not overall or not overall.get("graded_games"):
        st.info("No graded results yet — performance accrues as projected "
                "games finish and the hourly job grades them.")
    else:
        c = st.columns(5)
        c[0].metric("Graded games", overall.get("graded_games"))
        c[1].metric("Model Brier", overall.get("model_brier"))
        c[2].metric("Bets", overall.get("bets"))
        c[3].metric("Units", overall.get("units"))
        c[4].metric("ROI %", overall.get("roi_pct"))
        by_sport = perf.get("by_sport", {})
        if by_sport:
            st.dataframe(pd.DataFrame(by_sport).T, use_container_width=True)
        st.caption("Forward-test record from data/track/results.jsonl. "
                   "Lower Brier = better win-prob calibration; ROI is per "
                   "1u on model-recommended bets graded at projection-time prices.")
