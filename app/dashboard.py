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
from onesource import config, dfs, playerlogs, results, teamstats  # noqa: E402
from onesource.sports import SPORTS, default_slate_date  # noqa: E402

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
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from onesource import pipeline

    et = ZoneInfo("America/New_York")
    today = datetime.now(et).date()
    today, tomorrow = today.isoformat(), (today + timedelta(1)).isoformat()
    with st.spinner("Re-running projections (a couple of minutes)..."):
        slates = {}
        for d in (today, tomorrow):
            slates[d] = pipeline.run(d, write=False)["sports"]
            results.archive_projections(d, slates[d])
        primary = default_slate_date([today, tomorrow], slates) or today
        out = {"generated_at": pd.Timestamp.now("UTC").isoformat(),
               "primary_date": primary, "dates": [today, tomorrow],
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
    section = st.radio("Navigate", NAV_SPORTS + ["PLAYS", "DFS", "PERFORMANCE"],
                       label_visibility="collapsed", key="nav")
    st.divider()
    min_edge = st.slider("Min edge (EV)", 0.0, 0.15, config.MIN_EDGE, 0.005,
                         format="%.3f")
    bankroll = st.number_input("Bankroll ($)", min_value=0, value=1000, step=100)
    show_all = st.checkbox("Show rows without edges", value=False)
    hide_wild = st.checkbox("Hide implausible edges (≥30%)", value=True)
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
# Choose the default slate live (ET-anchored) rather than trusting the baked
# primary_date: keeps the app on today's slate until today's games finish.
default_date = (default_slate_date(dates, slates)
                or data.get("primary_date", dates[0])) if dates else None
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
        if shown:
            st.markdown("##### 📋 Full matchup breakdown")
            c1, c2 = st.columns([4, 2])
            labels = [f"{g.get('away_team')} @ {g.get('home_team')}" for g in shown]
            with c2:
                show_all_cards = st.toggle("Scroll all matchups", value=False,
                                           key=f"allcards_{sport}_{date_sel}")
            if show_all_cards:
                for g in shown:
                    render_research_card(sport, g, date_sel, caption=False)
                st.caption("Offense L5 vs the opponent's matching defense; "
                           "small numbers are league ranks (green = top "
                           "third). ★ = offense out-ranks the defense.")
            else:
                with c1:
                    pick = st.selectbox("Game", labels, label_visibility="collapsed",
                                        key=f"matchup_{sport}_{date_sel}")
                g = shown[labels.index(pick)]
                render_research_card(sport, g, date_sel)

    with tab_p:
        render_props(sport, props, q, blob.get("injuries") or [])

    news = blob.get("news") or []
    if news:
        with st.expander(f"📰 Latest {sport} news ({len(news)})"):
            for n in news:
                head = n.get("title", "")
                ply = n.get("player", "")
                tag = f"**{ply}** — " if ply and ply not in head else ""
                st.markdown(f"{tag}**{head}**")
                if n.get("body"):
                    st.caption(n["body"])


@st.cache_data(ttl=900, show_spinner=False)
def _matchup(sport: str, home: str, away: str, asof: str) -> dict:
    try:
        return teamstats.matchup(sport, home, away, asof)
    except Exception:
        return {}


def render_research_card(sport: str, g: dict, date_sel: str, caption: bool = True):
    m = _matchup(sport, g.get("home_team", ""), g.get("away_team", ""), date_sel)
    if not m:
        st.info("Team stat splits aren't available for this matchup yet.")
        st.markdown(ui.game_card_html(sport, g), unsafe_allow_html=True)
        return
    st.markdown(ui.research_card_html(sport, g, m, min_edge), unsafe_allow_html=True)
    if caption:
        st.caption("Offense L5 vs the opponent's matching defense L5; small "
                   "numbers are league ranks (green = top third). ★ = the "
                   "offense out-ranks the defense it faces.")


def render_props(sport: str, props: list, q: str, injuries: list | None = None):
    if not props:
        st.info("No props yet — MLB batter props post once lineups are "
                "confirmed (~2-4h before first pitch).")
        return
    df = pd.DataFrame(props)
    if "player" not in df.columns:
        st.info("Prop data is missing player names — check the next hourly run.")
        return
    if q:
        df = df[df["player"].str.contains(q, case=False, na=False)]
    ev_like = [c for c in ("ev", "ev_over", "ev_under") if c in df.columns]
    if ev_like:
        df["_best"] = df[ev_like].apply(pd.to_numeric, errors="coerce").max(axis=1)
        if not show_all and df["_best"].notna().any():
            df = df[df["_best"].notna() & (df["_best"] >= min_edge)]
        df = df.sort_values("_best", ascending=False, na_position="last")
    markets = sorted(ui.short_market(m) for m in df.get("market", pd.Series()).dropna().unique())
    market = st.selectbox("Market", ["All"] + markets) if markets else "All"
    if market != "All" and "market" in df.columns:
        df = df[df["market"].map(ui.short_market) == market]
    df = df.reset_index(drop=True)
    view = ui.prep_props(df.drop(columns=["_best"], errors="ignore"))
    if view.empty:
        st.info("Nothing matches the current filters. Toggle “Show rows "
                "without edges” in the sidebar to browse the full board.")
        return
    ev_cols = [c for c in ("EV", "Over EV", "Under EV") if c in view.columns]
    heat = [c for c in ui.HEAT_COLS if c in view.columns]
    styler = ev_styler(view, ev_cols)
    if heat:
        styler = styler.background_gradient(cmap="RdYlGn", vmin=0, vmax=100,
                                            subset=heat) \
                       .format({c: "{:.0f}%" for c in heat}, na_rep="—")
    sel = st.dataframe(styler, width="stretch", hide_index=True, height=480,
                       on_select="rerun", selection_mode="single-row",
                       key=f"props_table_{sport}")
    st.caption("👆 Tap a row for the player deep-dive. L5/L10/L20/Season/H2H "
               "= how often the player has gone OVER the line (our game "
               "logs). bp_* are BettingPros' consensus.")

    rows = (sel.selection.rows if sel and getattr(sel, "selection", None) else [])
    if rows:
        render_prop_detail(sport, df.iloc[rows[0]].to_dict(), injuries or [])
    else:
        st.info("Select a prop above to open the deep-dive: recent-game "
                "chart, hit-rate splits, and model vs BettingPros read.")


def render_prop_detail(sport: str, p: dict, injuries: list | None = None):
    """Deep-dive panel for one prop: header facts, trend chart, hit-rate
    splits, and a model-vs-market read."""
    player = p.get("player", "")
    market = p.get("market", "")
    line = p.get("line")
    season = int(default_date[:4]) if default_date else None
    title = f"{player} · {ui.short_market(market)}" + (
        f" {line:g}" if isinstance(line, (int, float)) and pd.notna(line) else "")
    img = p.get("player_image")
    pos = p.get("position") or ""
    team = p.get("team") or ""
    head = (f"<img src='{img}' width='52' height='52' "
            f"style='border-radius:50%;object-fit:cover;vertical-align:middle;"
            f"margin-right:10px;' onerror=\"this.style.display='none'\">"
            if img else "")
    sub = " · ".join(x for x in (team, pos) if x)
    st.markdown(
        f"<div style='display:flex;align-items:center;margin:6px 0;'>{head}"
        f"<div><div style='font-size:1.15rem;font-weight:700;'>🔎 {title}</div>"
        f"<div style='color:#8b949e;font-size:0.8rem;'>{sub}</div></div></div>",
        unsafe_allow_html=True)

    c = st.columns(5)
    mop = p.get("model_over_prob")
    c[0].metric("Line", f"{line:g}" if line is not None and pd.notna(line) else "—")
    c[1].metric("Our proj", p.get("projection") if p.get("projection") is not None else "—")
    c[2].metric("Over %", f"{mop * 100:.0f}%" if mop is not None and pd.notna(mop) else "—")
    best_ev = max([v for v in (p.get("ev"), p.get("ev_over"), p.get("ev_under"))
                   if v is not None and pd.notna(v)], default=None)
    c[3].metric("Best EV", f"{best_ev:+.1%}" if best_ev is not None else "—")
    k = p.get("kelly")
    c[4].metric("Stake", f"${k * bankroll:,.0f}"
                if k is not None and pd.notna(k) and k > 0 else "—")

    left, right = st.columns([3, 2])
    with left:
        series = playerlogs.recent_series(sport, player, market, n=12, season=season)
        chart_line = float(line) if line is not None and pd.notna(line) else 0.0
        chart = ui.prop_chart(series, chart_line, title)
        if chart is None:
            st.info("No game-log history for this player yet.")
        else:
            st.altair_chart(chart)
            if chart_line:
                hit = sum(1 for s in series if s["value"] > chart_line)
                st.caption(f"Over in {hit} of the last {len(series)} "
                           f"(dashed = {chart_line:g}).")
    with right:
        if line is not None and pd.notna(line):
            hr = playerlogs.hit_rates(sport, player, market, float(line),
                                      opponent=p.get("opponent"), season=season)
        else:
            hr = {}
        if hr:
            chips = {"L5": hr.get("l5"), "L10": hr.get("l10"),
                     "L20": hr.get("l20"), "Season": hr.get("season"),
                     "H2H": hr.get("h2h")}
            cc = st.columns(len(chips))
            for i, (k_, v) in enumerate(chips.items()):
                cc[i].metric(k_, f"{v * 100:.0f}%" if v is not None else "—")
        # model vs BettingPros read + market context
        bits = []
        bp_proj, bp_side = p.get("bp_projection"), p.get("bp_recommended_side")
        bp_rating = p.get("bp_bet_rating")
        if p.get("projection") is not None and bp_proj is not None and pd.notna(bp_proj):
            bits.append(f"We project **{p['projection']}**, BettingPros "
                        f"projects **{bp_proj:g}**.")
        if bp_side:
            agree = (mop is not None and pd.notna(mop)
                     and ((mop >= 0.5) == (str(bp_side).lower() == "over")))
            verdict = "✅ model agrees" if agree else "⚠️ model disagrees"
            stars = f" ({'★' * int(bp_rating)})" if bp_rating and pd.notna(bp_rating) else ""
            bits.append(f"BP lean: **{str(bp_side).upper()}**{stars} — {verdict}.")
        opp_rank = p.get("opp_rank")
        if opp_rank is not None and pd.notna(opp_rank):
            bits.append(f"Opponent ranks **#{int(opp_rank)}** defending this stat.")
        ppo, ptot = p.get("pick_pct_over"), p.get("picks_total")
        if ppo is not None and pd.notna(ppo) and ptot:
            side_txt = "over" if ppo >= 0.5 else "under"
            pct = ppo if ppo >= 0.5 else 1 - ppo
            bits.append(f"Public picks (BP): **{pct:.0%} on the {side_txt}** "
                        f"({int(ptot)} picks).")
        streak, stype = p.get("streak"), p.get("streak_type")
        if streak and pd.notna(streak) and stype:
            bits.append(f"Current streak: **{int(streak)} straight {stype}s**.")
        open_, now_ = p.get("over_open"), p.get("over_odds")
        if (open_ is not None and pd.notna(open_) and now_ is not None
                and pd.notna(now_) and open_ != now_):
            moved = "toward the over" if now_ < open_ else "toward the under"
            bits.append(f"Over opened **{ui.fmt_american(open_)}**, now "
                        f"**{ui.fmt_american(now_)}** (moved {moved}).")
        from onesource.names import normalize as _norm
        inj = next((i for i in (injuries or [])
                    if i.get("norm") == _norm(player)), None)
        if inj:
            bits.append(f"🩹 **Injury report: {inj.get('status', '')}** "
                        f"{('— ' + inj['note']) if inj.get('note') else ''}")
        prof = _player_profile(sport, player, market)
        if prof:
            bits.append(prof)
        if p.get("opponent"):
            bits.append(f"{p.get('team', '')} vs {p.get('opponent', '')}.")
        if bits:
            st.markdown("\n\n".join(bits))


def _player_profile(sport: str, player: str, market: str) -> str | None:
    """Season profile line (MLB: box-log rates + prior-season Statcast
    expected stats)."""
    if sport != "MLB":
        return None
    try:
        from onesource import internal_stats
        from onesource.names import normalize as _norm
        season = int(default_date[:4]) if default_date else 2026
        n = _norm(player)
        if str(market).startswith("pitcher"):
            t = internal_stats.pitcher_table(season)
            r = t[t["norm_name"] == n]
            if r.empty:
                return None
            r = r.iloc[0]
            return (f"📊 Season: **{r['FIP']:.2f} FIP**, "
                    f"**{r['K%'] * 100:.1f}% K**, "
                    f"{r['IP'] / max(r['GS'], 1):.1f} IP/start ({int(r['GS'])} GS).")
        t = internal_stats.batter_table(season)
        r = t[t["norm_name"] == n]
        if r.empty:
            return None
        r = r.iloc[0]
        x = ""
        if pd.notna(r.get("est_ba")) and pd.notna(r.get("est_slg")):
            x = f" · last-season Statcast **{r['est_ba']:.3f} xBA / {r['est_slg']:.3f} xSLG**"
        return (f"📊 Season: **{r['AVG']:.3f} AVG / {r['SLG']:.3f} SLG**, "
                f"{int(r['HR'])} HR in {int(r['PA'])} PA{x}.")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# PLAYS: cross-sport best bets
# ---------------------------------------------------------------------------

def render_plays():
    q = topbar("Plays")
    date_sel = pick_date()
    board = ui.build_best_bets(slates.get(date_sel, {}), min_edge)
    if hide_wild and not board.empty:
        board = board[pd.to_numeric(board["ev"], errors="coerce") < 0.30]
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
    stake_ev = (view["ev"] / 100 * config.KELLY_FRACTION).clip(lower=0)
    stake_ev[view["ev"] >= 30] = 0  # no stake on implausible edges
    view["stake"] = (pd.to_numeric(view["kelly"], errors="coerce")
                     .fillna(stake_ev) * bankroll).round(0)
    view["time"] = view["time"].map(ui.fmt_time_et)
    cols = ["sport", "bet", "game", "time", "price", "model_prob", "ev", "stake"]
    if "flag" in view.columns and view["flag"].astype(bool).any():
        cols.append("flag")
    view = view[cols].rename(columns={
        "sport": "Sport", "bet": "Bet", "game": "Game", "time": "Time",
        "price": "Price", "model_prob": "Model %", "ev": "EV %",
        "stake": "Stake $", "flag": "Note"})
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

def render_dfs():
    topbar("DFS Optimizer", with_search=False)
    date_sel = pick_date()
    cands = dfs.candidates(slates.get(date_sel, {}))
    if cands.empty:
        st.info("No props with model probabilities yet for this slate.")
        return
    st.caption("Picks ranked by our model's confidence on the better side "
               f"(capped at {dfs.PROB_CAP:.0%} — the model runs hot in the "
               "tails). Slips assume PrizePicks multipliers; Underdog is "
               "nearly identical. Legs treated as independent.")
    slips = dfs.best_slips(cands)
    if slips:
        cols = st.columns(len(slips))
        for i, s_ in enumerate(slips):
            with cols[i]:
                pe, fe = s_["power_ev"], s_["flex_ev"]
                best = max([x for x in (pe, fe) if x is not None], default=None)
                color = "normal" if best is None else ("off" if best < 0 else "normal")
                st.metric(f"{s_['size']}-pick", f"{best:+.0%} EV" if best is not None else "—",
                          help=f"Power {pe:+.0%}" + (f" · Flex {fe:+.0%}" if fe is not None else "")
                          if pe is not None else None)
        top = slips[-1]
        st.markdown(f"##### Suggested {top['size']}-leg card "
                    f"(hit-all {top['joint']:.1%})")
        for l in top["legs"]:
            st.markdown(f"- **{l['player']}** ({l['sport']}, {l['team']}) — "
                        f"**{l['side']} {l['line']:g} "
                        f"{ui.short_market(str(l['market']))}** · "
                        f"model {l['raw_prob']:.0%} (capped {l['prob']:.0%})")
        if all(s_["power_ev"] is not None and s_["power_ev"] < 0 for s_ in slips):
            st.warning("No positive-EV slip today at capped probabilities — "
                       "DFS multipliers price in a big house edge; pass is "
                       "a fine play.")
    st.markdown("##### Candidate pool")
    view = cands.head(25).rename(columns={
        "player": "Player", "sport": "Sport", "team": "Team",
        "market": "Market", "line": "Line", "side": "Side",
        "prob": "P (capped)", "raw_prob": "P (model)"})
    st.dataframe(view, width="stretch", hide_index=True)


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
        st.subheader("Cumulative units")
        st.line_chart(equity, y="units", height=260)

    # Calibration: do our stated win-probabilities match reality?
    curve = ui.calibration_curve(ledger)
    st.subheader("Win-probability calibration")
    if curve.empty:
        st.info("Calibration accrues as projected games finish. Each graded "
                "game adds a point comparing our predicted win % to the actual "
                "result — once enough land, the curve should hug the diagonal.")
    else:
        ece = ui.calibration_error(curve)
        cc = st.columns([3, 1])
        with cc[0]:
            st.altair_chart(ui.calibration_chart(curve))
        with cc[1]:
            st.metric("Calibration error", f"{ece:.1%}" if ece is not None else "—",
                      help="Avg gap between predicted and actual win %, weighted "
                           "by games. Lower is better; under ~5% is well "
                           "calibrated.")
            st.metric("Graded games", int(curve["n"].sum()))
        st.caption("Dashed line = perfect calibration. Points above it mean we "
                   "were under-confident; below, over-confident. Bubble size = "
                   "games in that bucket.")

    by_sport = perf.get("by_sport", {})
    if by_sport:
        st.subheader("By sport")
        st.dataframe(pd.DataFrame(by_sport).T, width="stretch")
    recent = ui.recent_bets(ledger)
    if not recent.empty:
        st.subheader("Recent graded bets")
        st.dataframe(recent, width="stretch", hide_index=True)
    st.caption("Forward-test record at projection-time prices. Brier + "
               "calibration cover every projected game; units/ROI cover "
               "recommended bets only.")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

if section in NAV_SPORTS:
    render_sport(section)
elif section == "PLAYS":
    render_plays()
elif section == "DFS":
    render_dfs()
elif section == "PERFORMANCE":
    render_performance()
