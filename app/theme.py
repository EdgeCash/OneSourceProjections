"""Visual theme + copy layer — the "Case File" (Law & Order) skin.

Design-and-vibe only: this module owns the palette, fonts, global CSS, the
courtroom copy map, and a few themed HTML helpers (title cards, rubber
stamps, the "dun-dun"). It imports nothing from the app, touches no model
logic, and exposes pure functions so the rest of the app can stay almost
exactly as it was — every renderer already paints with CSS variables, so
swapping the palette here re-skins the whole site.

Two palettes ship: ``docket`` (the Law & Order case-file look, default) and
``original`` (the old neon-green terminal), toggled live from the sidebar so
you can A/B the lighting. Picks are still picks; only the lacquer changes.

Glossary the copy leans on (from the franchise + courtroom procedure):
  Docket .......... the day's slate of charges (our picks)
  Evidence/Exhibit  the matchup graphics that justify a wager
  The People ...... the model (the prosecution)
  Verdict ......... a market call: INDICTED (play) / DISMISSED (pass)
  Strength of case  model confidence
  Conviction rate   the forward-tested track record (win rate / CLV)
  Persons of int.   players / teams under scrutiny
"""

from __future__ import annotations

import streamlit as st

THEMES = ("docket", "original")
DEFAULT_THEME = "docket"
STATE_KEY = "osp_theme"

# ---------------------------------------------------------------------------
# Palettes — every CSS variable the app's inline styles reference. The
# `original` palette is the identity map (each token = its old hard-coded
# hex), so toggling to it restores the prior look exactly.
# ---------------------------------------------------------------------------

_ORIGINAL = {
    "--bg": "#080b12", "--bg2": "#0b0f18", "--bg3": "#070a11",
    "--panel": "#0e131d", "--card": "#121826",
    "--line": "#1e2636", "--line2": "#1c2330", "--line3": "#161d29",
    "--faint2": "#39414d",
    "--acc": "#00e676", "--acc2": "#22d3ee",
    "--neg": "#ff4d6d", "--neg2": "#ff6b6b", "--warn": "#e3b341",
    "--win": "#2ea043", "--pos": "#2ea043",
    # vivid L&O accents — electric blue + bright red, for verdicts & rates
    "--elec": "#22d3ee", "--vivid": "#ff4d6d",
    # text-tier variants (AA-safe on dark) for oxblood/info used as small text
    "--neg-text": "#ff6b6b", "--info-text": "#22d3ee",
    "--muted": "#8b949e", "--muted2": "#7d8794", "--faint": "#6e7781",
    "--text": "#e6edf3", "--text2": "#c9d1d9",
    "--blur1": "#1b2230", "--blur2": "#222b3c",
    # accents as rgb triples for rgba() glows/gradients
    "--acc-rgb": "0,230,118", "--acc2-rgb": "34,211,238", "--neg-rgb": "255,77,109",
    "--pos-rgb": "46,160,67",
}

# The Case File. Parchment + ink, brass scales, precinct blue, stamp oxblood,
# manila-in-shadow cards — chosen to escape the casino green/black sea while
# reading authoritative, not cheesy (sober premium, per the research).
_DOCKET = {
    "--bg": "#0a0a0b", "--bg2": "#0c0c0e", "--bg3": "#060607",
    "--panel": "#111014", "--card": "#16140f",
    "--line": "#2b281f", "--line2": "#232019", "--line3": "#1f1c16",
    "--faint2": "#3b3529",
    "--acc": "#c69a3f",        # brass — scales of justice (PLAY/highlight)
    "--acc2": "#5e87b3",       # precinct blue — the logo's neon glow
    "--neg": "#b23b3b",        # evidence-stamp oxblood — FILLS/STAMPS only
    "--neg2": "#cf6a6a",
    "--warn": "#cf9a33",       # amber (NOTE / caution)
    "--win": "#6f8b3a", "--pos": "#6f8b3a",   # olive "convicted" green
    # vivid L&O title-sequence accents — electric blue (LAW) + bright red
    # (ORDER) — used on verdicts, conviction rates, and the twin rules.
    "--elec": "#3d8bff", "--vivid": "#ff4438",
    # text-tier variants — oxblood/info fail AA as small text on dark, so
    # these lifted tiers carry any oxblood/info *text* (loss numbers, labels)
    "--neg-text": "#cf6a6a", "--info-text": "#7ea3c9",
    "--muted": "#9c9279",      # aged-paper taupe
    "--muted2": "#8a8068", "--faint": "#6e6555",   # --faint = NON-TEXT chrome only
    "--text": "#ece1cb",       # parchment white
    "--text2": "#d7ccb4",
    "--blur1": "#211d15", "--blur2": "#2b2619",
    "--acc-rgb": "198,154,63", "--acc2-rgb": "94,135,179", "--neg-rgb": "178,59,59",
    "--pos-rgb": "111,139,58",
}

