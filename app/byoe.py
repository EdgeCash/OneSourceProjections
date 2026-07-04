"""Build Your Own Edge — Streamlit page.

Lets you weight team stats into a custom edge, pick a market/model/staking rule,
and walk-forward backtest it on 360Five's historical games. Engine:
``project547/byoe.py``; data adapter: ``project547/byoe_data.py``.

Run standalone:  streamlit run app/byoe.py
Or import ``render()`` into the dashboard nav.
"""

from __future__ import annotations

import streamlit as st

from project547 import byoe, byoe_data, teamstats
from project547.sports import SPORTS

# Sports with a usable team_games history for BYOE.
_SPORTS = [k for k in ("NFL", "NCAAF", "NBA", "WNBA", "MLB") if k in SPORTS]
# Stats where a lower value is better (weight them negative by default).
_LOWER_IS_BETTER = {"opp_pts", "giveaways", "opp_pass_yds", "opp_rush_yds", "opp_tot_yds"}


def _seasons(sport: str) -> tuple[int, ...]:
    # recent seasons; teamstats decides what actually exists
    import datetime
    y = 2025
    return tuple(range(y - 2, y + 1))


def render():
    st.header("🛠️ Build Your Own Edge")
    st.caption("Weight team stats into a custom algorithm, then walk-forward "
               "backtest it. Your edge, your weights — graded honestly.")

    sport = st.selectbox("Sport", _SPORTS, index=0)
    try:
        df = teamstats.team_games(sport, _seasons(sport))
    except Exception as e:
        st.warning(f"No historical games available for {sport}: {e}")
        return
    if df is None or df.empty:
        st.warning(f"No historical games available for {sport}.")
        return

    stats = byoe_data.available_stats(df)
    if not stats:
        st.warning("No numeric stats found for this sport's history.")
        return

    st.subheader("1 · Pick stats and weights")
    chosen = st.multiselect("Stats in your edge", stats,
                            default=[s for s in ("pts", "opp_pts") if s in stats])
    inputs = []
    for s in chosen:
        default = -1.0 if s in _LOWER_IS_BETTER else 1.0
        w = st.slider(f"weight · {s}", -3.0, 3.0, default, 0.1, key=f"w_{s}")
        if w != 0:
            inputs.append(byoe.EdgeInput(s, w))

    st.subheader("2 · Market, model, staking")
    c1, c2, c3 = st.columns(3)
    market = c1.selectbox("Market", byoe.MARKETS)
    model = c2.selectbox("Model", byoe.MODELS,
                         help="weighted = ranker (picks a side); normal = "
                              "probabilities via the sport's Normal model.")
    stake = c3.selectbox("Staking", byoe.STAKES)

    if not inputs:
        st.info("Add at least one weighted stat to build your edge.")
        return

    edge = byoe.Edge(name="custom", inputs=tuple(inputs), market=market,
                     model=model, stake=stake)

    if st.button("▶ Backtest this edge", type="primary"):
        with st.spinner("Grading walk-forward…"):
            res = byoe_data.backtest_edge(edge, sport, _seasons(sport))
        s = res.summary()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Games", s["n"])
        m2.metric("Record", s["record"])
        m3.metric("Hit rate", f"{s['hit_rate']*100:.1f}%")
        m4.metric("Units", f"{s['units']:+.1f}")
        if res.equity:
            st.line_chart({"units": res.equity})
        be = "52.4% (-110)"
        st.caption(f"Graded against **real closing lines + american odds** "
                   f"(spread/total/moneyline). Break-even is ~{be}; beating the "
                   f"close is the bar. Note: these are bets *at* the close, so "
                   f"this is ROI/ATS, not CLV — CLV needs opening lines.")


if __name__ == "__main__":
    st.set_page_config(page_title="Build Your Own Edge", page_icon="🛠️")
    render()
