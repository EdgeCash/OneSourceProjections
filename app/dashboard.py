"""360Five — research dashboard.

Run locally:  streamlit run app/dashboard.py
Data source:  data/output/latest.json (rewritten hourly by the GitHub
Action). The sidebar "Refresh" re-runs projections live if API keys are set.

Layout: a left sport-nav sidebar, a top bar (section title + search), and a
main panel that shows per-sport game cards / props, the cross-sport PLAYS
board, or the PERFORMANCE tracker.
"""

import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

log = logging.getLogger("osp.dashboard")

from app import ui  # noqa: E402
from app.auth import require_password  # noqa: E402
from project547 import ai, config, dfs, edge, playerlogs, plays, results, teamstats, workbook  # noqa: E402
from project547.sports import SPORTS, default_slate_date  # noqa: E402

st.set_page_config(page_title="360Five", page_icon="🎯",
                   layout="wide", initial_sidebar_state="expanded")

# Streamlit Cloud exposes secrets via st.secrets; mirror the AI keys into the
# environment so the anthropic SDK (and project547.ai) can find them.
for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OSP_AI_MODEL"):
    try:
        if _k not in os.environ and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
    except Exception:
        pass

require_password()

_PALETTES = {
    # 360Five brand palettes — one system, two surfaces, identical keys. Brass
    # (acc) is the ONLY decorative accent, which frees good/neg (green/red) to
    # mean exactly one thing: money on or money off. A cool info-teal (acc2)
    # carries links and section kickers. Swapping this dict re-skins every
    # `var(--…)` in the app — no call-site changes.
    #
    # Almanac paper — warm off-white stock, graphite ink, dark-brass accent.
    "cream": dict(acc="#8f6a1a", acc2="#2f7d93", link="#2f7d93", warn="#a87d22",
                  bg="#ece5d5", card="#faf6ec",
                  card2="#f2ebda", line="#dbd1bb", text="#1a2226",
                  muted="#586158", faint="#8a8472", good="#2c8854",
                  neg="#bd463d", mid="#a87d22", sb1="#e5ddca", sb2="#ece5d5",
                  glow="0.0", shadow="0.10"),
    # Terminal — pure black with a copper edge: the Sharp Sheet's own palette,
    # so the app shell and the research cards read as one system (rather than
    # cloning the reference site's teal). Copper (acc) is the single decorative
    # accent — logo, section kickers, group headers, links — which frees
    # green/red to mean exactly one thing: money on / money off.
    "dark": dict(acc="#F5B841", acc2="#3d9fff", link="#3d9fff", warn="#ffc93c",
                 bg="#000000", card="#0a0a0a",
                 card2="#141414", line="#1f1f1f", text="#EAEAEA",
                 muted="#8a8a8a", faint="#4d4d4d", good="#3ddc84",
                 neg="#ff5b4a", mid="#ffc93c", sb1="#050505", sb2="#000000",
                 glow="0.20", shadow="0.6"),
}

_THEME_CSS = """
  @import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=Spline+Sans+Mono:wght@400;500;600;700&display=swap');
  .stApp, body { color: var(--text); font-family: 'DM Sans', system-ui, sans-serif; }
  /* --- hide default Streamlit chrome (premium product, not scaffolding).
         Keep stHeader itself (transparent) so the mobile sidebar toggle lives. */
  [data-testid="stToolbar"], [data-testid="stDecoration"],
  [data-testid="stStatusWidget"], #MainMenu, footer,
  .stDeployButton { display: none !important; }
  [data-testid="stHeader"] { background: transparent !important; }
  .stApp { background: var(--bg); }
  .block-container { padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1180px; }
  h1, h2, h3, h4, .osp-brand, .osp-title { font-family: var(--disp);
    letter-spacing: 0.02em; }
  /* --- one section-header system (uppercase kicker + hairline rule) --- */
  .osp-sec { font-family: var(--disp); font-size: 0.8rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.14em; color: var(--text);
    margin: 18px 0 8px; padding-bottom: 6px; display: flex; align-items: center;
    gap: 8px; border-bottom: 1.5px solid var(--text); }
  .osp-sec .ico { font-size: 0.95rem; }
  .osp-sec .tag { margin-left: auto; font-size: 0.66rem; font-weight: 600;
    text-transform: none; letter-spacing: 0; color: var(--muted); }
  section[data-testid="stSidebar"] {
    background: var(--sb1); border-right: 1.5px solid var(--line); }
  /* --- clean nav rows (BettorSheets look): full-width rows, muted by default,
         lifted-fill + bright text on the active row; the radio dot is hidden --- */
  section[data-testid="stSidebar"] .stRadio [role="radiogroup"] { gap: 2px; }
  section[data-testid="stSidebar"] .stRadio [role="radiogroup"] > label {
    display: flex; align-items: center; width: 100%; margin: 0;
    border-radius: 9px; padding: 9px 12px; font-weight: 600;
    font-family: var(--disp); font-size: 0.94rem; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--muted); cursor: pointer;
    transition: background .12s ease, color .12s ease; }
  /* hide the radio glyph (a nested div before the label text), leaving a clean
     text row; the real <input> is already visually-hidden by BaseWeb */
  section[data-testid="stSidebar"] .stRadio [role="radiogroup"] > label > div:last-child > div > div:first-child {
    display: none !important; }
  section[data-testid="stSidebar"] .stRadio [role="radiogroup"] > label:hover {
    background: var(--card); color: var(--text); }
  section[data-testid="stSidebar"] .stRadio [role="radiogroup"] > label:has(input:checked) {
    background: var(--card2); color: var(--text);
    box-shadow: inset 3px 0 0 var(--acc); }
  /* account chip pinned to the foot of the sidebar */
  .osp-acct { display: flex; align-items: center; gap: 10px; margin-top: 8px;
    padding: 10px 6px 2px; border-top: 1.5px solid var(--line); }
  .osp-acct .av { width: 30px; height: 30px; border-radius: 50%; flex: 0 0 auto;
    display: flex; align-items: center; justify-content: center; font-weight: 700;
    font-family: var(--disp); font-size: 0.9rem; color: var(--bg);
    background: var(--acc); }
  .osp-acct .nm { font-family: var(--disp); font-weight: 600; font-size: 0.92rem;
    color: var(--text); }
  /* brand lockup */
  .osp-logo { display: flex; align-items: center; gap: 10px; margin: 2px 0 2px; }
  .osp-logo .mk { width: 30px; height: 30px; border-radius: 8px; flex: 0 0 auto;
    display: flex; align-items: center; justify-content: center; font-size: 1rem;
    color: var(--bg); font-weight: 800;
    background: linear-gradient(135deg, var(--acc), var(--acc2)); }
  /* --- PLAYS board: one clean scannable table (matchup | play) --- */
  .pl-board { border: 1.5px solid var(--line); border-radius: 12px;
    overflow: hidden; background: var(--card); margin-top: 4px; }
  .pl-head, .pl-row { display: grid; grid-template-columns: 1.35fr 1fr;
    align-items: center; }
  .pl-head { padding: 12px 18px; border-bottom: 1.5px solid var(--line);
    background: var(--card2); }
  .pl-head span { font-family: var(--disp); font-size: 0.7rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); }
  .pl-grp { padding: 9px 18px; text-align: center; background: var(--sb1);
    border-top: 1.5px solid var(--line); border-bottom: 1.5px solid var(--line);
    font-family: var(--disp); font-size: 0.72rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.16em; color: var(--acc); }
  .pl-row { padding: 13px 18px; border-bottom: 1px solid var(--line); }
  .pl-row:last-child { border-bottom: none; }
  .pl-row .m { font-weight: 600; color: var(--text); }
  .pl-row .p { font-family: var(--mono); font-weight: 600; color: var(--good);
    font-variant-numeric: tabular-nums; }
  .pl-none { padding: 13px 18px; color: var(--muted); font-weight: 600;
    border-bottom: 1px solid var(--line); }
  /* rounded pill search to match the mock */
  .stTextInput input { border-radius: 999px !important; padding-left: 15px; }
  [data-testid="stMetric"] { position: relative; overflow: hidden;
    background: var(--card); border: 1.5px solid var(--line); border-radius: 8px;
    padding: 13px 15px 11px 16px; box-shadow: none; }
  [data-testid="stMetric"]::before { content:""; position:absolute; left:0; top:0;
    bottom:0; width:3px; background: var(--text); }
  [data-testid="stMetricLabel"] { opacity: 0.7; font-size: 0.7rem;
    text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;
    font-family: var(--disp); }
  [data-testid="stMetricValue"] { font-family: var(--disp); font-weight: 600;
    font-size: 1.7rem; letter-spacing: 0; }
  .osp-brand { font-family: var(--disp); font-size: 1.5rem; font-weight: 700;
    margin: 0 0 0.1rem 0; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--text); }
  .osp-title { font-size: 1.9rem; font-weight: 600; margin: 0; }
  div[data-testid="stCaptionContainer"] { opacity: 0.7; }
  /* tighter vertical rhythm + aligned numerals everywhere */
  [data-testid="stVerticalBlock"] { gap: 0.7rem; }
  [data-testid="stMetricValue"], [data-testid="stDataFrame"],
  .osp-sec .tag { font-variant-numeric: tabular-nums; }
  /* tabs as segmented pills (graphite active on cream) */
  .stTabs [data-baseweb="tab-list"] { gap: 0.35rem; background: var(--card2);
    padding: 0.3rem; border-radius: 8px; border: 1.5px solid var(--line); }
  .stTabs [data-baseweb="tab"] { font-family: var(--disp); font-weight: 600;
    font-size: 0.9rem; height: 34px; padding: 0 0.9rem; border-radius: 6px;
    color: var(--text); letter-spacing: 0.03em; }
  .stTabs [aria-selected="true"] { background: var(--text) !important;
    color: var(--bg) !important; }
  .stTabs [data-baseweb="tab-highlight"] { display: none; }
  .stButton > button { border-radius: 6px; border: 1.5px solid var(--text);
    font-family: var(--disp); font-weight: 600; letter-spacing: 0.04em;
    background: var(--card); color: var(--text); transition: all .15s ease; }
  .stButton > button:hover { background: var(--text); color: var(--bg);
    border-color: var(--text); transform: translateY(-1px); }
  .stButton > button:active { transform: translateY(0); }
  /* form surfaces follow the palette */
  [data-baseweb="select"] > div, .stTextInput input, .stNumberInput input,
  .stTextArea textarea { background: var(--card) !important; color: var(--text) !important;
    border-color: var(--line) !important; }
  .osp-hero { background: var(--card2); border: 1.5px solid var(--text);
    border-radius: 10px; padding: 18px 22px; margin-bottom: 14px; }
  .osp-pill { display:inline-block; font-size:0.7rem; font-weight:700; padding:3px 10px;
    border-radius:999px; margin-right:6px; font-family: var(--disp);
    letter-spacing: 0.03em; }
  .osp-pill.live { animation: osppulse 1.8s ease-in-out infinite; }
  @keyframes osppulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
  a.osp-plink { color: var(--link, var(--acc2)) !important; text-decoration:none;
    font-weight:600; border-bottom:1px solid transparent; transition: color .12s ease,
    border-color .12s ease; }
  a.osp-plink:hover { border-bottom-color: currentColor; }
  /* --- responsive nav: left sidebar on desktop, top tab-strip on mobile ---
     The keyed top-strip container is hidden by default (desktop uses the
     sidebar); shown only under the 768px breakpoint where the sidebar
     collapses to a hamburger. */
  .st-key-osp_topnav { display: none; }
  @media (max-width: 768px) {
    .st-key-osp_topnav { display: block; position: sticky; top: 0; z-index: 50;
      background: var(--bg); margin: -0.2rem 0 0.5rem; padding: 0.4rem 0 0.35rem;
      border-bottom: 1.5px solid var(--line); }
    .st-key-osp_topnav [data-testid="stVerticalBlock"] { gap: 0.3rem; }
    .st-key-osp_topnav [data-testid="stHorizontalBlock"] { gap: 0.3rem; }
    .st-key-osp_topnav .stButton > button { width: 100%; font-size: 0.78rem;
      padding: 0.28rem 0.15rem; letter-spacing: 0.01em; }
  }
  /* mobile: tighter padding, smaller display type, full-width buttons */
  @media (max-width: 640px) {
    .block-container { padding: 0.6rem 0.6rem 2rem; }
    [data-testid="stMetricValue"] { font-size: 1.4rem; }
    .osp-title { font-size: 1.4rem; } .osp-brand { font-size: 1.3rem; }
    .osp-hero { padding: 13px 15px; }
    .stButton > button { width: 100%; }
  }
"""


def _theme_css(theme: str) -> str:
    p = _PALETTES.get(theme, _PALETTES["dark"])
    root = (":root { " + "".join(f"--{k}:{v}; " for k, v in p.items())
            + "--disp:'Archivo', system-ui, -apple-system, sans-serif; "
            + "--font:'Archivo', system-ui, -apple-system, sans-serif; "
            + "--mono:'Spline Sans Mono', ui-monospace, 'JetBrains Mono', "
              "Menlo, Consolas, monospace; }")
    return f"<style>{root}{_THEME_CSS}</style>"


st.markdown(_theme_css(st.session_state.get("theme", "dark")),
            unsafe_allow_html=True)


def _theme_toggle(key: str):
    """Dark (cool near-black) is the default look; Cream stays as the light
    alternative. Both placements (sidebar + landing) sync via the shared
    ``theme`` state, re-seeded from it each render."""
    st.session_state[key] = (st.session_state.get("theme", "dark") == "dark")

    def _cb():
        st.session_state.theme = "dark" if st.session_state[key] else "cream"

    st.toggle("🌙 Night mode", key=key, on_change=_cb,
              help="Vintage cream by default — flip to dark if you prefer.")


def section_header(text: str, icon: str = "", tag: str = "") -> None:
    """One consistent section header across the whole app: an uppercase kicker
    with an accent underline (replaces the mix of #####/st.subheader/#### )."""
    ico = f"<span class='ico'>{icon}</span>" if icon else ""
    tg = f"<span class='tag'>{tag}</span>" if tag else ""
    st.markdown(f"<div class='osp-sec'>{ico}<span>{text}</span>{tg}</div>",
                unsafe_allow_html=True)


def _hr_color(v):
    """Heatmap color for a 0-1 hit rate (green = hits the line, red = misses)."""
    if v is None or pd.isna(v):
        return ("var(--faint)", "rgba(93,104,120,.12)")
    if v >= 0.60:
        return ("var(--good)", "rgba(46,226,122,.14)")
    if v >= 0.50:
        return ("var(--mid)", "rgba(240,180,41,.14)")
    return ("var(--neg)", "rgba(255,93,99,.14)")