PALETTES = {"docket": _DOCKET, "original": _ORIGINAL}

_FONTS = {
    "docket": {
        "import": ("@import url('https://fonts.googleapis.com/css2?"
                   "family=Cinzel:wght@500;600;700&"
                   "family=Courier+Prime:wght@400;700&"
                   "family=Inter:wght@400;500;600;700&display=swap');"),
        # glyphic/incised serif for the title-card "carved authority" feel
        "disp": "'Cinzel', Georgia, 'Times New Roman', serif",
        # typewriter for documents / rap sheets / numbers
        "mono": "'Courier Prime', 'Courier New', monospace",
        "body": "'Inter', system-ui, sans-serif",
    },
    "original": {
        "import": ("@import url('https://fonts.googleapis.com/css2?"
                   "family=Space+Grotesk:wght@500;600;700&display=swap');"),
        "disp": "'Space Grotesk', system-ui, sans-serif",
        "mono": "ui-monospace, SFMono-Regular, monospace",
        "body": "system-ui, -apple-system, sans-serif",
    },
}


# ---------------------------------------------------------------------------
# Theme selection (session-scoped)
# ---------------------------------------------------------------------------

def active() -> str:
    t = st.session_state.get(STATE_KEY, DEFAULT_THEME)
    return t if t in PALETTES else DEFAULT_THEME


def is_docket() -> bool:
    return active() == "docket"


def _vars_css(theme: str) -> str:
    pal = PALETTES[theme]
    fonts = _FONTS[theme]
    rows = "".join(f"{k}:{v};" for k, v in pal.items())
    rows += f"--disp:{fonts['disp']};--mono:{fonts['mono']};--body:{fonts['body']};"
    return f":root{{{rows}}}"


# ---------------------------------------------------------------------------
# Global stylesheet (ported from the old inline block, now variable-driven
# with case-file flourishes layered on top)
# ---------------------------------------------------------------------------

