"""Shared 360Five theme: palette tokens + the global CSS, with no Streamlit
imports so the offline static-site build (scripts/build_static.py) renders the
exact same look as the live app. app/dashboard.py imports these — single
source of truth for the palette and CSS.
"""
from __future__ import annotations

PALETTES = {
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

THEME_CSS = """
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


_TYPE_VARS = ("--disp:'Archivo', system-ui, -apple-system, sans-serif; "
              "--font:'Archivo', system-ui, -apple-system, sans-serif; "
              "--mono:'Spline Sans Mono', ui-monospace, 'JetBrains Mono', "
              "Menlo, Consolas, monospace; ")


def theme_css(theme: str = "dark") -> str:
    p = PALETTES.get(theme, PALETTES["dark"])
    root = (":root { " + "".join(f"--{k}:{v}; " for k, v in p.items())
            + _TYPE_VARS + "}")
    return f"<style>{root}{THEME_CSS}</style>"


def theme_css_both() -> str:
    """Emit BOTH palettes, scoped by ``data-theme`` on <html>, so a toggle can
    swap them live: light = cream almanac, dark = classic black. Shared type
    vars and the base rules go on every root. Default (no attribute) = light."""
    def _vars(name):
        return "".join(f"--{k}:{v}; " for k, v in PALETTES[name].items())
    css = (f":root, :root[data-theme=\"light\"] {{ {_vars('cream')}{_TYPE_VARS}}}"
           f":root[data-theme=\"dark\"] {{ {_vars('dark')}{_TYPE_VARS}}}")
    return f"<style>{css}{THEME_CSS}</style>"