def hit_rate_chips(chips: dict) -> None:
    """Render L5/L10/... hit rates as green→red heatmap chips (matches the
    props-table convention) instead of plain st.metric tiles."""
    cells = []
    for k, v in chips.items():
        col, bg = _hr_color(v)
        val = f"{v * 100:.0f}%" if (v is not None and pd.notna(v)) else "—"
        cells.append(
            f"<div style='flex:1;text-align:center;border-radius:10px;padding:8px 4px;"
            f"background:{bg};border:1px solid {col}3a;'>"
            f"<div style='font-size:.6rem;text-transform:uppercase;letter-spacing:.3px;"
            f"opacity:.65;'>{k}</div>"
            f"<div style='font-size:1.1rem;font-weight:700;color:{col};'>{val}</div></div>")
    st.markdown("<div style='display:flex;gap:6px;margin:2px 0 6px;'>"
                + "".join(cells) + "</div>", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def load_data() -> dict | None:
    path = config.OUTPUT_DIR / "latest.json"
    return json.loads(path.read_text()) if path.exists() else None


@st.cache_data(ttl=300)
def load_ledger() -> list[dict]:
    return results.load_ledger()


@st.cache_data(ttl=300)
def gate_table() -> dict:
    """Per-(sport, market) demonstrated-edge status for the bet-ticket badges.
    Cached (ttl 300s) so the scroll feed's many cards share one build."""
    try:
        from project547 import edge_gate
        return edge_gate.gate_table()
    except Exception:
        return {}


def refresh():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from project547 import pipeline

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


def ev_styler(df: pd.DataFrame, ev_cols: list[str], tier_col: str | None = None):
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
    sty = df.style.map(color, subset=[c for c in ev_cols if c in df.columns]) \
                  .format(fmt, na_rep="—")
    # Meaning-tint whole rows by tier: CORE PLAY faint green, VERIFY faint amber.
    # Display-only (Styler), so the underlying frame stays numeric/sortable.
    if tier_col and tier_col in df.columns:
        tints = {"CORE PLAY": "background-color: rgba(0,230,118,0.10);",
                 "VERIFY": "background-color: rgba(255,176,32,0.12);"}

        def tint_row(row):
            css = tints.get(str(row.get(tier_col, "")), "")
            return [css] * len(row)

        sty = sty.apply(tint_row, axis=1)
    return sty


# ---------------------------------------------------------------------------
# Sidebar: brand, nav, settings
# ---------------------------------------------------------------------------

data = load_data()
slates = slates_by_date(data) if data else {}

NAV_SPORTS = [s for s in ("MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL",
                          "MLS", "ATP") if s in SPORTS]
# Player-/goal-based match models (no team offense-vs-defense card, no prop
# board) — routed to a dedicated renderer instead of render_sport.
MATCH_MODEL_SPORTS = {"MLS", "EPL", "ATP", "WTA"}

_SPORT_LABELS = {"MLB": "MLB", "WNBA": "WNBA", "NBA": "NBA", "NHL": "NHL",
                 "NFL": "NFL", "NCAAF": "NCAAF", "MLS": "Soccer", "ATP": "Tennis"}

# Flat navigation (like the reference site): the in-season sports are top-level,
# one click each — no "Sports" sub-menu to dig through. Offseason sports + the
# lab/ledger pages live under "More". Section codes preserved for the dispatch.
from datetime import date as _date_cls  # noqa: E402
try:
    from project547.sports import active_sports as _active_sports
    _NAV_ACTIVE = [s for s in NAV_SPORTS if s in _active_sports(_date_cls.today().isoformat())]
except Exception:
    _NAV_ACTIVE = list(NAV_SPORTS)
_NAV_INACTIVE = [s for s in NAV_SPORTS if s not in _NAV_ACTIVE]
NAV_GROUPS = {
    "🏠 Today": ["HOME"],
    "🎯 Plays": ["PLAYS"],  # Board/Full table/Edge scanner are an in-page mode toggle
    **{_SPORT_LABELS.get(s, s): [s] for s in _NAV_ACTIVE},  # in-season sports, flat
    "📊 Scores": ["SCORES"],
    # SHARPSHEET browser retired — the Sharp Sheet now lives on each sport page.
    "⋯ More": _NAV_INACTIVE + ["OTHER_SPORTS", "DFS", "PERFORMANCE", "EDGE_BUILDER",
               "EXPERTS", "TOOLS", "BACKTEST", "PROMPT_ENGINE", "WORKBOOK", "DOCS"],
}
_PAGE_LABELS = {"HOME": "Today", "PLAYS": "Curated plays",
                "PLAYS_DETAIL": "Plays — full table", "EDGES": "Edge scanner",
                "DFS": "DFS optimizer", "SCORES": "Live scores",
                "PERFORMANCE": "Track record", "SHARPSHEET": "Sharp Sheets",
                "OTHER_SPORTS": "Other sports", "BACKTEST": "Backtest",
                "EXPERTS": "Expert consensus", "TOOLS": "Calculators",
                "DOCS": "Methodology & docs", "WORKBOOK": "📥 Daily workbook",
                "EDGE_BUILDER": "Edge Builder", "PROMPT_ENGINE": "Prompt Engine",
                **_SPORT_LABELS}

# Responsive-nav handoff. The mobile top strip (rendered later, CSS-shown only
# on narrow viewports) can't mutate the sidebar radio keys after those widgets
# instantiate this run — so it stashes the pick in `_pending_nav` and reruns.
# Apply it here, before the sidebar radios below read their keys.
_pending_nav = st.session_state.pop("_pending_nav", None)
if _pending_nav:
    _pa = _pending_nav.get("area")
    if _pa in NAV_GROUPS and NAV_GROUPS[_pa]:
        st.session_state["nav_area"] = _pa
        _ps = _pending_nav.get("section")
        if _ps and _ps in NAV_GROUPS[_pa]:
            st.session_state[f"nav_{_pa}"] = _ps

@st.cache_data(show_spinner=False)
def _build_stamp() -> str:
    """Short git SHA of the running checkout, so the sidebar shows exactly which
    commit is live. The stamp's mere presence also confirms the new build
    deployed (the old build has no stamp)."""
    import os
    import subprocess
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=3).stdout.strip()
        if sha:
            return sha
    except Exception:
        pass
    return os.environ.get("BUILD_SHA", "")


def _nav_display(label: str) -> str:
    """Strip a leading emoji/symbol token for display ("🎯 Plays" → "Plays");
    plain labels like "MLB" pass through. Keeps NAV_GROUPS keys (the routing
    codes) intact while the sidebar reads clean like the reference."""
    parts = label.split(" ", 1)
    if len(parts) == 2 and not parts[0][:1].isascii():
        return parts[1]
    return label


with st.sidebar:
    st.markdown(
        "<div class='osp-logo'><span class='mk'>◈</span>"
        "<span class='osp-brand'>360Five</span></div>",
        unsafe_allow_html=True)
    area = st.radio("Section", [g for g in NAV_GROUPS if NAV_GROUPS[g]],
                    label_visibility="collapsed", key="nav_area",
                    format_func=_nav_display)
    pages = NAV_GROUPS[area]
    if len(pages) == 1:
        section = pages[0]
        # Legacy deep-links / smoke cases may still carry a sub-page code for a
        # now-collapsed area (e.g. EDGES under Plays). Honour it as the in-page
        # mode default so the right view opens, without showing a nav row.
        _legacy = st.session_state.get(f"nav_{area}")
        if _legacy in ("PLAYS_DETAIL", "EDGES") and section == "PLAYS":
            st.session_state.setdefault("plays_mode_code", _legacy)
    else:
        section = st.radio(
            area, pages, label_visibility="collapsed", key=f"nav_{area}",
            format_func=lambda p: _PAGE_LABELS.get(p, p.title()))

    # Bankroll + filters live under a collapsed control so the nav reads clean;
    # they still drive the board and every card exactly as before.
    with st.expander("⚙ Filters & bankroll", expanded=False):
        _theme_toggle("theme_sb")
        min_edge = st.slider("Min edge (EV)", 0.0, 0.15, config.MIN_EDGE, 0.005,
                             format="%.3f")
        bankroll = st.number_input("Bankroll ($)", min_value=0, value=1000, step=100)
        show_all = st.checkbox("Show rows without edges", value=False)
        hide_wild = st.checkbox("Hide implausible edges (≥30%)", value=True)
        st.caption(f"Stakes are {config.KELLY_FRACTION:.0%}-Kelly × bankroll.")
        credits = (data or {}).get("odds_api_credits")
        if credits is not None:
            st.caption(f"📊 Odds API: {int(credits):,} credits left")
        _sha = _build_stamp()
        st.caption(f"⬢ Terminal build{f' · {_sha}' if _sha else ''}")
        if st.button("↻ Refresh", width="stretch"):
            refresh()
            st.rerun()

    st.markdown(
        "<div class='osp-acct'><span class='av'>E</span>"
        "<span class='nm'>EdgeCash</span></div>",
        unsafe_allow_html=True)

if not slates:
    st.title("🎯 360Five")
    st.caption("360° of research. 5 questions. 365 days.")
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


def ai_block(brief: str, key: str):
    """'Send to AI' panel. The free path is primary: copy the markdown brief and
    paste it into Claude.ai (or any chatbot) on your own subscription — no API
    cost. The in-app Claude analysis is offered second, clearly marked as a paid
    API call, and only when a key is configured. The analysis persists across
    reruns via session_state so it doesn't vanish on the next interaction."""
    from project547 import ai_modes
    with st.expander("🤖 Send to AI  ·  free copy-paste"):
        # Risk mode (ported from Edge Equation): Conservative / Standard /
        # Aggressive change the betting *posture* of the read — selectivity,
        # stake ladder, prop discipline, edge floor — not the numbers.
        mode = st.radio(
            "AI risk mode", list(ai_modes.MODE_ORDER),
            index=ai_modes.MODE_ORDER.index(ai_modes.DEFAULT_MODE),
            format_func=ai_modes.label, horizontal=True, key=f"ai_mode_{key}",
            help="How aggressive the AI's bet recommendations are. Applies to "
                 "both the copy-paste brief and the in-app read.")
        st.caption(ai_modes.MODES[mode][1])
        full_brief = f"{brief}\n\n{ai_modes.MODES[mode][3]}"
        st.caption("**Free** — copy this brief and paste it into Claude.ai or "
                   "any chatbot. Uses your own subscription; no API charge.")
        st.code(full_brief, language="markdown")
        st.caption("Hover the box and click ⧉ to copy.")
        ready, _ = ai.available()
        if ready:
            out_key = f"ai_out_{key}"
            st.divider()
            c1, c2 = st.columns([1, 3])
            if c1.button("✨ Analyze in-app", key=f"ai_btn_{key}",
                         help="Optional: runs Claude via the Anthropic API "
                              "(~5¢/click, billed to your API key — NOT your "
                              "Claude subscription)."):
                try:
                    with st.spinner("Claude is reading the brief…"):
                        st.session_state[out_key] = ai.analyze(brief, mode=mode)
                except Exception as e:  # network/auth/rate-limit — show, don't crash
                    st.session_state[out_key] = f"⚠️ AI analyst error: {e}"
            c2.caption("Optional paid one-click read (Anthropic API, ~5¢).")
            if st.session_state.get(out_key):
                st.markdown(st.session_state[out_key])


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
        st.caption(f"⚠️ Some {sport} data is temporarily unavailable "
                   f"({blob['error']}). It refreshes on the next update.")
    if not games and not props:
        nxt = next((d for d in dates if d != date_sel
                    and (slates.get(d, {}).get(sport, {}).get("games")
                         or slates.get(d, {}).get(sport, {}).get("props"))), None)
        msg = f"No {sport} games scheduled for {date_sel}."
        if nxt:
            msg += f" The **{nxt}** slate has {sport} games — switch dates above."
        st.info(msg)
        return

    tab_g, tab_p = st.tabs([f"📅 Games ({len(games)})", f"👤 Props ({len(props)})"])

    with tab_g:
        shown = [g for g in games if not q or q in
                 f"{g.get('home_team','')} {g.get('away_team','')}".lower()]
        if not shown:
            st.info("No games match the search." if q else "No games.")
        else:
            # One recency control for the whole feed (was a radio per card).
            confirmed = sum(ui.lineup_status(sport, g)["state"] == "confirmed"
                            for g in shown)
            top = st.columns([3, 2])
            top[0].caption(f"🔔 {confirmed} of {len(shown)} lineups confirmed — "
                           "edges and props firm up as lineups post.")
            with top[1]:
                window = st.radio(
                    "Recency window", list(teamstats.WINDOWS), index=0,
                    horizontal=True, label_visibility="collapsed",
                    format_func=lambda w: teamstats.WINDOW_LABELS[w],
                    key=f"win_{sport}_{date_sel}",
                    help="How much recency to weight — ranks, advantages and "
                         "strength of schedule recompute for the window you pick.")
            # The feed: one collapsible Sharp Sheet per game. The label shows the
            # matchup + the single best call, so a bettor scans the whole slate at
            # a glance and only opens the games worth a full read. Games with a
            # live PLAY/LEAN auto-expand; the rest stay tucked.
            gt = gate_table()
            st.caption("🟢 play · 🟡 lean · ⚪ pass — tap a game for its full Sharp Sheet.")
            for g in shown:
                label, auto = ui.sheet_headline(
                    sport, g, min_edge=min_edge, gate_table=gt, bankroll=bankroll)
                with st.expander(label, expanded=auto):
                    # Never let one game's data-build error blank the feed —
                    # degrade to DATA GAP chips (empty data) and keep rendering.
                    try:
                        m = _matchup(sport, g.get("home_team", ""),
                                     g.get("away_team", ""), date_sel, window) or {}
                        data = _sheet_data(sport, g, m, date_sel)
                    except Exception:
                        log.exception("sheet data failed for %s @ %s",
                                      g.get("away_team"), g.get("home_team"))
                        m, data = {}, {}
                    st.markdown(
                        ui.sharp_sheet_html(
                            sport, g, m, window=window, min_edge=min_edge,
                            gate_table=gt, bankroll=bankroll,
                            props=props, best_line=_best_line_for(sport, g, date_sel),
                            data=data),
                        unsafe_allow_html=True)

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


def _match_title(sport: str, g: dict) -> str:
    if sport in ("ATP", "WTA"):
        return f"{g.get('player1', '')} vs {g.get('player2', '')}"
    return f"{g.get('away_team', '')} @ {g.get('home_team', '')}"


def _match_sheet(sport: str, g: dict):
    """Sharp Sheet body for a soccer/tennis match: the model's read + the priced
    sides. No team offense/defense splits — these are goal-/Elo-based models."""
    is_tennis = sport in ("ATP", "WTA")
    if is_tennis:
        cols = st.columns(3)
        cols[0].metric(g.get("player1", "P1"), ui._pct(g.get("player1_win_prob")))
        cols[1].metric(g.get("player2", "P2"), ui._pct(g.get("player2_win_prob")))
        cols[2].metric("Surface", str(g.get("surface") or "—").title())
        st.caption(f"{g.get('tournament', '')} · surface-aware player Elo "
                   f"(sample: {g.get('p1_matches', '—')} / "
                   f"{g.get('p2_matches', '—')} matches).")
    else:
        cols = st.columns(4)
        cols[0].metric("Home win", ui._pct(g.get("home_win_prob")))
        cols[1].metric("Draw", ui._pct(g.get("draw_prob")))
        cols[2].metric("Away win", ui._pct(g.get("away_win_prob")))
        cols[3].metric("Over 2.5", ui._pct(g.get("over_2_5")))
        st.caption(f"Expected goals {ui._num(g.get('home_exp'))} "
                   f"(home) – {ui._num(g.get('away_exp'))} (away), "
                   "Dixon-Coles-corrected Poisson scoreline model.")
    edges = ui.match_edge_table(sport, g)
    if edges.empty:
        st.caption("No market prices yet — odds arrive from The Odds API "
                   "(soccer books; tennis during the majors). The model's "
                   "probabilities above stand on their own until then.")
    else:
        st.markdown("**Priced sides** — model vs the market:")
        st.dataframe(edges, hide_index=True, width="stretch")
        st.caption("EV is after shrinking the model toward the de-vigged "
                   "market; new markets sit in the edge gate's probation tier "
                   "until they prove CLV.")


def render_match_sport(sport: str):
    """Soccer / tennis: a match-model slate (1X2 + totals for soccer, player
    match-win for tennis) with a Sharp Sheet per match. No prop board."""
    q = topbar(sport)
    date_sel = pick_date()
    blob = slates.get(date_sel, {}).get(sport, {})
    games = blob.get("games", []) or []
    if blob.get("error"):
        st.caption(f"⚠️ Some {sport} data is temporarily unavailable "
                   f"({blob['error']}). It refreshes on the next update.")
    if q:
        games = [g for g in games if q in _match_title(sport, g).lower()]
    if not games:
        st.info(f"No {sport} matches for {date_sel}"
                + (" matching the search." if q else "."))
        return
    is_tennis = sport in ("ATP", "WTA")
    st.caption(("Surface-aware player Elo; win % is the Elo logistic. "
                if is_tennis else
                "1X2 + totals from a Dixon-Coles Poisson on each side's expected "
                "goals. ") + "Odds/EV appear where The Odds API covers the match.")
    # Collapsible Sharp Sheet per match (mirrors the team-sport feed): the label
    # carries the matchup + best call so the slate scans at a glance.
    st.caption("🟢 play · 🟡 lean · ⚪ pass — tap a match for its full Sharp Sheet.")
    _style = st.radio(
        "Card style", ["Sharp Sheet", "Terminal (beta)"], index=0, horizontal=True,
        key=f"style_match_{sport}_{date_sel}",
        help="Terminal is the new interactive 360Five card — Ask AI, Share view.")
    use_terminal = _style.startswith("Terminal")
    if use_terminal:
        import streamlit.components.v1 as components
        from app import terminal_card
    for g in games:
        label, auto = ui.sheet_headline(sport, g, min_edge=min_edge,
                                        gate_table=gate_table(), bankroll=bankroll)
        with st.expander(label, expanded=auto):
            if use_terminal:
                components.html(terminal_card.terminal_card_html(sport, g, {}, []),
                                height=900, scrolling=True)
            else:
                st.markdown(
                    ui.match_sheet_html(sport, g,
                                        best_line=_best_line_for(sport, g, date_sel)),
                    unsafe_allow_html=True)


@st.cache_data(ttl=900, show_spinner=False)
def _matchup(sport: str, home: str, away: str, asof: str, window: str = "l5") -> dict:
    try:
        return teamstats.matchup(sport, home, away, asof, window=window)
    except Exception:
        return {}


def _bp_validator(sport: str, g: dict, date_sel: str):
    """BettingPros as a validator on a modeled matchup: compare our model's home
    win prob to BP's de-vigged consensus, and show BP's prop second opinion.
    Defensive — BP errors/no-coverage just render nothing."""
    from project547 import baseline as _baseline
    from project547 import bp as _bp
    bp_sport = _bp.modeled_bp_sport(sport)
    if not bp_sport:
        return
    home, away = g.get("home_team", ""), g.get("away_team", "")
    bpb = _bp.game_baseline(bp_sport, date_sel, home, away)
    our = g.get("home_win_prob")
    if bpb:
        if isinstance(our, (int, float)):
            st.markdown(f"**🔍 BettingPros check** — model **{our*100:.0f}%** vs "
                        f"BP **{bpb.home_wp*100:.0f}%** (home win)")
            c = _baseline.compare(our, bpb.home_wp)
            st.caption("✅ We agree with BettingPros — higher confidence."
                       if c["agree"] else
                       f"⚠️ We diverge from BettingPros — {c['label']}. "
                       "That's our edge or a data check, not a coin flip.")
        else:
            st.markdown(f"**🔍 BettingPros baseline** — home "
                        f"{bpb.home_wp*100:.0f}% / away {bpb.away_wp*100:.0f}%")
    props = _bp.prop_projections(bp_sport, date_sel, teams=[home, away], limit=8)
    if props:
        with st.expander(f"📊 BettingPros prop projections ({len(props)}) — "
                         "their model, our second opinion"):
            st.dataframe(pd.DataFrame([{
                "Player": p["participant"], "Line": p["bp_line"],
                "BP proj": p["bp_projection"],
                "EV%": (f"{p['bp_ev']*100:+.1f}"
                        if isinstance(p["bp_ev"], (int, float)) else None),
                "Side": p["bp_recommended_side"], "Rating": p["bp_bet_rating"],
            } for p in props]), hide_index=True, width="stretch")