def css(theme: str | None = None) -> str:
    theme = theme or active()
    docket = theme == "docket"
    fonts = _FONTS[theme]
    # Background wash: brass + precinct-blue glows over near-black for the
    # case-file; the original keeps its green/cyan radial gradients.
    return f"""<style>
  {fonts['import']}
  {_vars_css(theme)}
  html, body, [class*="css"] {{ font-family: var(--body); }}
  .stApp {{ background:
    radial-gradient(1100px 520px at 8% -10%, rgba(var(--acc-rgb),0.10), transparent 55%),
    radial-gradient(900px 480px at 100% -6%, rgba(var(--acc2-rgb),0.09), transparent 50%),
    var(--bg); color: var(--text); }}
  .block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1340px; }}
  h1,h2,h3,h4, .osp-brand, .osp-title {{ font-family: var(--disp);
    letter-spacing: {'0.4px' if docket else '-0.5px'}; color: var(--text); }}
  section[data-testid="stSidebar"] {{
    background: linear-gradient(180deg,var(--bg2) 0%,var(--bg3) 100%);
    border-right: 1px solid var(--line); }}
  section[data-testid="stSidebar"] .stRadio [role="radiogroup"] > label {{
    border-radius: 8px; padding: 5px 10px; font-weight: 600;
    font-family: var(--disp); font-size: {'0.92rem' if docket else '1rem'};
    letter-spacing: {'0.3px' if docket else '0'}; transition: background .15s ease; }}
  section[data-testid="stSidebar"] .stRadio [role="radiogroup"] > label:hover {{
    background: rgba(var(--acc-rgb),0.12); }}
  [data-testid="stMetric"] {{ position: relative; overflow: hidden;
    background: linear-gradient(160deg, rgba(var(--acc-rgb),0.07), rgba(var(--acc2-rgb),0.04)),
      var(--card);
    border: 1px solid var(--line); border-radius: {'8px' if docket else '14px'};
    padding: 14px 16px 12px 18px; box-shadow: 0 6px 18px rgba(0,0,0,0.40); }}
  [data-testid="stMetric"]::before {{ content:""; position:absolute; left:0; top:0;
    bottom:0; width:3px; background: linear-gradient(180deg,var(--acc),var(--acc2)); }}
  [data-testid="stMetricLabel"] {{ opacity: 0.75; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.7px; font-weight: 600;
    font-family: var(--mono); }}
  [data-testid="stMetricValue"] {{
    font-family: {'var(--mono)' if docket else 'var(--disp)'}; font-weight: 700;
    font-size: {'1.55rem' if docket else '1.8rem'};
    letter-spacing: {'0' if docket else '-1px'}; color: var(--text);
    font-variant-numeric: tabular-nums; }}
  .osp-brand {{ font-size: 1.5rem; font-weight: 700; margin: 0 0 0.1rem 0;
    text-transform: uppercase; letter-spacing: {'1.5px' if docket else '0.6px'};
    font-family: var(--disp);
    background: linear-gradient(90deg,var(--acc),var(--acc2)); -webkit-background-clip: text;
    background-clip: text; -webkit-text-fill-color: transparent; }}
  .osp-title {{ font-size: 1.75rem; font-weight: 700; margin: 0; font-family: var(--disp);
    text-transform: {'uppercase' if docket else 'none'};
    letter-spacing: {'0.6px' if docket else '0'}; }}
  div[data-testid="stCaptionContainer"] {{ opacity: 0.66; font-family: var(--mono);
    font-size: 0.82rem; }}
  .stTabs [data-baseweb="tab"] {{ font-family: var(--disp); font-weight: 600;
    font-size: 0.95rem; letter-spacing: {'0.4px' if docket else '0'}; }}
  .stTabs [aria-selected="true"] {{ color: var(--acc) !important; }}
  .stTabs [data-baseweb="tab-highlight"] {{ background: var(--acc) !important; height: 3px; }}
  .stButton > button {{ border-radius: {'6px' if docket else '10px'};
    border: 1px solid var(--line); font-weight: 700; font-family: var(--disp);
    letter-spacing: {'0.5px' if docket else '0'}; transition: all .15s ease; }}
  .stButton > button:hover {{ border-color: var(--acc); color: var(--text);
    box-shadow: 0 0 0 1px var(--acc), 0 6px 18px rgba(var(--acc-rgb),0.20);
    transform: translateY(-1px); }}
  .stButton > button:active {{ transform: translateY(0); }}
  .osp-hero {{ background: linear-gradient(135deg, rgba(var(--acc-rgb),0.12),
      rgba(var(--acc2-rgb),0.06));
    border: 1px solid rgba(var(--acc-rgb),0.25); border-radius: {'10px' if docket else '18px'};
    padding: 18px 22px; margin-bottom: 14px; box-shadow: 0 8px 26px rgba(0,0,0,0.35); }}
  .osp-pill {{ display:inline-block; font-size:0.72rem; font-weight:700; padding:3px 10px;
    border-radius:999px; margin-right:6px; font-family: var(--mono); }}
  .osp-pill.live {{ animation: osppulse 1.8s ease-in-out infinite; }}
  @keyframes osppulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.5; }} }}
  a.osp-plink {{ color:var(--text2) !important; text-decoration:none;
    border-bottom:1px dashed rgba(var(--acc-rgb),0.0); transition: color .12s ease,
    border-color .12s ease; }}
  a.osp-plink:hover {{ color:var(--acc) !important;
    border-bottom-color:rgba(var(--acc-rgb),0.7); }}

  /* ---- Case-file flourishes ---- */
  /* Title card: the white-on-black location/date intertitle. */
  .osp-tcard {{ background:#000; border:1px solid var(--line); border-left:3px solid var(--acc);
    padding:12px 18px; margin: 2px 0 10px; border-radius:4px;
    animation: ospdun 0.7s ease-out; }}
  .osp-tcard .eb {{ font-family:var(--mono); font-size:0.66rem; letter-spacing:3px;
    text-transform:uppercase; color:var(--acc); }}
  .osp-tcard .ti {{ font-family:var(--disp); font-weight:700; font-size:1.5rem;
    letter-spacing:1px; text-transform:uppercase; color:#fff; line-height:1.15; }}
  .osp-tcard .sub {{ font-family:var(--mono); font-size:0.82rem; color:#cfc6b4;
    letter-spacing:1px; margin-top:2px; }}
  /* the silent "dun-dun": two scale pulses on appearance */
  @keyframes ospdun {{ 0%{{transform:scale(0.985);opacity:0;}}
    25%{{transform:scale(1.004);opacity:1;}} 45%{{transform:scale(0.997);}}
    70%{{transform:scale(1.002);}} 100%{{transform:scale(1);opacity:1;}} }}
  /* Rubber evidence stamp — slams in once, overshoots, settles at -4deg. */
  .osp-stamp {{ display:inline-block; font-family:var(--disp); font-weight:700;
    text-transform:uppercase; letter-spacing:2px; padding:4px 12px; border-radius:3px;
    border:2.5px solid currentColor; transform:rotate(-4deg); font-size:0.86rem;
    opacity:0.92; animation: tbslam 0.30s ease-out; }}
  .osp-stamp.indicted {{ color:var(--elec); }}
  .osp-stamp.true-bill {{ color:var(--acc); }}
  .osp-stamp.dismissed {{ color:var(--vivid); border-style:double; }}
  .osp-stamp.exhibit {{ color:var(--info-text); }}
  .osp-stamp.sealed {{ color:var(--vivid); }}
  @keyframes tbslam {{ 0%{{transform:scale(1.6) rotate(-14deg);opacity:0;}}
    55%{{transform:scale(.94) rotate(-2deg);opacity:1;}}
    72%{{transform:scale(1.02) rotate(-5deg);}}
    100%{{transform:scale(1) rotate(-4deg);opacity:.92;}} }}
  /* The theme serves the numbers: honor reduced-motion. */
  @media (prefers-reduced-motion: reduce) {{
    *, .osp-tcard, .osp-stamp {{ animation: none !important; }} }}
</style>"""