def render_research_card(sport: str, g: dict, date_sel: str, caption: bool = True):
    away, home = g.get("away_team", ""), g.get("home_team", "")
    wkey = f"win_{sport}_{date_sel}_{away}_{home}"
    window = st.radio(
        "Recency window", list(teamstats.WINDOWS), index=0, horizontal=True,
        format_func=lambda w: teamstats.WINDOW_LABELS[w], key=wkey,
        help="How much recency to weight — ranks, advantages and strength of "
             "schedule all recompute for the window you pick.")
    m = _matchup(sport, home, away, date_sel, window) or {}
    props = slates.get(date_sel, {}).get(sport, {}).get("props", []) or []
    # Terminal card (beta) — the new interactive 360Five research card, mounted
    # via components.html so its JS (Research↔Share, prop drawers, Ask AI) runs.
    # MLB-only for now (the stat-matrix section labels are MLB-specific); A/B
    # against the Sharp Sheet so the live card path is untouched.
    _style = st.radio(
        "Card style", ["Sharp Sheet", "Terminal (beta)"], index=0,
        horizontal=True, key=f"style_{wkey}",
        help="Terminal is the new interactive research card — Research↔Share, "
             "tappable prop drawers, and one-click Ask AI.")
    if _style.startswith("Terminal"):
        import streamlit.components.v1 as components
        from app import terminal_card
        components.html(terminal_card.terminal_card_html(sport, g, m, props),
                        height=1700, scrolling=True)
        return
    st.markdown(ui.sharp_sheet_html(sport, g, m, window=window, min_edge=min_edge,
                                    gate_table=gate_table(), bankroll=bankroll,
                                    props=props, best_line=_best_line_for(sport, g, date_sel),
                                    data=_sheet_data(sport, g, m, date_sel)),
                unsafe_allow_html=True)
    if caption:
        st.caption(f"Offense {teamstats.WINDOW_LABELS[window]} vs the opponent's "
                   "matching defense; rank pills are league ranks (green = top "
                   "third). ★ = the offense out-ranks the defense it faces.")


@st.cache_data(ttl=300, show_spinner=False)
def _market_stats_cached() -> dict:
    """edge_gate.market_stats() with string keys (JSON-cacheable)."""
    try:
        from project547 import edge_gate
        return {f"{s}|{m}": v for (s, m), v in edge_gate.market_stats().items()}
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def _market_calibration(sport: str) -> dict | None:
    """Per-market {pred, actual, n} for the calibration receipt: predicted =
    mean logged model_prob for the side bet; actual = realized hit-rate; from the
    graded ledger. Markets without a logged model_prob (older rows / run line,
    which isn't graded yet) fall back to actual-only + a DATA GAP on predicted."""
    try:
        from project547 import results
        led = [r for r in results.load_ledger()
               if r.get("sport") == sport and r.get("won") is not None]
    except Exception:
        return None
    ms = _market_stats_cached()
    out, any_data = {}, False
    for label, mk in (("Moneyline", "moneyline"), ("Total", "total"), ("Run Line", "spread")):
        rows = [r for r in led if r.get("market") == mk
                and isinstance(r.get("model_prob"), (int, float))]
        if rows:
            pred = sum(r["model_prob"] for r in rows) / len(rows)
            actual = sum(1 for r in rows if r["won"]) / len(rows)
            out[label] = {"pred": round(pred, 4), "actual": round(actual, 4), "n": len(rows)}
            any_data = True
        else:
            s = ms.get(f"{sport}|{mk}") or {}
            out[label] = {"pred": None, "actual": s.get("win_rate"), "n": s.get("n")}
            any_data = any_data or s.get("win_rate") is not None
    return out if any_data else None


@st.cache_data(ttl=3600, show_spinner=False)
def _pitcher_stats(season: int) -> dict:
    """{norm_name: FanGraphs pitching row} for filling the Sharp Sheet's pitching
    strip (IP, K/9, BB/9, xFIP). Empty if the table can't be built."""
    try:
        from project547 import pipeline
        df = pipeline._pitcher_table(season)
        return {r.get("norm_name"): r for r in df.to_dict("records")
                if r.get("norm_name")}
    except Exception:
        return {}


def _sheet_data(sport: str, g: dict, matchup: dict, date_sel: str) -> dict:
    """Assemble the Sharp Sheet v2 ``data`` object from live sources. Missing
    pieces stay absent so the composer renders DATA GAP chips (never fabricated)."""
    ms = _market_stats_cached()
    clv = {}
    for mk in ("moneyline", "total", "spread"):
        s = ms.get(f"{sport}|{mk}") or {}
        if s.get("clv_n"):
            clv[mk] = {"avg_clv": s.get("avg_clv"), "clv_n": s.get("clv_n")}
    pitching = None
    if sport == "MLB":
        from project547 import platoon
        from project547.names import normalize
        try:
            from project547.pipeline import starter_xfip, _lookup_float as _lf
        except Exception:
            starter_xfip = lambda *_: None       # noqa: E731
            _lf = lambda row, *ks: None           # noqa: E731
        pstats = _pitcher_stats(int(str(date_sel)[:4]))
        pitching = {}
        for side in ("away", "home"):
            nm, pid = g.get(f"{side}_pitcher"), g.get(f"{side}_pitcher_id")
            # TBD/missing starters come back as pandas NaN (a truthy float), so
            # guard on the actual string — otherwise NaN reaches normalize().
            if not isinstance(nm, str) or not nm.strip():
                continue
            row = pstats.get(normalize(nm)) or {}
            ip_tot, gs = _lf(row, "IP"), _lf(row, "GS")
            ip = (ip_tot / gs) if ip_tot and gs else None   # innings per start
            k9, bb9 = _lf(row, "K/9", "K9"), _lf(row, "BB/9", "BB9")
            xfip = _lf(row, "xFIP", "FIP")
            if xfip is None:
                try:
                    xfip = starter_xfip(nm)
                except Exception:
                    xfip = None
            try:
                hand = platoon.throws(nm, pid)
            except Exception:
                hand = None
            pitching[f"{side}_sp"] = {
                "name": nm, "id": pid, "hand": hand, "xfip": xfip, "ip": ip,
                "k9": k9, "bb9": bb9,
                # a starter who averages 3 turns through the order (~5.8+ IP)
                "tto_flag": bool(ip and ip >= 5.8)}
            bp = (matchup or {}).get(f"{side}_bullpen") or {}
            if bp:
                pitching[f"{side}_bullpen"] = {"fatigue": bp.get("level"),
                                               "proj_ip": bp.get("proj_ip")}
    return {"clv": clv or None, "calibration": _market_calibration(sport),
            "pitching": pitching or None}


def _best_line_for(sport: str, g: dict, date_sel: str) -> dict:
    """Best available price per market for the Sharp Sheet's odds strip, shaped
    {label: {price, book}} from the line-shop. Empty when no coverage."""
    from project547.names import normalize
    try:
        best = load_best_lines(sport, date_sel)
    except Exception:
        return {}
    key = " vs ".join(sorted({normalize(g.get("home_team", "")),
                              normalize(g.get("away_team", ""))}))
    rec = best.get(key)
    if not rec:
        return {}
    out: dict = {}
    ml = rec.get("moneyline") or {}
    for side in ("away_team", "home_team"):
        info = ml.get(normalize(g.get(side, "")))
        if info:
            out[f"{g.get(side, '').split()[-1]} ML"] = {
                "price": info.get("price"), "book": info.get("book")}
    tot = rec.get("total") or {}
    for side in ("over", "under"):
        info = tot.get(side)
        if info:
            ln = f" {info['line']:g}" if info.get("line") is not None else ""
            out[f"{side.title()}{ln}"] = {"price": info.get("price"), "book": info.get("book")}
    return out


@st.cache_data(ttl=300, show_spinner=False)
def load_best_lines(sport: str, date_sel: str) -> dict:
    from project547 import lineshop
    # frozenset keys aren't JSON-friendly for caching, so stringify them
    return {" vs ".join(sorted(k)): v
            for k, v in lineshop.best_lines(sport, date_sel).items()}


def _shop_line(sport: str, g: dict, date_sel: str):
    from project547.names import normalize
    best = load_best_lines(sport, date_sel)
    key = " vs ".join(sorted({normalize(g.get("home_team", "")),
                              normalize(g.get("away_team", ""))}))
    rec = best.get(key)
    if not rec:
        return

    def book(b):
        return str(b).replace("_", " ").title()

    bits = []
    ml = rec.get("moneyline") or {}
    for side in ("away_team", "home_team"):
        info = ml.get(normalize(g.get(side, "")))
        if info:
            short = g.get(side, "").split()[-1]
            bits.append(f"{short} ML {ui.fmt_american(info['price'])} "
                        f"({book(info['book'])})")
    tot = rec.get("total") or {}
    for side in ("over", "under"):
        info = tot.get(side)
        if info:
            ln = f" {info['line']:g}" if info.get("line") is not None else ""
            bits.append(f"{side.title()}{ln} {ui.fmt_american(info['price'])} "
                        f"({book(info['book'])})")
    if bits:
        st.markdown("🛒 **Best available:** " + " · ".join(bits))


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
    if "Def Rk" in view.columns and view["Def Rk"].notna().any():
        # opponent rank defending this stat: high rank = soft matchup = green
        styler = styler.background_gradient(cmap="RdYlGn", subset=["Def Rk"]) \
                       .format({"Def Rk": "{:.0f}"}, na_rep="—")
    sel = st.dataframe(styler, width="stretch", hide_index=True, height=480,
                       on_select="rerun", selection_mode="single-row",
                       key=f"props_table_{sport}")
    st.caption("👆 Tap a row for the player deep-dive. L5/L10/L20/Season/H2H "
               "= how often the player has gone OVER the line (our game "
               "logs). **Def Rk** = where the opponent ranks defending this "
               "stat (green = softer matchup). bp_* are BettingPros' consensus.")

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
        f"<div style='color:var(--muted);font-size:0.8rem;'>{sub}</div></div></div>",
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
            hit_rate_chips(chips)
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
        from project547.names import normalize as _norm
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

    corr = p.get("correlated_picks") or []
    if isinstance(corr, list) and corr:
        with st.expander(f"🔗 Correlated picks for an SGP ({len(corr)})"):
            for c in corr:
                if not isinstance(c, dict):
                    continue
                parts = [f"**{c.get('player') or '—'}**"]
                if c.get("market"):
                    parts.append(str(c["market"]))
                if c.get("side"):
                    parts.append(str(c["side"]).title())
                if c.get("line") is not None and pd.notna(c["line"]):
                    parts.append(f"{c['line']:g}")
                if c.get("odds") is not None and pd.notna(c["odds"]):
                    parts.append(ui.fmt_american(c["odds"]))
                r = c.get("correlation")
                tail = f" · ρ≈{r:+.2f}" if (r is not None and pd.notna(r)) else ""
                st.markdown("- " + " ".join(parts) + tail)
            st.caption("BettingPros' correlated-leg suggestions — pair with this "
                       "prop in a same-game parlay, then price it in "
                       "**Tools → Parlay & Correlation**.")

    ai_block(ui.ai_brief_prop(sport, p),
             key=f"prop_{sport}_{p.get('player','')}_{p.get('market','')}_{p.get('line','')}")


def _player_profile(sport: str, player: str, market: str) -> str | None:
    """Season profile line (MLB: box-log rates + prior-season Statcast
    expected stats)."""
    if sport != "MLB":
        return None
    try:
        from project547 import internal_stats
        from project547.names import normalize as _norm
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

def render_plays_detail():
    q = topbar("Plays — full table")
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
    games = board[board["type"] == "Game"]
    props = board[board["type"] == "Prop"]
    c = st.columns(4)
    c[0].metric("Edges", len(board))
    c[1].metric("Games", len(games))
    c[2].metric("Props", len(props))
    c[3].metric("Best EV", f"{board['ev'].max():+.1%}")

    # line shopping: best available price + book per bet from multi-book odds
    from project547 import lineshop
    shop = ({sp: lineshop.best_lines(sp, date_sel)
             for sp in board["sport"].unique()} if not board.empty else {})

    def _shop(r):
        # prop rows leave these as NaN floats; `not NaN` is False, so guard on
        # the type, not truthiness, or normalize() blows up on a float.
        mkt, home, away = r.get("_shop_mkt"), r.get("_home"), r.get("_away")
        if not (isinstance(mkt, str) and isinstance(home, str)
                and isinstance(away, str)):
            return pd.Series([None, None])
        hit = lineshop.lookup(shop.get(r["sport"], {}), home, away, mkt,
                              r.get("_sidekey"))
        return pd.Series([hit["price"], hit["book"]] if hit else [None, None])

    def render_board(sub, key, kind):
        if sub.empty:
            st.info(f"No {kind} edges clear the {min_edge:.1%} bar on {date_sel}.")
            return
        view = sub.copy()
        if {"_shop_mkt", "_home", "_away", "_sidekey"}.issubset(view.columns):
            view[["_best_price", "_best_book"]] = view.apply(_shop, axis=1)
        else:
            view["_best_price"], view["_best_book"] = None, None
        has_shop = view["_best_price"].notna().any()
        view["best"] = [
            f"{ui.fmt_american(p)} ({str(b).replace('_', ' ').title()})"
            if p is not None and pd.notna(p) else "—"
            for p, b in zip(view["_best_price"], view["_best_book"])]

        # Tier label up front (CORE PLAY / VERIFY / …) from the same ladder the
        # cards use — classify off the raw EV fraction before we rescale to %.
        view["tier"] = [ui.play_tier(e, min_edge)["label"]
                        for e in pd.to_numeric(sub["ev"], errors="coerce")]
        view["price"] = view["price"].map(ui.fmt_american)
        view["model_prob"] = pd.to_numeric(view["model_prob"], errors="coerce") * 100
        view["ev"] = pd.to_numeric(view["ev"], errors="coerce") * 100
        stake_ev = (view["ev"] / 100 * config.KELLY_FRACTION).clip(lower=0)
        stake_ev[view["ev"] >= 30] = 0  # no stake on implausible edges
        view["stake"] = (pd.to_numeric(view["kelly"], errors="coerce")
                         .fillna(stake_ev) * bankroll).round(0)
        view["time"] = view["time"].map(ui.fmt_time_et)
        # Lead with the decision-relevant columns; secondary detail (matchup,
        # tip-off, line-shop price, notes) hides behind a toggle to cut clutter.
        more = st.checkbox("More columns", value=False, key=f"more_{key}",
                           help="Show matchup, time, line-shop price and notes.")
        lead = ["tier", "sport", "bet", "price", "model_prob", "ev", "stake"]
        extra = ["game", "time"]
        if has_shop:
            extra.append("best")
        if "flag" in view.columns and view["flag"].astype(bool).any():
            extra.append("flag")
        cols = lead + (extra if more else [])
        view = view[cols].rename(columns={
            "tier": "Tier", "sport": "Sport", "bet": "Bet", "game": "Game",
            "time": "Time", "price": "Price", "best": "Best (book)",
            "model_prob": "Model %", "ev": "EV %", "stake": "Stake $",
            "flag": "Note"})
        st.dataframe(
            ev_styler(view, ["EV %"], tier_col="Tier"), width="stretch",
            hide_index=True, height=600,
            column_config={
                "Model %": st.column_config.ProgressColumn(
                    "Model %", min_value=0, max_value=100, format="%.0f%%"),
                "Stake $": st.column_config.NumberColumn(format="$%d")})
        shop_note = (" **Best (book)** is the top price across books — shopping "
                     "the best number is the most reliable edge there is."
                     if (more and has_shop) else "")
        st.caption("Sorted by EV (high → low). CORE-PLAY rows tint green, "
                   "VERIFY (edge too big — market likely knows) amber. "
                   "Stake = ¼-Kelly × bankroll." + shop_note)
        ai_block(ui.ai_brief_board(sub, date_sel), key=key)

    tab_g, tab_p = st.tabs([f"🏟️ Game plays ({len(games)})",
                            f"🎯 Props ({len(props)})"])
    with tab_g:
        render_board(games, f"board_games_{date_sel}", "game")
    with tab_p:
        st.caption("Single props — sortable; click the **EV %** header for a "
                   "quick high-to-low EV check.")
        render_board(props, f"board_props_{date_sel}", "prop")


# ---------------------------------------------------------------------------
# Sharp Sheet — the deep matchup breakdown behind a play (research starter)
# ---------------------------------------------------------------------------

def _resolve_game(sport: str, r, date_sel: str) -> dict | None:
    """The slate game dict behind a board row (game or prop), or None."""
    from project547.names import normalize
    games = slates.get(date_sel, {}).get(sport, {}).get("games", []) or []
    h, a = r.get("_home"), r.get("_away")
    if isinstance(h, str) and isinstance(a, str):
        names = {normalize(h), normalize(a)}
    else:  # prop rows carry "Team vs Opponent" in `game`
        names = {normalize(x) for x in str(r.get("game", "")).split(" vs ")}
    names.discard("")
    for g in games:
        gnames = {normalize(g.get("home_team", "")), normalize(g.get("away_team", "")),
                  normalize(g.get("player1", "")), normalize(g.get("player2", ""))}
        gnames.discard("")
        if gnames & names:
            return g
    return {"home_team": h, "away_team": a} if isinstance(h, str) else None


@st.dialog("📊 Sharp Sheet", width="large")
def _sharpsheet(sport: str, g: dict | None, date_sel: str):
    if not g:
        st.info("Couldn't resolve the matchup behind this play.")
        return
    st.markdown(f"### {_match_title(sport, g)}")
    if sport in MATCH_MODEL_SPORTS:
        st.caption(f"{sport} · {date_sel} · the model's read and the priced sides.")
        _match_sheet(sport, g)
        return
    st.caption(f"{sport} · {date_sel} · the full breakdown behind the play — "
               "offense vs defense, the edges, and the model's read.")
    render_research_card(sport, g, date_sel)


_PLAYS_MODES = ("◆ Board", "Full table", "Edge scanner")
_PLAYS_MODE_BY_CODE = {"PLAYS": "◆ Board", "PLAYS_DETAIL": "Full table",
                       "EDGES": "Edge scanner"}


def render_plays():
    """The single Plays page. One in-page mode toggle routes to the three
    existing views — the curated board, the full sortable table, and the
    multi-book edge scanner — so all three stay reachable without three nav
    rows. 'Board' is the default."""
    # Seed the toggle from a legacy section code (deep-link / collapsed nav).
    code = st.session_state.pop("plays_mode_code", None)
    if code and "plays_mode" not in st.session_state:
        st.session_state["plays_mode"] = _PLAYS_MODE_BY_CODE.get(code, "◆ Board")
    mode = st.radio("Plays view", _PLAYS_MODES, horizontal=True,
                    label_visibility="collapsed", key="plays_mode")
    if mode == "Full table":
        render_plays_detail()
    elif mode == "Edge scanner":
        render_edges()
    else:
        render_plays_board()


def render_plays_board():
    """Curated plays as one clean, scannable table (matchup | play), grouped by
    sport — the BettorSheets board. A pass shows as 'No Plays' so the whole
    slate reads at a glance; the deep Sharp Sheet opens from the selector below,
    keeping the table itself uncluttered."""
    import html as _html

    q = topbar("Plays")
    date_sel = pick_date()
    day = slates.get(date_sel, {})
    board = ui.build_best_bets(day, min_edge)
    if hide_wild and not board.empty:
        board = board[pd.to_numeric(board["ev"], errors="coerce") < 0.30]
    if q and not board.empty:
        board = board[board["bet"].str.lower().str.contains(q)
                      | board["game"].str.lower().str.contains(q)]
    sports_in_slate = [s for s in NAV_SPORTS if s in day]
    if not sports_in_slate:
        st.info("No slate loaded yet.")
        return

    html_rows = ["<div class='pl-board'>",
                 "<div class='pl-head'><span>Matchup</span><span>Play</span></div>"]
    picker: list[tuple[str, str, object]] = []  # (label, sport, row)
    for sport in sports_in_slate:
        rows = (board[board["sport"] == sport] if not board.empty
                else board.iloc[0:0])
        html_rows.append(
            f"<div class='pl-grp'>{_html.escape(_SPORT_LABELS.get(sport, sport))}</div>")
        if rows.empty:
            html_rows.append("<div class='pl-none'>No Plays</div>")
            continue
        for _, r in rows.reset_index(drop=True).iterrows():
            price = ui.fmt_american(r["price"]) if pd.notna(r.get("price")) else ""
            play = f"{r['bet']} {price}".strip()
            html_rows.append(
                f"<div class='pl-row'><span class='m'>{_html.escape(str(r['game']))}</span>"
                f"<span class='p'>{_html.escape(play)}</span></div>")
            picker.append((f"{sport} · {r['game']} — {play}", sport, r))
    html_rows.append("</div>")
    st.markdown("".join(html_rows), unsafe_allow_html=True)
    st.caption("The model's edges, plain — passes shown too. Not financial advice.")

    # Deep dive: open the Sharp Sheet behind any listed play from a single
    # selector, so the board stays a clean table (no per-row buttons). The
    # button is a one-shot event, which avoids the dialog re-opening on rerun.
    if picker:
        labels = [p[0] for p in picker]
        c1, c2 = st.columns([6, 1], vertical_alignment="bottom")
        pick = c1.selectbox("Sharp Sheet", ["📊 Open a Sharp Sheet…"] + labels,
                            label_visibility="collapsed")
        if c2.button("Open", width="stretch") and pick in labels:
            _, sport, r = picker[labels.index(pick)]
            _sharpsheet(sport, _resolve_game(sport, r, date_sel), date_sel)


# ---------------------------------------------------------------------------
# PERFORMANCE
# ---------------------------------------------------------------------------

def render_dfs():
    topbar("DFS Optimizer", with_search=False)
    date_sel = pick_date()
    day = slates.get(date_sel, {})
    dfs_only = st.toggle(
        "PrizePicks / Underdog lines only", value=False,
        help="Price every leg off the operator's own pick'em line (when "
             "BettingPros carries it) and hide props that only have a "
             "sportsbook consensus number.")
    cands = dfs.candidates(day, dfs_only=dfs_only)
    if cands.empty:
        st.info("No props with a PrizePicks/Underdog line yet for this slate."
                if dfs_only else
                "No props with model probabilities yet for this slate.")
        return
    n_dfs = int((cands.get("line_source", pd.Series(dtype=str))
                 .isin(["PrizePicks", "Underdog"])).sum()) \
        if "line_source" in cands.columns else 0
    st.caption("**Edge = our model probability − the de-vigged price.** When an "
               "operator's own pick'em line is available it's priced off *that* "
               "line (the number you actually bet); otherwise the sportsbook "
               "consensus. Only legs where we beat the price are eligible. "
               f"Probabilities capped at {dfs.PROB_CAP:.0%}; PrizePicks "
               "multipliers (Underdog ≈ same); legs treated as independent. "
               f"({n_dfs} legs on a live PrizePicks/Underdog line.)")
    slips = dfs.best_slips(cands, min_edge=min_edge)
    if not slips:
        st.warning(f"No legs clear a {min_edge:.0%} edge over the book on this "
                   "slate — that usually means a pass. Lower **Min edge (EV)** in "
                   "the sidebar to inspect thinner spots.")
    else:
        cols = st.columns(len(slips))
        for i, s_ in enumerate(slips):
            with cols[i]:
                pe, fe = s_["power_ev"], s_["flex_ev"]
                best = max([x for x in (pe, fe) if x is not None], default=None)
                st.metric(f"{s_['size']}-pick", f"{best:+.0%} EV" if best is not None else "—",
                          help=f"Power {pe:+.0%}" + (f" · Flex {fe:+.0%}" if fe is not None else "")
                          if pe is not None else None)
        top = slips[-1]
        st.markdown(f"##### Suggested {top['size']}-leg card "
                    f"(hit-all {top['joint']:.1%}; each leg must clear "
                    f"{top['breakeven']:.0%} to break even)")
        for l in top["legs"]:
            src = l.get("line_source")
            at = f" @ {src}" if src and src != "consensus" else ""
            st.markdown(f"- **{l['player']}** ({l['sport']}, {l['team']}) — "
                        f"**{l['side']} {l['line']:g} "
                        f"{ui.short_market(str(l['market']))}**{at} · "
                        f"model {l['raw_prob']:.0%} vs book {l['book_prob']:.0%} "
                        f"(**{l['edge']:+.0%}** edge)")
        if all(s_["power_ev"] is not None and s_["power_ev"] < 0 for s_ in slips):
            st.warning("Even with edge, no positive-EV power slip today — the "
                       "multipliers' house edge swamps it. Flex or pass.")
    section_header("Candidate pool (by edge)")
    pool = cands[cands["edge"].notna()].head(25).copy()
    if "line_source" not in pool.columns:
        pool["line_source"] = "consensus"
    pool["market"] = pool["market"].map(lambda m: ui.short_market(str(m)))
    pool["raw_prob"] = pool["raw_prob"].map(lambda v: f"{v:.0%}")
    pool["book_prob"] = pool["book_prob"].map(lambda v: f"{v:.0%}")
    pool["edge"] = pool["edge"].map(lambda v: f"{v:+.1%}")
    pool = pool.rename(columns={
        "player": "Player", "sport": "Sport", "team": "Team",
        "market": "Market", "line": "Line", "side": "Side",
        "raw_prob": "P (model)", "book_prob": "P (book)", "edge": "Edge",
        "line_source": "Book"})
    st.dataframe(pool[["Player", "Sport", "Team", "Market", "Line", "Side",
                       "Book", "P (model)", "P (book)", "Edge"]],
                 width="stretch", hide_index=True)


_GATE_BADGE = {"cleared": "🟢 Cleared", "probation": "🟡 Probation",
               "gated": "🔴 Gated off"}


def _edge_gate_section():
    """Show the per-market demonstrated-edge gate: which markets we've *proven*
    an edge in (and bet fully), which are unproven (bet small), and which we've
    shown no edge in (gated off). Transparency is the brand — we say out loud
    where we do and don't bet, and why."""
    try:
        from project547 import edge_gate
        table = edge_gate.gate_table()
    except Exception:
        return
    if not table:
        return
    order = {"cleared": 0, "probation": 1, "gated": 2}
    rows = []
    for (sport, market), v in sorted(
            table.items(), key=lambda kv: (order.get(kv[1]["status"], 9), kv[0])):
        rows.append({
            "Market": f"{sport} {str(market).title()}",
            "Status": _GATE_BADGE.get(v["status"], v["status"]),
            "CLV bets": v["clv_n"],
            "Avg CLV": v["avg_clv"],
            "ROI": v["roi"],
            "Stake": {"cleared": "full", "probation": "½",
                      "gated": "none"}.get(v["status"], "½"),
        })
    df = pd.DataFrame(rows)
    section_header("Edge gate — which markets we bet")
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={
                     "Avg CLV": st.column_config.NumberColumn(format="%+.3f"),
                     "ROI": st.column_config.NumberColumn(format="%+.3f")})
    st.caption("A market earns curation by **beating the close (CLV)**, not by "
               "clearing an EV bar — a 2–6% edge on a market we don't beat is "
               "noise. 🟢 proven (full stake) · 🟡 unproven, betting small to "
               "gather CLV · 🔴 shown no edge, gated off. Rolling window, so a "
               "market re-earns or loses its status as results accrue.")


def _source_tracking_section():
    """Projection accuracy by source — our model vs the de-vigged market vs
    BettingPros, across every sport. This is how we prove we beat the baseline."""
    from project547 import tracking
    s = tracking.summary()
    bs = s.get("by_source", {})
    if not bs:
        return
    section_header("Projection accuracy by source", "📊")
    st.caption(f"Who's been right, across every sport "
               f"({s['graded_events']} graded events). Our edge = beating the "
               "de-vigged market baseline.")
    label = {"model": "Our model", "blend": "Model + adj",
             "market": "Market baseline", "bettingpros": "BettingPros"}
    order = [k for k in ("model", "blend", "market", "bettingpros") if k in bs]
    for col, src in zip(st.columns(len(order)), order):
        d = bs[src]
        acc = f"{d['accuracy']*100:.0f}%" if d.get("accuracy") is not None else "—"
        lift = d.get("lift_vs_market")
        col.metric(label[src], acc,
                   delta=(f"{lift*100:+.0f} vs market" if lift else None),
                   help=f"n={d['n']} graded · Brier {d.get('brier')}")
    st.divider()


def _bet_label(r: dict) -> str:
    """Human pick string from a ledger bet row → the play card's `bet` field.
    Tolerates missing pieces; mirrors how the side/line read on the alert."""
    side = (r.get("side") or "").strip()
    line = r.get("line")
    market = (r.get("market") or "").lower()
    team = ""
    game = r.get("game") or ""
    if " @ " in game:
        away, home = game.split(" @ ", 1)
        team = home.strip() if side == "home" else away.strip() if side == "away" else ""
    if market in ("total", "totals"):
        ou = "Over" if side == "over" else "Under" if side == "under" else side.title()
        return f"{ou} {line:g}".strip() if isinstance(line, (int, float)) else ou or "Total"
    if market in ("spread", "spreads", "runline", "puckline"):
        base = team or side.title()
        return f"{base} {line:+g}".strip() if isinstance(line, (int, float)) else base
    if market in ("moneyline", "ml"):
        return f"{team or side.title()} ML".strip()
    return (team or side.title() or market.title() or "—")


def _slice_table(bets: list[dict]) -> pd.DataFrame:
    """By-sport / by-bet-type receipts from graded bet rows. One row per
    (sport, bet type) plus per-sport subtotals: Bets · Win% · Units · ROI% ·
    Avg CLV. Pure aggregation — tolerates missing CLV (degrades to None)."""
    if not bets:
        return pd.DataFrame()

    def _agg(rows):
        n = len(rows)
        wins = sum(1 for r in rows if r.get("won"))
        units = sum(r.get("pnl") or 0 for r in rows)
        clvs = [r["clv"] for r in rows if r.get("clv") is not None]
        return {
            "Bets": n,
            "Win%": round(100 * wins / n, 1) if n else None,
            "Units": round(units, 2),
            "ROI%": round(100 * units / n, 1) if n else None,
            "Avg CLV": round(100 * sum(clvs) / len(clvs), 2) if clvs else None,
        }

    by_sport: dict = {}
    for r in bets:
        by_sport.setdefault(r.get("sport") or "—", []).append(r)
    out = []
    for sport in sorted(by_sport, key=lambda s: -len(by_sport[s])):
        rows = by_sport[sport]
        out.append({"Sport": sport, "Bet type": "All", **_agg(rows)})
        by_type: dict = {}
        for r in rows:
            by_type.setdefault((r.get("market") or "—").title(), []).append(r)
        for bt in sorted(by_type, key=lambda t: -len(by_type[t])):
            out.append({"Sport": sport, "Bet type": bt, **_agg(by_type[bt])})
    return pd.DataFrame(out)