# ---------------------------------------------------------------------------
# Copy maps — courtroom rebrand of the navigation & section titles. Group
# labels double as NAV_GROUPS keys, so the app reads them from here directly.
# ---------------------------------------------------------------------------

# Top-level sidebar areas. Order preserved from the original grouping.
NAV_GROUP_LABELS = {
    "HOME": "⚖️ The Bench",
    "RESEARCH": "🗂️ Case Files",
    "BETS": "📋 The Docket",
    "LIVE": "🚨 The Blotter",
    "TOOLS": "🔬 The Crime Lab",
    "PERF": "🏛️ The Record",
}

# Sub-page display names (format_func only — page *keys* are unchanged).
PAGE_LABELS = {
    "PLAYS": "The Daily Docket",
    "EDGES": "The Wiretap",
    "EXPERTS": "The Jury",
    "DFS": "The Conspiracy",
    "PERFORMANCE": "Conviction Record",
    "BACKTEST": "Cold Cases",
}

# Section titles passed to topbar()/headers.
SECTION_TITLES = {
    "HOME": "The Bench",
    "PLAYS": "The Daily Docket",
    "PERFORMANCE": "Conviction Record",
    "DFS": "The Conspiracy",
    "SCORES": "The Blotter",
    "TOOLS": "The Crime Lab",
    "EDGES": "The Wiretap",
    "EXPERTS": "The Jury",
    "BACKTEST": "Cold Cases",
}

# Verdict display (the decision *values* PLAY/PASS/NOTE stay as logic keys).
VERDICT_LABELS = {"PLAY": "INDICTED", "PASS": "DISMISSED", "NOTE": "EXHIBIT"}
VERDICT_PREFIX = "VERDICT"          # was "DECISION"
CONFIDENCE_LABEL = "STRENGTH OF CASE"  # was "CONFIDENCE"

# Brand canon (from the naming research). Used in copy and exports.
BRAND = "True Bill"
FIRM = "Office of Special Prosecutions"
UNIT = "Special Wagers Unit"
MOTTO = "Res ipsa loquitur — the evidence speaks for itself."
SIGNOFF = "The evidence is in. Return your verdict."

# Themed copy for the many neutral empty-states / labels, keyed by a short id.
# Off-theme, callers pass the original string as the default, so the original
# skin is untouched. Keep meaning; change only the framing.
_COPY = {
    "no_data": "The docket is sealed — no evidence on file yet. The hourly "
               "clerk publishes the record, or hit **↻ Recall the witness**.",
    "no_games": "No cases on the {sport} docket for {date}.",
    "no_match": "No cases match the search.",
    "no_plays": "No charges clear the {edge} bar on {date} yet. A quiet docket "
                "is a disciplined docket.",
    "no_props": "No props filed yet — batter props post once the witness list "
                "(lineup) is confirmed, ~2–4h before first pitch.",
    "select_prop": "Select a charge above to open the case file: recent-game "
                   "exhibit, hit-rate splits, and the People-vs-market read.",
    "no_perf": "No verdicts on the record yet — the Conviction Record accrues "
               "as filed cases finish and the clerk grades them.",
    "no_edges": "No charges over the current bar yet — lower **Min edge** in the "
                "sidebar, or check back as the witness list firms up.",
    "smoking_gun": "🔫 The Smoking Gun — today's loudest evidence",
    "calendar": "🗓️ The calendar — slate at a glance",
}