def render_performance():
    topbar("Performance", with_search=False)
    _source_tracking_section()
    perf = (data or {}).get("performance", {})
    overall = perf.get("overall", {})
    ledger = load_ledger()
    if not overall.get("graded_games") and not ledger:
        st.info("No graded results yet — performance accrues as projected "
                "games finish and the hourly job grades them.")
        return

    bets = [r for r in ledger if "pnl" in r]

    # ── 1. TOP VERDICT BAND — CLV first, then the bankroll receipts. ──────
    avg_clv = overall.get("avg_clv_pct")
    units = overall.get("units", 0) or 0
    roi = overall.get("roi_pct")
    n_bets = overall.get("bets", 0) or 0
    wins = sum(1 for r in bets if r.get("won"))
    losses = sum(1 for r in bets if r.get("won") is False)
    v = st.columns(4)
    v[0].metric("Avg CLV", f"{avg_clv:+.2f}%" if avg_clv is not None else "—",
                help="Average edge vs the de-vigged closing line. Beating the "
                     "close is the strongest early proof of real edge — it "
                     "converges long before win/loss ROI does.")
    v[1].metric("Units", f"{units:+.2f}" if units else "0.00",
                help="Cumulative profit/loss in units at ¼-Kelly stakes.")
    v[2].metric("ROI", f"{roi:+.1f}%" if roi is not None else "—",
                help="Units returned per unit risked across all graded bets.")
    v[3].metric("Record (W–L)", f"{wins}–{losses}",
                help=f"{n_bets} graded bets. Wins and losses, both counted.")
    st.caption("Auto-graded — wins and losses included. We don't delete the "
               "misses. 52.4% pays the house; 54.7% pays you.")

    # ── 2. EQUITY CURVE — the visual centerpiece. ────────────────────────
    equity = ui.cumulative_units(ledger)
    if not equity.empty:
        eq = ui.equity_chart(equity)
        if eq is not None:
            st.altair_chart(eq, use_container_width=True)
        else:
            st.line_chart(equity, y="units", height=260)
        st.caption("Cumulative units at projection-time prices. Up and to the "
                   "right is the only pitch — every dip is a real losing stretch, "
                   "left in.")

    # ── 3. BY-SPORT / BY-BET-TYPE SLICE TABLE. ───────────────────────────
    slice_df = _slice_table(bets)
    if not slice_df.empty:
        section_header("By sport & bet type")
        styler = ev_styler(slice_df, ["ROI%", "Avg CLV"])
        st.dataframe(styler, width="stretch", hide_index=True)
        st.caption("Where the edge is — and isn't. ROI% and Avg CLV shade "
                   "green when we're ahead, red when we're behind. CLV blanks "
                   "where no closing line was captured.")

    # ── 3b. EDGE GATE — which markets we've earned the right to bet. ──────
    _edge_gate_section()

    # ── 4. RECENT BETS — the five freshest receipts as graded cards. ─────
    section_header("Recent bets")
    recent_rows = sorted(bets, key=lambda r: r.get("date") or "", reverse=True)[:5]
    if recent_rows:
        for r in recent_rows:
            card = {
                "sport": r.get("sport"), "game": r.get("game"),
                "bet": _bet_label(r), "price": r.get("price"),
                "ev": r.get("ev"), "clv": r.get("clv"),
                "won": r.get("won"), "pnl": r.get("pnl"),
            }
            st.markdown(ui.play_card_html(card, graded=True),
                        unsafe_allow_html=True)
    rest = ui.recent_bets([r for r in bets
                           if r not in recent_rows], n=25)
    if not rest.empty:
        with st.expander("More graded bets", expanded=False):
            st.dataframe(rest, width="stretch", hide_index=True)
    if not recent_rows:
        st.info("Recent bets show up here once recommended bets are graded.")

    st.caption("Forward-test record at projection-time prices. Brier + "
               "calibration cover every projected game; units/ROI cover "
               "recommended bets only.")

    # ── 5. SHOW THE WORK — the deeper views, behind expanders. ───────────
    # The two daily-recap accuracy numbers + day-by-day record.
    with st.expander("Accuracy & daily record", expanded=False):
        macc, mn = results.model_accuracy()
        pacc, pn = plays.played_accuracy()
        a = st.columns(2)
        a[0].metric("Model accuracy", f"{macc:.0%}" if macc is not None else "—",
                    help=f"How often the model's favored side won ({mn} graded "
                         "games). The plain-English accuracy number.")
        a[1].metric("Your played accuracy", f"{pacc:.0%}" if pacc is not None else "—",
                    help=f"Win rate on plays you tapped Played ({pn} decided) — "
                         "DFS legs and game bets combined.")
        drec = plays.load_daily_record()
        if drec:
            def _pct(val):
                return f"{val:.0%}" if isinstance(val, (int, float)) else "—"
            dd = pd.DataFrame(sorted(drec, key=lambda r: r["date"], reverse=True))
            dd["Model"] = [f"{_pct(val)} ({int(n)})" for val, n in
                           zip(dd["model_acc"], dd["model_n"].fillna(0))]
            dd["Played"] = [f"{_pct(val)} ({int(n)})" for val, n in
                            zip(dd["played_acc"], dd["played_n"].fillna(0))]
            dd = dd.rename(columns={"date": "Date"})
            st.dataframe(dd[["Date", "Model", "Played"]].head(30),
                         width="stretch", hide_index=True)
            st.caption("Each row is that day's model accuracy and your played "
                       "accuracy (sample size in parentheses).")
        gg = st.columns(2)
        gg[0].metric("Graded games", overall.get("graded_games", 0))
        gg[1].metric("Model Brier", overall.get("model_brier") or "—",
                     help="Win-probability error. 0.25 = coin flip; lower is better.")

    # CLV detail + reliability/calibration curve.
    clv_bets = overall.get("clv_bets") or 0
    with st.expander("Calibration & CLV detail — the honesty check", expanded=False):
        if not clv_bets:
            st.info("CLV accrues once recommended bets are graded against the "
                    "captured closing line. Beating the no-vig close is the "
                    "strongest early signal an edge is real.")
        else:
            cl = st.columns(3)
            beat = overall.get("clv_beat_rate")
            cl[0].metric("Avg CLV", f"{avg_clv:+.2f}%" if avg_clv is not None else "—")
            cl[1].metric("Beat-close rate", f"{beat * 100:.0f}%" if beat is not None else "—",
                         help="Share of graded bets that beat the closing line. "
                              "Above ~55–60% over a few hundred bets signals edge.")
            cl[2].metric("Bets w/ close", clv_bets)
            st.caption("CLV is measured against our own captured BettingPros "
                       "closing line; it sharpens as more books are added.")

        # Reliability table — when the model said X%, did it happen X%?
        from project547 import scorecard as _scorecard
        rel = _scorecard.reliability(ledger)
        if rel.get("n"):
            st.caption(f"When the model said X%, how often it actually happened "
                       f"({rel['n']} graded games · ECE {rel['ece']:.3f} — lower "
                       "is better). Points below the line mean we were "
                       "overconfident there.")
            rdf = pd.DataFrame([{"Model said": f"{int(b['lo']*100)}–{int(b['hi']*100)}%",
                                 "mid": (b["lo"] + b["hi"]) / 2, "Predicted": b["pred"],
                                 "Actually won": b["obs"], "Games": b["n"],
                                 "Gap": b["gap"]}
                                for b in rel["bins"] if b["n"]])
            try:
                import altair as alt
                diag = (alt.Chart(pd.DataFrame({"x": [0, 1]}))
                        .mark_line(strokeDash=[4, 4], color="#9b937f")
                        .encode(x=alt.X("x:Q", title="Model said (predicted win %)"),
                                y=alt.Y("x:Q", title="Actually won")))
                pts = alt.Chart(rdf).mark_circle(color="#2f7a4a").encode(
                    x="mid:Q", y="Actually won:Q",
                    size=alt.Size("Games:Q", legend=None),
                    tooltip=["Model said", "Predicted", "Actually won", "Games", "Gap"])
                st.altair_chart((diag + pts).properties(height=300), width="stretch")
            except Exception:  # noqa: BLE001 — chart optional, table always shows
                pass
            st.dataframe(rdf.drop(columns=["mid"]).style.format(
                {"Predicted": "{:.0%}", "Actually won": "{:.0%}", "Gap": "{:+.0%}"}),
                hide_index=True, width="stretch")

        # Win-probability calibration curve.
        curve = ui.calibration_curve(ledger)
        if curve.empty:
            st.info("Calibration accrues as projected games finish — once enough "
                    "land, the curve should hug the diagonal.")
        else:
            ece = ui.calibration_error(curve)
            cc = st.columns([3, 1])
            with cc[0]:
                st.altair_chart(ui.calibration_chart(curve))
            with cc[1]:
                st.metric("Calibration error", f"{ece:.1%}" if ece is not None else "—",
                          help="Avg gap between predicted and actual win %, "
                               "weighted by games. Under ~5% is well calibrated.")
                ll = overall.get("model_log_loss")
                st.metric("Log loss", ll if ll is not None else "—",
                          help="Kelly-aligned probability score. Lower is better; "
                               "0.69 = coin flip.")
                st.metric("Graded games", int(curve["n"].sum()))
            st.caption("Dashed line = perfect calibration. Above it = we were "
                       "under-confident; below = over-confident. Bubble size = "
                       "games in that bucket.")

    # DFS played-card track record.
    dfs_perf = plays.summary()
    if dfs_perf.get("legs_logged"):
        with st.expander("DFS plays (PrizePicks / Underdog)", expanded=False):
            dc = st.columns(4)
            dc[0].metric("Legs played", dfs_perf["played_logged"],
                         help="Legs in a notified +EV card — what you actually "
                              f"put in. ({dfs_perf['legs_logged']} total cleared "
                              f"the {config.DFS_MIN_EDGE:.0%} edge bar.)")
            dc[1].metric("Played graded", dfs_perf["played_graded"])
            wr = dfs_perf["played_win_rate"]
            be = dfs.per_leg_breakeven(2)
            dc[2].metric("Played win rate", f"{wr:.0%}" if wr is not None else "—",
                         help="Share of played legs that hit. A 2-pick needs each "
                              f"leg above ~{be:.0%} to break even.")
            beat = dfs_perf["line_clv_beat_rate"]
            dc[3].metric("Line came to us", f"{beat:.0%}" if beat is not None else "—",
                         help="Share of played legs whose DFS line moved in our "
                              "favor after we logged it.")
            st.caption("Pushed via ntfy only when a card is +EV; tap **Played** "
                       "on the alert to count it. Legs commit on first qualify so "
                       "you bet before the line moves.")

    # Sharp game plays: the CLV-validated EV band.
    sharp = [r for r in bets
             if config.SHARP_EV_MIN <= (r.get("ev") or 0) <= config.SHARP_EV_MAX]
    played_keys = plays.confirmed_played()

    def _gkey(r):
        return (f"game|{r['date']}|{r['sport']}|{r['game']}|"
                f"{r['market']}|{r.get('side', '')}")

    if sharp:
        shown = ([r for r in sharp if _gkey(r) in played_keys]
                 if played_keys else sharp)
        label = "played" if played_keys else "flagged"
        if shown:
            with st.expander(f"Sharp game plays (EV {config.SHARP_EV_MIN:.0%}"
                             f"–{config.SHARP_EV_MAX:.0%})", expanded=False):
                sp = st.columns(4)
                swins = sum(1 for r in shown if r.get("won"))
                sunits = sum(r["pnl"] for r in shown)
                sclv = [r["clv"] for r in shown if r.get("clv") is not None]
                sp[0].metric(f"Sharp bets ({label})", len(shown))
                sp[1].metric("Win rate", f"{swins / len(shown):.0%}")
                sp[2].metric("Units", f"{sunits:+.2f}")
                sp[3].metric("Avg CLV", f"{100 * sum(sclv) / len(sclv):+.1f}%"
                             if sclv else "—")
                st.caption("The CLV-validated EV band — moderate edges that "
                           "historically beat the close. Very high-EV spots are "
                           "flagged 'verify', not pushed (their CLV says the line "
                           "is likely stale).")

    # Model vs market + the de-vig / MARKET_SHRINK blend tuner.
    from project547 import scorecard as _sc
    sc = _sc.scorecard(ledger)
    d, a = sc["disagree"], sc["agree"]
    with st.expander("Model vs market & blend tuner", expanded=False):
        if not d["n"] and not a["n"]:
            st.info("Accrues once games are graded with a captured closing line. "
                    "Compares the model's calibration where it **agrees** vs "
                    "**disagrees** with the market — the disagreement bucket is "
                    "the real test of independent edge.")
        else:
            be, dmacc, kacc = d["brier_edge"], d["model_acc"], d["market_acc"]
            mc = st.columns(3)
            mc[0].metric("Disagreement games", d["n"])
            mc[1].metric("Model Brier edge vs market",
                         f"{be:+.3f}" if be is not None else "—",
                         help="Market Brier − model Brier where our pick differs "
                              "from the market. Positive = better calibrated "
                              "exactly where it breaks from the market.")
            mc[2].metric("Model acc. on disagreements",
                         f"{dmacc:.0%}" if dmacc is not None else "—",
                         delta=(f"{(dmacc - kacc) * 100:+.0f} pts vs market"
                                if dmacc is not None and kacc is not None else None))
            tbl = pd.DataFrame({
                "Bucket": ["Disagrees w/ market", "Agrees w/ market", "All games"],
                "Games": [d["n"], a["n"], sc["n_games"]],
                "Model Brier": [d["model_brier"], a["model_brier"],
                                sc["overall"]["model_brier"]],
                "Market Brier": [d["market_brier"], a["market_brier"],
                                 sc["overall"]["market_brier"]],
                "Model acc": [d["model_acc"], a["model_acc"], sc["overall"]["model_acc"]],
                "Market acc": [d["market_acc"], a["market_acc"], sc["overall"]["market_acc"]],
            })
            st.dataframe(tbl, width="stretch", hide_index=True)
            bsc = _sc.bet_scorecard(ledger)
            con, wm = bsc["contrarian"], bsc["with_market"]
            if con["n"] or wm["n"]:
                st.markdown("**Bets by stance**")
                st.dataframe(pd.DataFrame({
                    "Stance": ["Contrarian (vs market)", "With market"],
                    "Bets": [con["n"], wm["n"]],
                    "ROI %": [con["roi_pct"], wm["roi_pct"]],
                    "Win rate": [con["win_rate"], wm["win_rate"]],
                    "Avg CLV %": [con["avg_clv_pct"], wm["avg_clv_pct"]],
                }), width="stretch", hide_index=True)
            st.caption("If the model only wins when it agrees with the market, "
                       "it's following it. A positive Brier edge on the "
                       "disagreement bucket is the proof it adds signal.")

            # de-vig / MARKET_SHRINK blend tuner.
            tune = _sc.optimal_shrink(ledger, current=config.MARKET_SHRINK)
            st.markdown("**Calibration-driven tuning (de-vig blend)**")
            if not tune.get("ready"):
                st.caption(f"Optimal-shrink tuning unlocks after "
                           f"{tune['min_games']} graded games ({tune['n']} so "
                           "far). It will recommend the model/market blend that "
                           "minimizes Brier.")
            else:
                improv = tune["current_brier"] - tune["best_brier"]
                tc = st.columns(3)
                tc[0].metric("Current MARKET_SHRINK", f"{tune['current_alpha']:.2f}")
                tc[1].metric("Data-optimal shrink", f"{tune['best_brier_alpha']:.2f}",
                             help="The model↔market blend weight that minimizes "
                                  "Brier. 0 = trust the model fully; 1 = defer to "
                                  "the market.")
                tc[2].metric("Brier if retuned", f"{tune['best_brier']:.4f}",
                             delta=f"{-improv:+.4f} vs current", delta_color="inverse")
                st.caption(
                    f"Over {tune['n']} graded games, `MARKET_SHRINK = "
                    f"{tune['best_brier_alpha']:.2f}` would have minimized win-prob "
                    f"Brier (model-only {tune['model_only_brier']:.4f} · "
                    f"market-only {tune['market_only_brier']:.4f}). Recommendation "
                    "only — update config.py when the sample is large enough.")


# ---------------------------------------------------------------------------
# SCORES: live scoreboard + box scores (follow results inside the app)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30, show_spinner=False)
def load_scores(date_str: str) -> list[dict]:
    from project547 import scores
    return scores.live_scoreboard(date_str)


def _score_ticker(games: list[dict]):
    from project547 import scores
    live = [g for g in games if g.get("state") == "in"] or games
    chips = []
    for g in live[:40]:
        color = ("var(--good)" if g.get("state") == "in"
                 else "var(--muted)" if g.get("state") == "post" else "var(--acc2)")
        chips.append(
            f"<span style='margin:0 18px;color:{color};font-weight:600;'>"
            f"{scores.ticker_text(g)}</span>")
    if not chips:
        return
    strip = "".join(chips)
    st.markdown(
        "<div style='overflow:hidden;white-space:nowrap;border:1px solid var(--line);"
        "border-radius:8px;background:var(--card2);padding:8px 0;margin-bottom:10px;'>"
        "<div style='display:inline-block;padding-left:100%;"
        "animation:osp-marquee 60s linear infinite;'>" + strip + strip + "</div></div>"
        "<style>@keyframes osp-marquee{0%{transform:translateX(0)}"
        "100%{transform:translateX(-50%)}}</style>", unsafe_allow_html=True)


def _score_card(g: dict) -> str:
    def row(side):
        s = g.get(side, {})
        logo = (f"<img src='{s['logo']}' width='22' height='22' "
                f"style='vertical-align:middle;margin-right:6px;'>" if s.get("logo") else "")
        sc = s.get("score")
        sc = "" if sc is None else (f"{int(sc) if float(sc) == int(sc) else sc}")
        win = (g.get("state") == "post" and sc != "" and
               (g.get(side, {}).get("score") or 0) >
               (g.get("home" if side == "away" else "away", {}).get("score") or 0))
        weight = "800" if win else "500"
        rec = f"<span style='color:var(--faint);font-size:0.7rem;'> {s.get('record','')}</span>" if s.get("record") else ""
        return (f"<div style='display:flex;justify-content:space-between;'>"
                f"<span style='font-weight:{weight};'>{logo}{s.get('abbrev') or s.get('team') or '—'}{rec}</span>"
                f"<span style='font-weight:{weight};font-size:1.05rem;'>{sc}</span></div>")
    color = ("var(--good)" if g.get("state") == "in" else "var(--muted)")
    return ("<div style='background:var(--card);border:1px solid var(--line);border-radius:10px;"
            "padding:10px 12px;'>"
            + row("away") + "<div style='height:4px;'></div>" + row("home")
            + f"<div style='color:{color};font-size:0.72rem;margin-top:6px;'>"
            f"{g.get('detail') or ''}</div></div>")


def _auto_fragment(**kw):
    """st.fragment(run_every=…) when available, else a no-op decorator so the
    page still renders (just without live auto-refresh) on older Streamlit."""
    frag = getattr(st, "fragment", None)
    return frag(**kw) if frag else (lambda f: f)


@_auto_fragment(run_every=30)
def render_scores_board(date_str: str):
    games = load_scores(date_str)
    _score_ticker(games)
    if not games:
        st.info("No games found for this date (or score sources are "
                "unreachable right now).")
        return
    st.caption(f"{sum(g['state'] == 'in' for g in games)} live · "
               f"{len(games)} games · auto-refreshes every 30s")
    by_sport: dict = {}
    for g in games:
        by_sport.setdefault(g["sport"], []).append(g)
    for sport, gs in by_sport.items():
        section_header(sport)
        cols = st.columns(3)
        for i, g in enumerate(gs):
            with cols[i % 3]:
                st.markdown(_score_card(g), unsafe_allow_html=True)

    # box-score drill-in
    st.divider()
    labels = [f"{g['sport']}: {g['away'].get('abbrev') or g['away'].get('team')} @ "
              f"{g['home'].get('abbrev') or g['home'].get('team')}" for g in games]
    pick = st.selectbox("📋 Open a box score", ["—", *labels], key="boxpick")
    if pick != "—":
        from project547 import scores
        g = games[labels.index(pick)]
        box = scores.box_score(g["sport"], g["game_id"])
        if not box.get("teams"):
            st.info("Box score not available yet for this game.")
        for team in box["teams"]:
            if not team["rows"]:
                continue
            st.markdown(f"**{team['team']}**")
            st.dataframe(pd.DataFrame(team["rows"], columns=team["columns"]),
                         width="stretch", hide_index=True)


def render_scores():
    topbar("Scoreboard", with_search=False)
    et_today = pd.Timestamp.now(tz="America/New_York")
    days = [(et_today - pd.Timedelta(days=d)).date().isoformat() for d in range(3)]
    date_str = st.radio("Day", days, horizontal=True, label_visibility="collapsed",
                        format_func=lambda d: "Today" if d == days[0]
                        else "Yesterday" if d == days[1] else d, key="scoredate")
    render_scores_board(date_str)


# ---------------------------------------------------------------------------
# TOOLS: the betting-math toolkit (devig / EV / arb / middle / hedge / Kelly)
# ---------------------------------------------------------------------------