def say(key: str, default: str, **fmt) -> str:
    """Themed copy lookup. Off-theme returns the original `default` so the
    plain skin is unchanged. `fmt` fills {placeholders} in the themed string."""
    if not is_docket():
        return default
    s = _COPY.get(key, default)
    return s.format(**fmt) if fmt else s


def verdict(decision: str) -> str:
    """Display label for a PLAY/PASS/NOTE decision under the active theme."""
    if not is_docket():
        return decision
    return VERDICT_LABELS.get(decision, decision)


def t(key: str, default: str) -> str:
    """Section-title lookup; falls back to the plain label off-theme."""
    return SECTION_TITLES.get(key, default) if is_docket() else default


# ---------------------------------------------------------------------------
# Themed HTML helpers
# ---------------------------------------------------------------------------

def title_card(eyebrow: str, title: str, sub: str | None = None) -> str:
    """A Law & Order intertitle: black card, carved title, monospace sub —
    the location/date stamp that fires with the dun-dun."""
    sub_html = f"<div class='sub'>{sub}</div>" if sub else ""
    return (f"<div class='osp-tcard'><div class='eb'>{eyebrow}</div>"
            f"<div class='ti'>{title}</div>{sub_html}</div>")


def stamp(text: str, kind: str = "exhibit") -> str:
    return f"<span class='osp-stamp {kind}'>{text}</span>"


def dun_dun_component(height: int = 64):
    """A real, mutable 'DUN-DUN' button (Web Audio synth of The Clang: a
    metallic clang + a low thud). Rendered as a components.html island so the
    JS actually runs; nothing autoplays. Pushing Streamlit a little, on
    purpose."""
    html = """
<div style="display:flex;justify-content:center;font-family:'Courier Prime',monospace;">
  <button id="dd" style="cursor:pointer;background:#000;color:#c69a3f;
    border:2px solid #2b281f;border-radius:6px;padding:9px 20px;font-weight:700;
    letter-spacing:3px;font-family:inherit;font-size:0.9rem;">▶ DUN-DUN</button>
</div>
<script>
const b = document.getElementById('dd');
b.onclick = () => {
  const C = new (window.AudioContext||window.webkitAudioContext)();
  const hit = (t, f, dur, type, gain) => {
    const o = C.createOscillator(), g = C.createGain();
    o.type = type; o.frequency.setValueAtTime(f, t);
    o.frequency.exponentialRampToValueAtTime(f*0.5, t+dur);
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.001, t+dur);
    o.connect(g); g.connect(C.destination); o.start(t); o.stop(t+dur);
  };
  // metallic noise burst = the "clang"
  const noise = (t, dur, gain) => {
    const n = C.createBufferSource();
    const buf = C.createBuffer(1, C.sampleRate*dur, C.sampleRate);
    const d = buf.getChannelData(0);
    for (let i=0;i<d.length;i++) d[i] = (Math.random()*2-1)*Math.pow(1-i/d.length,2);
    n.buffer = buf;
    const f = C.createBiquadFilter(); f.type='bandpass'; f.frequency.value=1800; f.Q.value=0.8;
    const g = C.createGain(); g.gain.setValueAtTime(gain,t);
    g.gain.exponentialRampToValueAtTime(0.001,t+dur);
    n.connect(f); f.connect(g); g.connect(C.destination); n.start(t); n.stop(t+dur);
  };
  const now = C.currentTime;
  hit(now, 320, 0.18, 'square', 0.35); noise(now, 0.16, 0.5);          // DUN
  hit(now+0.22, 150, 0.45, 'sawtooth', 0.4); noise(now+0.22, 0.30, 0.35); // DUNNN
  b.animate([{transform:'scale(1)'},{transform:'scale(0.94)'},{transform:'scale(1)'}],
            {duration:300});
};
</script>
"""
    # An interactive island (Web Audio needs real JS, which st.markdown strips).
    # Degrade quietly if the components API is ever unavailable — the sound is
    # a flourish, never load-bearing.
    try:
        from streamlit.components.v1 import html as _html
        _html(html, height=height)
    except Exception:
        st.caption("🔊 DUN-DUN")