def render_tools():
    from project547 import calculators as calc

    topbar("Betting Tools", with_search=False)
    st.caption("The deterministic toolkit — fair odds, edge, and staking math "
               "on any prices you paste in.")
    t1, t2, t3, t4, t5 = st.tabs(["De-vig & EV", "Arbitrage & Middle", "Hedge",
                                  "Kelly & Risk", "Parlay & Correlation"])

    with t1:
        c = st.columns(3)
        over = c[0].number_input("Side A odds", value=-110, step=5, key="dv_a")
        under = c[1].number_input("Side B odds", value=-110, step=5, key="dv_b")
        meth = c[2].selectbox("De-vig method", ["multiplicative", "power"])
        fair = calc.no_vig(over, under, method=meth)
        m = st.columns(3)
        m[0].metric("Fair A", f"{fair[0]:.1%}")
        m[1].metric("Fair B", f"{fair[1]:.1%}")
        m[2].metric("Hold (vig)", f"{calc.hold(over, under):.2%}")
        st.divider()
        st.markdown("**Expected value** of a price vs your win probability")
        e = st.columns(3)
        p = e[0].number_input("Your win %", 0.0, 100.0, 55.0, 0.5, key="ev_p") / 100
        price = e[1].number_input("Offered odds", value=-110, step=5, key="ev_price")
        ev = ui_ev(p, price)
        e[2].metric("EV per $1", f"{ev:+.1%}",
                    help="Positive means the price pays more than your probability.")
        st.caption("Tip: set 'Your win %' to a sharp book's de-vigged fair "
                   "probability to screen for +EV against the market.")

    with t2:
        st.markdown("**Arbitrage** — best price on each side across books")
        a = st.columns(3)
        oa = a[0].number_input("Outcome A best", value=110, step=5, key="ar_a")
        ob = a[1].number_input("Outcome B best", value=110, step=5, key="ar_b")
        stake = a[2].number_input("Total stake $", value=100, step=10, key="ar_s")
        arb = calc.arbitrage([oa, ob], total=stake)
        if arb:
            st.success(f"Arb! Lock **${arb['profit']:.2f}** "
                       f"({arb['profit_pct']:.2f}%). Stake "
                       f"${arb['stakes'][0]:.2f} / ${arb['stakes'][1]:.2f}.")
        else:
            st.info("No arbitrage — the two prices imply ≥100%.")
        st.divider()
        mid = st.number_input("Middle vig (both sides)", value=-110, step=5,
                              key="mid")
        st.metric("Break-even hit rate", f"{calc.middle_breakeven(mid):.1%}",
                  help="How often the middle must land to be +EV at this vig.")

    with t3:
        h = st.columns(3)
        os_ = h[0].number_input("Original stake $", value=100, step=10, key="h_s")
        oo = h[1].number_input("Original odds", value=200, step=10, key="h_o")
        ho = h[2].number_input("Hedge odds (other side)", value=-150, step=10,
                               key="h_h")
        res = calc.hedge(os_, oo, ho)
        hc = st.columns(3)
        hc[0].metric("Hedge stake", f"${res['hedge_stake']:.2f}")
        hc[1].metric("Guaranteed profit", f"${res['guaranteed_profit']:.2f}")
        hc[2].metric("Total outlay", f"${res['total_outlay']:.2f}")

    with t4:
        k = st.columns(3)
        kp = k[0].number_input("Win %", 0.0, 100.0, 55.0, 0.5, key="k_p") / 100
        ko = k[1].number_input("Odds", value=-110, step=5, key="k_o")
        kf = k[2].slider("Kelly fraction", 0.0, 1.0, 0.25, 0.05)
        from project547 import odds as _odds
        stake_frac = _odds.kelly_stake(kp, ko, kf)
        ror = calc.risk_of_ruin(kp, ko, fraction=kf)
        kc = st.columns(3)
        kc[0].metric("Stake (% bankroll)", f"{stake_frac:.1%}")
        kc[1].metric("EV per $1", f"{ui_ev(kp, ko):+.1%}")
        kc[2].metric("Risk of 50% drawdown", f"{ror:.0%}",
                     help="Monte-Carlo chance of halving the bankroll over 500 "
                          "bets at this fraction.")

    with t5:
        st.markdown("**Independent parlay**")
        legs_txt = st.text_input("Leg odds (comma-separated American)",
                                 "-110, -110, +120", key="par")
        try:
            legs = [float(x) for x in legs_txt.split(",") if x.strip()]
            par = calc.parlay(legs)
            pc = st.columns(2)
            pc[0].metric("Parlay price", ui.fmt_american(par["american"]))
            pc[1].metric("Implied win %", f"{par['implied_prob']:.1%}")
        except ValueError:
            st.warning("Enter comma-separated numbers, e.g. -110, +120.")
        st.divider()
        st.markdown("**Same-game parlay** — correlation-adjusted fair price & EV")
        from project547 import sgp as _sgp
        legs_p = st.text_input("Leg win % (comma-separated)", "55, 60",
                               key="sgp_p")
        preset = st.selectbox("Leg relationship (sets ρ)",
                              list(_sgp.CORRELATION_PRESETS), index=1,
                              key="sgp_preset")
        rho = st.slider("Correlation ρ (override the preset)", -0.5, 1.0,
                        _sgp.CORRELATION_PRESETS[preset], 0.05, key="sgp_rho")
        quoted = st.number_input("Book's SGP price (optional; 0 = none)",
                                 value=0, step=5, key="sgp_quote")
        try:
            ps = [float(x) / 100 for x in legs_p.split(",") if x.strip()]
            r = _sgp.price_sgp(ps, rho=rho,
                               quoted_american=quoted if quoted else None)
            rc = st.columns(3)
            rc[0].metric("Joint (correlated)", f"{r['joint_prob']:.1%}",
                         delta=f"{r['lift'] * 100:+.1f} pts vs indep")
            rc[1].metric("Fair price", ui.fmt_american(r["fair_american"])
                         if r["fair_american"] is not None else "—")
            rc[2].metric("If independent", ui.fmt_american(r["independent_american"])
                         if r["independent_american"] is not None else "—")
            if "ev" in r:
                ec = st.columns(2)
                ec[0].metric("EV vs book's SGP", f"{r['ev']:+.1%}",
                             help="Positive = the book's SGP price beats the "
                                  "correlation-adjusted fair value.")
                ec[1].metric("¼-Kelly stake", f"{r['stake']:.1%}")
        except ValueError as e:
            st.warning(str(e))
        st.caption("Pick the relationship between your legs (or set ρ directly). "
                   "Positive correlation lifts the true joint probability above "
                   "the independent product — so a book SGP priced near the "
                   "independent number is +EV. Enter the book's price to grade it.")


def ui_ev(prob: float, american) -> float:
    from project547 import odds as _odds
    return _odds.expected_value(prob, american)


# ---------------------------------------------------------------------------
# HOME: command-center overview
# ---------------------------------------------------------------------------

def _hero_band(pct):
    """Color for a headline percentage against the 54.7 target: under 54.7 =
    red, 54.7–56 = green, 56+ = a deep editorial blue. Calm on cream, no glow."""
    if pct is None:
        return "var(--faint)"
    if pct < 54.7:
        return "var(--neg)"
    if pct < 56.0:
        return "var(--good)"
    return "#2b5f8a"


def _hero_tile(label, pct, n, sub):
    color = _hero_band(pct)
    val = f"{pct:.1f}%" if pct is not None else "—"
    return (
        f"<div style='flex:1;min-width:240px;text-align:center;border-radius:10px;"
        f"padding:22px 18px 16px;background:var(--card);border:1.5px solid var(--text);'>"
        f"<div style='font-family:var(--disp);font-size:.72rem;font-weight:600;"
        f"text-transform:uppercase;letter-spacing:.14em;color:var(--muted);'>{label}</div>"
        f"<div style='font-family:var(--disp);font-size:4rem;font-weight:700;"
        f"line-height:1.05;margin:6px 0 2px;color:{color};'>{val}</div>"
        f"<div style='height:3px;width:46px;margin:2px auto 8px;background:{color};'></div>"
        f"<div style='font-size:.76rem;color:var(--muted);'>{sub}"
        f"{f' · {n} graded' if n else ''}</div></div>"
    )


def _heroes_html(m: dict) -> str:
    tiles = (_hero_tile("Overall Engine", m["engine_pct"], m["engine_n"],
                        "model picks the winner, straight up")
             + _hero_tile("Curated Plays · 2–6% EV", m["curated_pct"],
                          m["curated_n"], "curated-band hit rate"))
    legend = (
        "<div style='text-align:center;margin-top:10px;font-size:.72rem;color:var(--muted);'>"
        "Bar to clear: <b style='color:var(--text);'>54.7%</b> &nbsp;·&nbsp; "
        "<span style='color:var(--neg);'>● under 54.7</span> &nbsp; "
        "<span style='color:var(--good);'>● 54.7–56</span> &nbsp; "
        "<span style='color:#2b5f8a;'>● 56+</span> &nbsp;·&nbsp; updated daily</div>")
    return ("<div style='display:flex;gap:16px;flex-wrap:wrap;margin:4px 0 2px;'>"
            + tiles + "</div>" + legend)


def render_home():
    st.markdown("<div class='osp-hero'><div class='osp-title'>🎯 360Five"
                "</div></div>", unsafe_allow_html=True)
    st.caption("360° of research. 5 questions. 365 days. · "
               f"Updated {gen} ET · model estimates, not financial advice")

    # --- The 54.7 heroes: front and center, glowing by where we stand -------
    st.markdown(_heroes_html(results.hero_metrics(load_ledger())),
                unsafe_allow_html=True)
    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)

    day = slates.get(default_date, {})
    board = ui.build_best_bets(day, min_edge)
    if hide_wild and not board.empty:
        board = board[pd.to_numeric(board["ev"], errors="coerce") < 0.30]
    perf = (data or {}).get("performance", {}).get("overall", {})
    games_today = sum(len(b.get("games", []) or []) for b in day.values())

    # --- The receipts (CLV-first: skill shows up in CLV before the bankroll) -
    section_header("The receipts — every pick graded, wins and losses", "📒")
    roi = perf.get("roi_pct")
    clv = perf.get("avg_clv_pct")
    units = perf.get("units")
    beat = perf.get("clv_beat_rate")
    nbets = perf.get("bets", 0)
    s = st.columns(4)
    s[0].metric("Avg CLV", f"{clv:+.2f}%" if clv is not None else "—",
                help="Edge vs the closing line — the truest early skill signal.")
    s[1].metric("Units", f"{units:+.1f}u" if units is not None else "—",
                help=f"Net units across {nbets} graded bets "
                     f"(Brier {perf.get('model_brier') or '—'}).")
    s[2].metric("ROI", f"{roi:+.1f}%" if roi is not None else "—",
                help=f"Profit ÷ amount staked, over {nbets} bets.")
    s[3].metric("CLV beat", f"{beat*100:.0f}%" if beat is not None else "—",
                help="Share of bets that beat the closing line.")
    if not nbets:
        st.caption("Every pick graded — wins and losses. No record yet; it fills "
                   "in as picks settle. Full history on the **Ledger** tab.")
    else:
        st.caption("Every pick graded — wins and losses. "
                   "Full breakdown on the **Ledger** tab.")
    st.divider()

    # --- Tonight's slate ----------------------------------------------------
    section_header(f"Tonight — {default_date or '—'}", "🗓️")
    c = st.columns(3)
    c[0].metric("Games", games_today)
    c[1].metric("Edges ≥ thresh", 0 if board.empty else len(board))
    c[2].metric("Best EV", f"{board['ev'].max():+.1%}" if not board.empty else "—")

    left, right = st.columns([3, 2])
    with left:
        section_header("Tonight's top plays", "🔥")
        if board.empty:
            # Pass-day honesty, brand voice — not a "lower the bar" nudge.
            st.markdown(
                "<div style='background:var(--card);border:1px solid var(--line);"
                "border-radius:10px;padding:16px 18px;color:var(--muted);'>"
                "<b style='color:var(--text);'>Passed the slate</b> — nothing "
                "cleared the bar tonight. We'd rather sit than sell you a coin "
                "flip. Check back as lineups post.</div>",
                unsafe_allow_html=True)
        else:
            top = board.head(5).to_dict("records")
            cols = st.columns(2)
            for i, play in enumerate(top):
                with cols[i % 2]:
                    st.markdown(ui.play_card_html(play), unsafe_allow_html=True)
            st.markdown(
                "<div style='margin-top:4px;font-family:var(--disp);font-weight:600;"
                "font-size:.82rem;letter-spacing:.04em;color:var(--good);'>"
                "See all plays → <span style='color:var(--muted);font-weight:400;'>"
                "open <b>Plays</b> for the full board, or <b>Edges</b> for "
                "sharp/arb/middle markets.</span></div>",
                unsafe_allow_html=True)
    with right:
        section_header("Slate at a glance", "🗓️")
        pills = []
        for sport in NAV_SPORTS:
            b = day.get(sport, {})
            ng, npr = len(b.get("games", []) or []), len(b.get("props", []) or [])
            if ng or npr:
                pills.append(
                    "<span class='osp-pill' style='background:var(--card);"
                    "border:1px solid var(--line);color:var(--text);'>"
                    f"<b>{sport}</b> · {ng}g · {npr}p</span>")
        if pills:
            st.markdown("<div style='display:flex;flex-wrap:wrap;gap:6px;'>"
                        + "".join(pills) + "</div>", unsafe_allow_html=True)
        else:
            st.caption("No games scheduled on this slate.")
        ready, _ = ai.available()
        chip = ("<span class='osp-pill live' style='background:rgba(0,230,118,0.18);"
                "color:var(--good);'>● AI analyst on</span>" if ready else
                "<span class='osp-pill' style='background:rgba(110,118,129,0.18);"
                "color:var(--muted);'>○ AI analyst off</span>")
        st.markdown(chip, unsafe_allow_html=True)
        st.caption("Open any matchup, prop, or board and hit **✨ Analyze** for a "
                   "Claude read." if ready else
                   "Add ANTHROPIC_API_KEY (secret) to enable one-click Claude analysis.")


# ---------------------------------------------------------------------------
# EDGES: multi-book consensus scanner (+EV / arb / middles / low-hold)
# ---------------------------------------------------------------------------

def _book(b) -> str:
    return str(b).replace("_", " ").title()


def render_edges():
    topbar("Edge Scanner", with_search=False)
    date_sel = pick_date()
    st.caption("Multi-book **consensus** edges: +EV vs the de-vigged market (the "
               "OddsJam/Unabated standard), plus arbitrage, middles, and soft "
               "low-hold markets — computed from the captured Odds API multi-book "
               "lines.")
    found = False
    for sport in NAV_SPORTS:
        try:
            scan = edge.scan_slate(sport, date_sel, min_ev=min_edge)
        except Exception:
            continue
        if not any(scan.get(k) for k in ("plus_ev", "arbs", "middles", "low_holds")):
            continue
        found = True
        section_header(sport)
        pev = scan["plus_ev"]
        if pev:
            st.markdown("**➕ Positive EV vs consensus**  ·  sorted EV high → low")
            df = pd.DataFrame([{
                "Side": r["side"], "Best": ui.fmt_american(r["price"]),
                "Book": _book(r["book"]), "Fair %": r["fair_prob"] * 100,
                "EV %": r["ev"] * 100, "Game": r["game"], "Market": r["market"],
                "#bk": r["n_books"]} for r in pev])
            # Keep the raw EV numeric, sort desc, then format via column_config.
            df = df.sort_values("EV %", ascending=False).reset_index(drop=True)
            st.dataframe(df, width="stretch", hide_index=True, column_config={
                "Fair %": st.column_config.NumberColumn(format="%.0f%%"),
                "EV %": st.column_config.NumberColumn(format="%+.1f%%")})
        for a in scan["arbs"]:
            legs = " · ".join(
                f"{l['side']} {ui.fmt_american(l['price'])} ({_book(l['book'])}) "
                f"${l['stake']:.0f}" for l in a["legs"])
            st.success(f"**Arb — {a['game']} ({a['market']})**: lock "
                       f"{a['profit_pct']:.2f}% — {legs}")
        if scan["middles"]:
            st.markdown("**🎯 Middles**")
            mdf = pd.DataFrame([{
                "Game": m["game"], "Window": f"{m['low']:g}–{m['high']:g}",
                "Width": m["width"],
                "Over": f"{ui.fmt_american(m['over']['price'])} ({_book(m['over']['book'])})",
                "Under": f"{ui.fmt_american(m['under']['price'])} ({_book(m['under']['book'])})",
                "Breakeven %": m["breakeven"] * 100} for m in scan["middles"]])
            st.dataframe(mdf, width="stretch", hide_index=True, column_config={
                "Breakeven %": st.column_config.NumberColumn(format="%.1f%%")})
        for h in scan["low_holds"]:
            st.info(f"**Low hold — {h['game']} ({h['market']})**: "
                    f"{h['hold'] * 100:.2f}% market margin (soft, near-arb).")
    if not found:
        st.info("No multi-book edges for this slate yet. The scanner reads the "
                "Odds API multi-book capture in the snapshot store — once that's "
                "flowing and books diverge, +EV / arbitrage / middles surface "
                "here automatically. (The math is unit-tested and ready.)")


# ---------------------------------------------------------------------------
# EXPERTS: multi-source consensus (model + BettingPros experts + public)
# ---------------------------------------------------------------------------

def render_experts():
    q = topbar("Expert Consensus")
    date_sel = pick_date()
    from project547 import experts
    rows = experts.consensus_table(slates.get(date_sel, {}), query=q or "")
    st.caption("Where independent reads agree: **our model**, **BettingPros' "
               "expert recommendation** (with their ★ confidence), and the "
               "**public** pick %. Search a player/team/market in the top bar.")
    if not rows:
        st.info("No props with a consensus signal on this slate yet. "
                "BettingPros expert recommendations populate once the BP_USER "
                "premium auth is configured; our model lean shows regardless.")
        return
    multi = [r for r in rows if r["n_sources"] >= 2]
    unanimous = [r for r in rows if r["unanimous"]]
    c = st.columns(4)
    c[0].metric("Props", len(rows))
    c[1].metric("Multi-source", len(multi))
    c[2].metric("Unanimous", len(unanimous))
    c[3].metric("Best EV", f"{max((r['ev'] or 0) for r in rows):+.1%}")

    only_multi = st.toggle("Only props where 2+ sources have a lean",
                           value=bool(multi))
    show = multi if only_multi else rows
    df = pd.DataFrame([{
        "Sport": r["sport"], "Player": r["player"], "Market": r["market"],
        "Line": r["line"] if pd.notna(r["line"]) else None,
        "Consensus": ("✅ " if r["unanimous"] else "") + (r["consensus"] or "split"),
        "Model": r["model_side"] or "—", "BP Expert": r["bp_side"] or "—",
        "Public": r["public_side"] or "—", "BP ★": r["bp_rating"],
        "Agree": f"{r['agree']}/{r['n_sources']}",
        "EV %": (r["ev"] * 100) if r["ev"] is not None else None}
        for r in show])
    st.dataframe(df, width="stretch", hide_index=True, height=600, column_config={
        "EV %": st.column_config.NumberColumn(format="%+.1f%%"),
        "BP ★": st.column_config.NumberColumn(format="%.1f ★")})
    st.caption("✅ = every available source agrees. BP Expert / Public / BP ★ "
               "fill in when BettingPros premium auth is set; Model is always "
               "shown. Click the **EV %** header to sort.")


# ---------------------------------------------------------------------------
# Player profile dialog (click any player name to open)
# ---------------------------------------------------------------------------

def _player_headshot(player: str, game_pk, sport: str | None):
    """MLB headshot URL for a player, via the player_ids map the pipeline
    attaches to each game. None when we don't have an id (or non-MLB)."""
    if sport and sport != "MLB":
        return None
    from project547.names import normalize
    key = normalize(player)
    for _date, sl in (slates or {}).items():
        for sp, blob in (sl or {}).items():
            if sport and sp != sport:
                continue
            for g in blob.get("games", []) or []:
                if game_pk and str(g.get("game_pk")) != str(game_pk):
                    continue
                pid = (g.get("player_ids") or {}).get(key)
                if pid:
                    return ("https://img.mlbstatic.com/mlb-photos/image/upload/"
                            "d_people:generic:headshot:67:current/w_120,q_auto:best/"
                            f"v1/people/{pid}/headshot/67/current")
    return None


def _recent_form_fallback(player: str, sport: str) -> bool:
    """When a player has no posted props yet, show recent game-log form so
    the card still has something useful. Returns True if anything rendered."""
    season = int(default_date[:4]) if default_date else None
    for market, label in (("pitcher_strikeouts", "strikeouts"),
                          ("batter_total_bases", "total bases"),
                          ("batter_hits", "hits"),
                          ("batter_home_runs", "home runs")):
        try:
            series = playerlogs.recent_series(sport, player, market, n=12,
                                              season=season)
        except Exception:
            series = []
        if series:
            st.caption(f"No props posted yet — recent {label} "
                       f"(last {len(series)} games):")
            chart = ui.prop_chart(series, 0.0, f"{player} · {label.title()}")
            if chart is not None:
                st.altair_chart(chart)
            return True
    return False


def _find_player_props(player: str, game_pk, sport: str | None):
    """All prop rows for a player on the loaded slates, plus the sport they
    were found in. game_pk disambiguates same-named players across games.
    Names are normalised (accents/case) so lineup and prop spellings match."""
    from project547.names import normalize
    target = normalize(player)
    found, found_sport = [], sport
    for _date, sl in (slates or {}).items():
        for sp, blob in (sl or {}).items():
            if sport and sp != sport:
                continue
            for p in blob.get("props", []) or []:
                nm = p.get("norm_player") or normalize(p.get("player", ""))
                if nm != target:
                    continue
                if game_pk and str(p.get("game_pk")) != str(game_pk):
                    continue
                found.append(p)
                found_sport = sp
    return found, (found_sport or "MLB")


@st.dialog("Player profile", width="large")
def player_dialog(player: str, game_pk, sport: str | None):
    rows, sport = _find_player_props(player, game_pk, sport)
    team = next((r.get("team") for r in rows if r.get("team")), "")
    opp = next((r.get("opponent") for r in rows if r.get("opponent")), "")
    initials = "".join(w[0] for w in str(player).split()[:2]).upper() or "?"
    sub = " · ".join(x for x in (team, f"vs {opp}" if opp else "") if x)
    shot = _player_headshot(player, game_pk, sport)
    img = (f"<img src='{shot}' style='position:absolute;inset:0;width:60px;"
           f"height:60px;border-radius:50%;object-fit:cover;border:2px solid "
           f"var(--text);' onerror=\"this.style.display='none'\">" if shot else "")
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:15px;margin-bottom:6px;"
        f"padding-bottom:12px;border-bottom:1.5px solid var(--text);'>"
        f"<div style='position:relative;width:60px;height:60px;flex:0 0 auto;'>"
        f"<div style='position:absolute;inset:0;border-radius:50%;display:flex;"
        f"align-items:center;justify-content:center;font-family:var(--disp);"
        f"font-weight:600;font-size:1.3rem;color:var(--bg);background:var(--text);"
        f"border:2px solid var(--text);'>{initials}</div>{img}</div>"
        f"<div><div style='font-family:var(--disp);font-size:1.6rem;font-weight:600;"
        f"letter-spacing:.03em;'>{player}</div>"
        f"<div style='color:var(--muted);font-size:0.82rem;text-transform:uppercase;"
        f"letter-spacing:.06em;'>{sub}</div></div></div>",
        unsafe_allow_html=True)

    if not rows:
        if not _recent_form_fallback(player, sport):
            st.info("No props or game logs for this player yet — MLB props firm "
                    "up once lineups are official (~2-4h before first pitch).")
        return

    recs = []
    for r in rows:
        ev = pd.to_numeric(r.get("ev"), errors="coerce")
        mop = pd.to_numeric(r.get("model_over_prob"), errors="coerce")
        recs.append({
            "Market": ui.short_market(r.get("market", "")),
            "Line": pd.to_numeric(r.get("line"), errors="coerce"),
            "Proj": pd.to_numeric(r.get("projection"), errors="coerce"),
            "Model Over %": mop * 100 if pd.notna(mop) else None,
            "EV %": ev * 100 if pd.notna(ev) else None,
            "Pick": ("Over" if pd.notna(mop) and mop >= 0.5 else
                     "Under" if pd.notna(mop) else "—"),
        })
    view = pd.DataFrame(recs)

    sort = st.selectbox(
        "Sort props by",
        ["Edge (EV %)", "Market", "Line", "Projection", "Model Over %"],
        key=f"pp_sort_{player}")
    by, asc = {"Edge (EV %)": ("EV %", False), "Market": ("Market", True),
               "Line": ("Line", True), "Projection": ("Proj", False),
               "Model Over %": ("Model Over %", False)}[sort]
    view = view.sort_values(by, ascending=asc, na_position="last").reset_index(drop=True)

    def _ev_color(v):
        if not isinstance(v, (int, float)) or pd.isna(v):
            return ""
        if v >= min_edge * 100:
            return "color:var(--good);font-weight:700;"
        return "color:var(--neg);" if v < 0 else ""
    sty = (view.style.map(_ev_color, subset=["EV %"])
           .format({"Line": "{:g}", "Proj": "{:.2f}", "Model Over %": "{:.0f}%",
                    "EV %": "{:+.1f}%"}, na_rep="—"))
    st.dataframe(sty, width="stretch", hide_index=True)

    markets = view["Market"].tolist()
    pick = st.selectbox("Open the full breakdown for", ["—"] + markets,
                        key=f"pp_detail_{player}")
    if pick != "—":
        prop = next((r for r in rows if ui.short_market(r.get("market", "")) == pick),
                    None)
        if prop:
            st.divider()
            render_prop_detail(sport, prop, [])


# Carry the "remember sign-in" token through every player link so a click
# (which reloads the page) doesn't drop it and force a re-login.
ui.set_link_keep({"k": st.query_params.get("k")})

_clicked = st.query_params.get("player")
if _clicked:
    _gk = st.query_params.get("game")
    _sp = st.query_params.get("s")
    for _k in ("player", "game", "s"):
        try:
            del st.query_params[_k]
        except KeyError:
            pass
    player_dialog(_clicked, _gk, _sp)


def _backfill_seasons(sport: str) -> list[int]:
    base = config.REPO_ROOT / "data" / "history" / "backfill" / sport.lower()
    if not base.exists():
        return []
    return sorted(int(p.name) for p in base.iterdir()
                  if p.is_dir() and p.name.isdigit() and (p / "games.json.gz").exists())


@st.cache_data(ttl=3600, show_spinner=False)
def _run_backtest(sport: str, seasons: tuple) -> dict:
    from project547 import backtest
    return backtest.run_game_backtest(sport, list(seasons), draws=800, detail=True)


def _replay_card_html(g: dict) -> str:
    def mark(hit):
        if hit is None:
            return "<span style='color:var(--muted);'>— push</span>"
        return ("<span style='color:var(--good);font-weight:700;'>✓</span>" if hit
                else "<span style='color:var(--neg);font-weight:700;'>✗</span>")
    hm, aw = g["home"], g["away"]
    hwp = g["home_win_prob"]
    fav_pct = max(hwp, 1 - hwp) * 100
    close = []
    if g.get("close_spread") is not None:
        close.append(f"{hm.split()[-1]} {g['close_spread']:+g}")
    if g.get("close_total") is not None:
        close.append(f"O/U {g['close_total']:g}")
    if g.get("home_ml") is not None and g.get("away_ml") is not None:
        close.append(f"ML {g['away_ml']:+g}/{g['home_ml']:+g}")
    res = [f"ML {mark(g['ml_hit'])} {g['ml_fav'].split()[-1]}"]
    if "ats_hit" in g:
        res.append(f"ATS {mark(g['ats_hit'])} {g['ats_pick'].split()[-1]}")
    if "tot_hit" in g:
        res.append(f"Tot {mark(g['tot_hit'])} {g['tot_pick']}")
    clv = g.get("clv")
    clv_s = (f" · CLV <b style='color:{'var(--good)' if clv >= 0 else 'var(--neg)'};'>"
             f"{clv:+.1%}</b>") if clv is not None else ""
    return (
        "<div style='background:var(--card);border:1px solid var(--line);border-radius:12px;"
        "padding:12px 14px;margin-bottom:10px;'>"
        f"<div style='font-weight:700;'>{aw} <span style='color:var(--muted);'>@</span> {hm}"
        f"<span style='float:right;'>{g['away_score']}–{g['home_score']}</span></div>"
        f"<div style='color:var(--acc2);font-size:0.8rem;margin-top:4px;'>Model: "
        f"{g['ml_fav'].split()[-1]} {fav_pct:.0f}% · proj total {g['proj_total']:.0f}</div>"
        f"<div style='color:var(--muted);font-size:0.8rem;'>Close: {' · '.join(close) or '—'}</div>"
        f"<div style='font-size:0.85rem;margin-top:6px;'>{' &nbsp; '.join(res)}{clv_s}</div>"
        "</div>")


def _slate_record(games: list[dict]) -> str:
    """Model's leans graded across a slate: W-L (+units) per market. ML priced
    at the favorite's best closing number; ATS/Total at -110."""
    def dec(a):
        return 1 + (100 / abs(a) if a < 0 else a / 100)
    parts = []
    # moneyline
    w = l = 0
    u = 0.0
    priced = True
    for g in games:
        if g.get("ml_hit") is None:
            continue
        w, l = (w + 1, l) if g["ml_hit"] else (w, l + 1)
        price = g.get("home_ml") if g["ml_fav"] == g["home"] else g.get("away_ml")
        if price is None:
            priced = False
        else:
            u += (dec(price) - 1) if g["ml_hit"] else -1
    if w + l:
        parts.append(f"ML {w}–{l}" + (f" ({u:+.1f}u)" if priced else ""))
    # ATS + total at -110
    for label, key in (("ATS", "ats_hit"), ("Total", "tot_hit")):
        w = l = 0
        u = 0.0
        for g in games:
            h = g.get(key)
            if h is None:
                continue
            if h:
                w += 1
                u += 100 / 110
            else:
                l += 1
                u -= 1
        if w + l:
            parts.append(f"{label} {w}–{l} ({u:+.1f}u)")
    return " · ".join(parts)


def render_backtest():
    st.markdown("<div class='osp-title'>🧪 Model backtest</div>", unsafe_allow_html=True)
    tab_g, tab_p = st.tabs(["🎮 Game model", "🎯 Prop calibration"])
    with tab_g:
        _render_game_model()
    with tab_p:
        _render_prop_calibration()


def _render_prop_calibration():
    st.caption("Is our P(over) accurate? **Gap** = mean predicted P(over) − the "
               "actual over-rate; |gap| under ~1% is well calibrated. Graded vs "
               "model-derived lines (we don't have historical PrizePicks lines).")
    path = config.REPO_ROOT / "data" / "history" / "calibration" / "props_calibration_summary.json"
    if not path.exists():
        st.info("Prop calibration summary not generated yet.")
        return
    cal = json.loads(path.read_text())
    for sport, markets in cal.items():
        rows = [{"Market": ui.short_market(m), "n": c.get("n"),
                 "MAE": c.get("projection_mae"),
                 "Pred over": c.get("mean_pred_over"),
                 "Actual over": c.get("empirical_over"),
                 "Gap": c.get("calibration_gap")}
                for m, c in markets.items() if c.get("n")]
        if not rows:
            continue
        st.markdown(f"**{sport}**")
        df = pd.DataFrame(rows)
        sty = df.style.map(
            lambda v: ("color:var(--good);" if isinstance(v, (int, float)) and abs(v) < 0.01
                       else "color:#ffa657;" if isinstance(v, (int, float)) and abs(v) < 0.025
                       else "color:var(--neg);" if isinstance(v, (int, float)) else ""),
            subset=["Gap"]).format(
            {"MAE": "{:.3f}", "Pred over": "{:.3f}", "Actual over": "{:.3f}",
             "Gap": "{:+.4f}"}, na_rep="—")
        st.dataframe(sty, hide_index=True, width="stretch")


def _render_game_model():
    st.caption("Walk-forward: each game projected from only prior data, then "
               "graded against the result and the **closing line** (CLV / ROI). "
               "Historical evaluation of the model — not live picks.")
    sports = [s for s in ("NFL", "NBA", "NHL") if _backfill_seasons(s)]
    if not sports:
        st.info("No backtest data is committed yet.")
        return
    c1, c2 = st.columns([1, 3])
    sport = c1.selectbox("Sport", sports, key="bt_sport")
    seasons_all = _backfill_seasons(sport)
    default = seasons_all[-4:] if len(seasons_all) >= 4 else seasons_all
    seasons = c2.multiselect("Seasons", seasons_all, default=default,
                             key=f"bt_seasons_{sport}")
    if not seasons:
        st.info("Pick at least one season.")
        return
    with st.spinner(f"Backtesting {sport} over {len(seasons)} season(s)…"):
        res = _run_backtest(sport, tuple(sorted(seasons)))
    if not res.get("n_games_graded"):
        st.warning("No games graded for that selection.")
        return

    ml, tot, cl = res["moneyline"], res["total"], res["closing_line"]
    m = st.columns(5)
    m[0].metric("Games graded", f"{res['n_games_graded']:,}")
    m[1].metric("Brier", ml["brier"])
    m[2].metric("Log loss", ml["log_loss"])
    m[3].metric("Favorite hit",
                f"{ml['favorite_hit_rate'] * 100:.1f}%" if ml["favorite_hit_rate"] else "—")
    m[4].metric("Total MAE", tot["mae"])

    section_header("Closing-line value & ROI")
    rows = []
    for label, key in (("Moneyline", "moneyline_bets"), ("Spread", "spread_bets"),
                       ("Total", "total_bets")):
        b = cl.get(key) or {}
        rows.append({"Market": label, "Bets": b.get("bets"),
                     "Win %": round(b["win_rate"] * 100, 1) if b.get("win_rate") else None,
                     "ROI %": b.get("roi_pct"), "Units": b.get("units")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    clv, sclv = cl.get("avg_clv_vs_fair"), cl.get("avg_spread_clv_vs_fair")
    bits = [f"moneyline {clv:+.4f}"] if clv is not None else []
    if sclv is not None:
        bits.append(f"spread {sclv:+.4f}")
    st.caption(f"Avg CLV vs the de-vigged close — {', '.join(bits)} · matched "
               f"{cl.get('games_matched', 0):,} games. Positive = beating the close; "
               "negative ROI means the model trails the market (expected on efficient "
               "sides/totals).")

    cal = res.get("calibration") or []
    if cal:
        section_header("Win-probability calibration")
        cdf = pd.DataFrame(cal)
        chart = cdf.set_index("predicted")[["empirical"]].copy()
        chart["ideal"] = chart.index
        st.line_chart(chart)
        st.caption("Empirical home-win rate vs predicted, by 10% bin — the closer "
                   "**empirical** tracks **ideal** (the diagonal), the better calibrated.")

    games = res.get("games") or []
    if games:
        section_header("Game-by-game replay", "📼")
        def _label(g):
            return f"{g['season']} · Week {g['week']}" if g.get("week") else g["date"]
        slates: dict = {}
        for g in games:
            slates.setdefault(_label(g), []).append(g)
        order = sorted(slates, key=lambda L: max(
            (x["season"] or 0, x["week"] or 0, x["date"]) for x in slates[L]),
            reverse=True)
        pick = st.selectbox("Slate", order, key=f"bt_slate_{sport}")
        slate_games = sorted(slates[pick], key=lambda x: (x["date"], x["home"]))
        rec = _slate_record(slate_games)
        if rec:
            st.markdown(f"**Model leans this slate:** {rec}")
        st.caption("Each card: the model's pre-game lean vs the closing line and the "
                   "final result. ✓ = the model's side cashed at the close. "
                   "Units: ML at the favorite's best price, ATS/Total at -110.")
        cols = st.columns(2)
        for i, g in enumerate(slate_games):
            cols[i % 2].markdown(_replay_card_html(g), unsafe_allow_html=True)

        if sport in ("NFL", "NCAAF"):   # teamstats matchup engine = football
            section_header("Full research card (historical)", "📋")
            labels = [f"{g['away']} @ {g['home']}" for g in slate_games]
            sel = st.selectbox("Matchup", labels, key=f"bt_card_{sport}")
            _render_replay_research_card(sport, slate_games[labels.index(sel)])


def _render_replay_research_card(sport: str, gd: dict):
    """Full teamstats research card (form, off-vs-def splits, ranks) for a
    historical matchup — built from backdata as of game day, no lookahead."""
    from scipy.stats import norm
    home, away, date = gd["home"], gd["away"], gd["date"]
    m = _matchup(sport, home, away, date)
    if not m:
        st.info("Team-stat splits aren't available for this matchup yet.")
        return
    sig = SPORTS[sport].sigma_margin or 13.5
    hwp = min(max(gd["home_win_prob"], 1e-4), 1 - 1e-4)
    margin = sig * float(norm.ppf(hwp))            # implied home margin
    total = gd["proj_total"]
    g = {"away_team": away, "home_team": home, "game_pk": f"replay-{date}",
         "game_time": f"{date}T17:00:00Z", "proj_total": total,
         "home_exp": (total + margin) / 2, "away_exp": (total - margin) / 2,
         "home_win_prob": round(gd["home_win_prob"], 4),
         "away_win_prob": round(1 - gd["home_win_prob"], 4),
         "total_line": gd.get("close_total")}
    st.markdown(ui.research_card_html(sport, g, m, min_edge), unsafe_allow_html=True)
    st.caption(f"As-of {date} (pre-game, no lookahead). **Final: {away} "
               f"{gd['away_score']} – {gd['home_score']} {home}** · close: "
               f"spread {gd.get('close_spread')}, O/U {gd.get('close_total')}.")


# ---------------------------------------------------------------------------
# Edge Builder · Prompt Engine · Research Hub docs (360Five tabs)
# ---------------------------------------------------------------------------

def render_edge_builder():
    from app import byoe
    byoe.render()


def render_prompt_engine():
    st.markdown("<div class='osp-hero'><div class='osp-title'>🤖 Prompt Engine"
                "</div></div>", unsafe_allow_html=True)
    st.caption("Turn a slate into an AI-ready brief with a risk posture you "
               "choose — Conservative, Standard, or Aggressive. Copy it into "
               "Claude.ai on your own subscription, or run it in-app. A tool, "
               "not a take.")
    scope = st.selectbox("Scope", ["Whole slate"] + NAV_SPORTS)
    day = slates.get(default_date, {})
    board = ui.build_best_bets(day, min_edge)
    if scope != "Whole slate" and not board.empty:
        board = board[board["sport"] == scope]
    if board is None or board.empty:
        st.info("No edges on this slate/scope yet to brief on — lower **Min "
                "edge** in the sidebar, or check back as lines post.")
        return
    ai_block(ui.ai_brief_board(board, default_date), key="prompt_engine")


_DOC_PAGES = {
    "Brand & voice": "docs/BRAND.md",
    "Accuracy roadmap": "docs/ACCURACY_ROADMAP.md",
    "Research synthesis": "docs/research/00-synthesis.md",
    "Consolidation & provenance": "docs/CONSOLIDATION.md",
}


def render_docs():
    st.markdown("<div class='osp-hero'><div class='osp-title'>🔬 Research Hub"
                "</div></div>", unsafe_allow_html=True)
    st.caption("Show the work. How the model is built, what we validated, and "
               "what we're still chasing — the opposite of a black box.")
    pick = st.selectbox("Document", list(_DOC_PAGES))
    path = config.REPO_ROOT / _DOC_PAGES[pick]
    st.markdown(path.read_text() if path.exists()
                else f"_{_DOC_PAGES[pick]} not found._")


def render_sharpsheet_browser():
    st.markdown("<div class='osp-hero'><div class='osp-title'>📊 Sharp Sheets"
                "</div></div>", unsafe_allow_html=True)
    st.caption("The research starter. Pick a matchup for the full breakdown — "
               "offense vs defense, league ranks, the edges, and the model's "
               "read. Same sheet that opens from a play's chip.")
    date_sel = pick_date()
    day = slates.get(date_sel, {})
    sports_in = [s for s in NAV_SPORTS if (day.get(s, {}) or {}).get("games")]
    if not sports_in:
        st.info("No matchups on this slate yet.")
        return
    sport = st.selectbox("Sport", sports_in)
    games = day.get(sport, {}).get("games", []) or []
    labels = [f"{g.get('away_team')} @ {g.get('home_team')}" for g in games]
    pick = st.selectbox("Matchup", labels)
    if pick:
        render_research_card(sport, games[labels.index(pick)], date_sel)


def _extra_team_head(t: dict, align: str) -> str:
    logo = (f"<img src='{t['logo']}' width='44' style='vertical-align:middle'/> "
            if t.get("logo") else "")
    rank = f"<span style='opacity:.55'>#{t['rank']}</span> " if t.get("rank") else ""
    sub = " · ".join(x for x in [t.get("record"), t.get("form")] if x)
    return (f"<div style='text-align:{align};min-width:40%'>{logo}{rank}"
            f"<b style='font-size:1.05rem'>{t.get('name','')}</b>"
            f"<div style='opacity:.65;font-size:.82rem'>{sub}</div></div>")


def _extra_sheet(g: dict, bp_sport: str | None = None):
    a, h = g["away"], g["home"]
    st.markdown(
        "<div class='osp-hero'><div style='display:flex;justify-content:space-between;"
        f"align-items:center;gap:12px'>{_extra_team_head(a, 'left')}"
        "<div style='opacity:.5;font-weight:700'>@</div>"
        f"{_extra_team_head(h, 'right')}</div></div>", unsafe_allow_html=True)
    meta = " · ".join(x for x in [
        g.get("league"), g.get("detail"), g.get("venue"),
        ("neutral site" if g.get("neutral") else None)] if x)
    if meta:
        st.caption(meta)
    o = g.get("odds") or {}
    if o:
        bits = [o.get("details"),
                (f"O/U {o['over_under']}" if o.get("over_under") is not None else None),
                o.get("provider")]
        line = " · ".join(b for b in bits if b)
        if line:
            st.markdown("**Market:** " + line)
    from project547 import baseline as _baseline
    b = _baseline.market_baseline(o.get("home_ml"), o.get("away_ml"), o.get("draw_ml"))
    if b:
        st.markdown("**📈 Baseline projection** (de-vigged market): "
                    + " · ".join(f"{k} {v}" for k, v in b.as_pct().items()))
        st.caption("The market-implied baseline — the number every model is "
                   "measured against. Our own adjustment + tracking layer onto "
                   "this; BettingPros gives a sharper baseline where it covers "
                   "the sport.")
    # BettingPros: sharper consensus baseline + validator + their prop model
    if bp_sport:
        from project547 import bp as _bp
        d10 = (g.get("date") or "")[:10]
        hn, an = h.get("name", ""), a.get("name", "")
        bpb = _bp.game_baseline(bp_sport, d10, hn, an)
        if bpb:
            st.markdown("**🎯 BettingPros baseline** (consensus, de-vigged): "
                        + " · ".join(f"{k} {v}" for k, v in bpb.as_pct().items()))
            if b:
                c = _baseline.compare(b.home_wp, bpb.home_wp)
                st.caption("✅ Market and BettingPros agree here."
                           if c["agree"] else
                           f"⚠️ Market vs BettingPros differ — "
                           f"{c['label'].replace('home', 'the home side')}.")
        bprops = _bp.prop_projections(bp_sport, d10, teams=[hn, an])
        if bprops:
            try:
                from project547.clients import bettingpros as _bpc
                mlk = _bpc.market_lookup(bp_sport)
            except Exception:
                mlk = {}
            st.markdown("**BettingPros player projections** — their model as our "
                        "second opinion (baseline / validator, not gospel).")
            st.dataframe(pd.DataFrame([{
                "Player": p["participant"],
                "Market": (mlk.get(p["market_id"]) or {}).get("name") or p["market_id"],
                "Line": p["bp_line"], "BP proj": p["bp_projection"],
                "EV%": (f"{p['bp_ev']*100:+.1f}"
                        if isinstance(p["bp_ev"], (int, float)) else None),
                "Side": p["bp_recommended_side"], "Rating": p["bp_bet_rating"],
            } for p in bprops]), hide_index=True, width="stretch")
    labels = [s["label"] for s in a["stats"]]
    for s in h["stats"]:
        if s["label"] not in labels:
            labels.append(s["label"])
    if labels:
        am = {s["label"]: s["value"] for s in a["stats"]}
        hm = {s["label"]: s["value"] for s in h["stats"]}
        df = pd.DataFrame([{a.get("name", "Away"): am.get(l, "—"), "Stat": l,
                            h.get("name", "Home"): hm.get(l, "—")} for l in labels])
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.caption("No season stats published for this matchup yet.")
    st.caption("Stats & logos: ESPN (free). We don't model this league — the "
               "sheet is here for *your* read, not ours.")


def _extra_field(g: dict):
    """Leaderboard / field events (golf, racing) — schedule, field, odds."""
    st.markdown(f"<div class='osp-hero'><div class='osp-title'>{g.get('name','')}"
                "</div></div>", unsafe_allow_html=True)
    meta = " · ".join(x for x in [g.get("league"), g.get("detail"),
                                  g.get("venue")] if x)
    if meta:
        st.caption(meta)
    if g.get("odds") and g["odds"].get("details"):
        st.markdown(f"**Market:** {g['odds']['details']}")
    rows = [{"#": c.get("rank") or i + 1, "Competitor": c["name"],
             "Score / Pos": c.get("score") or ("✓" if c.get("winner") else "—")}
            for i, c in enumerate(g.get("competitors", []))]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Schedule, field & data: ESPN (free). We don't model this event "
               "— it's here so you don't have to leave to research it.")


def render_other_sports():
    st.markdown("<div class='osp-hero'><div class='osp-title'>🌍 Other Sports"
                "</div></div>", unsafe_allow_html=True)
    st.caption("Sharp Sheets for in-season leagues we don't model — full stats, "
               "logos, form and odds, so you can make your own read. "
               "Data & logos: ESPN (free).")
    from project547.clients import espn_extra
    groups = espn_extra.leagues_by_group()
    cg, cl, cd = st.columns([1, 2, 2])
    group = cg.selectbox("Sport", list(groups))
    opts = groups[group]
    label = cl.selectbox("League", [n for _k, n in opts])
    key = next(k for k, n in opts if n == label)
    default_d = pd.to_datetime(default_date).date() if default_date else None
    date_sel = cd.date_input("Date", value=default_d).isoformat()
    try:
        evs = espn_extra.events(key, date_sel)
    except Exception as e:  # network/parse — never crash the page
        st.warning(f"Couldn't load {label} from ESPN: {e}")
        return
    if not evs:
        st.info(f"No {label} events on {date_sel}.")
        return
    labels = [g["name"] for g in evs]
    pick = st.selectbox("Event", labels)
    g = evs[labels.index(pick)]
    if g.get("is_matchup"):
        from project547 import baseline as _baseline
        _extra_sheet(g, _baseline.bp_sport_for(group))
    else:
        _extra_field(g)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

def render_ticker():
    """The two brand numbers, pinned to the top of every page so the whole app
    stands on the 54.7 promise — Model (engine) and Curated (2-6%) hit rates."""
    m = results.hero_metrics(load_ledger())

    def chip(label, pct):
        col = _hero_band(pct)
        val = f"{pct:.1f}%" if pct is not None else "—"
        return (f"<span style='font-family:var(--disp);font-size:.64rem;"
                f"letter-spacing:.08em;color:var(--muted);text-transform:uppercase;'>"
                f"{label}</span> <b style='font-family:var(--disp);font-size:1.02rem;"
                f"color:{col};'>{val}</b>")

    st.markdown(
        "<div style='display:flex;align-items:center;gap:22px;"
        "border-bottom:1.5px solid var(--text);padding:4px 2px 8px;margin-bottom:10px;'>"
        "<span style='margin-right:auto;font-family:var(--disp);font-weight:700;"
        "letter-spacing:.06em;font-size:.92rem;'>360FIVE</span>"
        f"{chip('Engine', m['engine_pct'])}"
        "<span style='color:var(--line);'>|</span>"
        f"{chip('Curated 2–6%', m['curated_pct'])}"
        "<span style='font-size:.6rem;color:var(--faint);font-family:var(--disp);"
        "letter-spacing:.06em;'>UPDATED DAILY</span></div>",
        unsafe_allow_html=True)


if section != "HOME":  # Home leads with the full glowing heroes already
    render_ticker()

@st.cache_data(show_spinner=False)
def _workbook_lite_bytes(stamp: str) -> bytes:
    # On-demand fallback: fast, no Research Hub images (stamp = generated_at).
    return workbook.build_workbook_bytes(data)


def render_workbook():
    topbar("Daily workbook", with_search=False)
    section_header("Daily Wager Workbook", icon="📥")
    st.markdown(
        "An editable Excel/Google-Sheets workbook of today's slate. The model's "
        "win probability is **locked**; you type the price **your** sportsbook is "
        "showing into the yellow cells, and the edge, EV, and ¼-Kelly stake "
        "recompute in-cell — in Excel *or* Google Sheets, offline. The odds are "
        "the only thing that changes all day, so they're the only thing you edit.")
    stamp = str(data.get("generated_at", ""))
    # Prefer the full pre-built file (Research Hub + premium card images, baked
    # hourly); fall back to a fast on-demand lite build if it's missing.
    prebuilt, _ = workbook.default_paths()
    full = False
    try:
        if prebuilt.exists():
            xlsx = prebuilt.read_bytes()
            full = True
        else:
            xlsx = _workbook_lite_bytes(stamp)
    except Exception as e:  # never let a build error take down the page
        st.error(f"Couldn't build the workbook right now: {e}")
        return
    fname = f"project547_workbook_{(data.get('primary_date') or 'latest')}.xlsx"
    st.download_button("⬇ Download today's workbook", data=xlsx, file_name=fname,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary", width="stretch")
    hub = ("Research Hub (premium matchup cards) · " if full else "")
    st.caption(f"Built from the {stamp[:16].replace('T', ' ')} slate · "
               f"{len(xlsx) // 1024} KB · tabs: Read Me · Settings · Top Plays · "
               f"{hub}per-sport games & props · Track Record")
    with st.expander("What's inside & how to use it"):
        st.markdown(
            "- **Read Me** — how it works + what each Verdict means.\n"
            "- **Settings** — set your bankroll and Kelly fraction once; every "
            "tab reprices.\n"
            "- **Top Plays** — the biggest model edges today (nothing's a lock).\n"
            "- **Research Hub** — a premium matchup card per game (offense-vs-"
            "defense, ranks, advantages, gauges) plus an all-windows table.\n"
            "- **Per-sport Games & Props** — every market, bring your own odds.\n"
            "- **Track Record** — receipts, losses included.\n\n"
            "Tip: opening a protected `.xlsx` in Google Sheets drops cell "
            "protection, so just remember **yellow = the cells you edit**.")
    st.caption(workbook.DISCLAIMER)


def _topnav_row(items, active, on_pick, label_fn, per_row, keyp):
    """Render `items` as a wrapped grid of buttons; the active one is primary.
    `on_pick(item)` fires on click (it stashes `_pending_nav` + reruns)."""
    for i in range(0, len(items), per_row):
        chunk = items[i:i + per_row]
        cols = st.columns(len(chunk))
        for c, it in zip(cols, chunk):
            if c.button(label_fn(it), key=f"{keyp}{it}", width="stretch",
                        type="primary" if it == active else "secondary"):
                on_pick(it)


# Mobile top-strip nav: mirrors the sidebar radios but as a tab strip. Hidden on
# desktop via CSS (`.st-key-osp_topnav`), where the left sidebar drives nav; on
# narrow viewports the sidebar collapses to a hamburger and this is the primary
# nav (BettorSheets-style: tap a sport, scroll every sheet).
with st.container(key="osp_topnav"):
    _areas = [g for g in NAV_GROUPS if NAV_GROUPS[g]]

    def _pick_area(a):
        _pgs = NAV_GROUPS[a]
        _sec = _pgs[0] if len(_pgs) == 1 else st.session_state.get(f"nav_{a}", _pgs[0])
        st.session_state["_pending_nav"] = {"area": a, "section": _sec}
        st.rerun()

    _topnav_row(_areas, area, _pick_area, lambda a: a, 5, "tnav_a_")
    _pages = NAV_GROUPS[area]
    if len(_pages) > 1:
        def _pick_page(p):
            st.session_state["_pending_nav"] = {"area": area, "section": p}
            st.rerun()

        _topnav_row(_pages, section, _pick_page,
                    lambda p: _PAGE_LABELS.get(p, p.title()), 3, "tnav_p_")


if section == "HOME":
    render_home()
elif section in NAV_SPORTS:
    (render_match_sport if section in MATCH_MODEL_SPORTS
     else render_sport)(section)
elif section == "SCORES":
    render_scores()
elif section == "WORKBOOK":
    render_workbook()
elif section == "PLAYS":
    render_plays()
elif section == "PLAYS_DETAIL":
    render_plays_detail()
elif section == "SHARPSHEET":
    render_sharpsheet_browser()
elif section == "OTHER_SPORTS":
    render_other_sports()
elif section == "EDGES":
    render_edges()
elif section == "EXPERTS":
    render_experts()
elif section == "DFS":
    render_dfs()
elif section == "TOOLS":
    render_tools()
elif section == "EDGE_BUILDER":
    render_edge_builder()
elif section == "PROMPT_ENGINE":
    render_prompt_engine()
elif section == "DOCS":
    render_docs()
elif section == "PERFORMANCE":
    render_performance()
elif section == "BACKTEST":
    render_backtest()
