"""Render a self-contained HTML preview of the public landing page (the Law &
Order "cold open") using the real renderers (``app/landing.py`` + the themed
stylesheet from ``app/theme.py``) and the live ``latest.json`` data. Open the
output in a browser to design the public face without deploying.

    python scripts/make_landing_preview.py [-o landing.html]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import conviction, landing, theme  # noqa: E402
from onesource import config  # noqa: E402


def _trial_section(day: dict) -> str:
    """A static stand-in for the interactive 'Put it on trial' panel: the
    dropdown row plus one real example verdict computed off the slate."""
    def sel(label, value):
        return (f"<div style='flex:1;min-width:130px;'><div style='font-family:var(--mono);"
                f"font-size:0.66rem;letter-spacing:1px;color:var(--muted);"
                f"margin-bottom:3px;'>{label}</div><div style='border:1px solid var(--line);"
                f"border-radius:6px;background:var(--card);padding:8px 12px;color:var(--text);"
                f"font-size:0.88rem;'>{value} &nbsp;▾</div></div>")

    sports = conviction.sports(day)
    res, picks = None, ("MLB", "—", "Moneyline", "—")
    for sp in sports:
        gms = conviction.games(day, sp)
        if gms:
            g = gms[0]
            ch = (conviction.charges(g) or ["Moneyline"])[0]
            sd = conviction.sides(g, ch)
            # favor the higher-conviction side for the showcase
            side_id = max(sd, key=lambda s: conviction.game_verdict(g, ch, s[0])["rate"] or 0)[0]
            res = conviction.game_verdict(g, ch, side_id)
            picks = (sp, conviction.game_label(g), ch, dict(sd)[side_id])
            break
    row = ("<div style='display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 4px;'>"
           + sel("Jurisdiction", picks[0]) + sel("The Case", picks[1])
           + sel("The Charge", picks[2]) + sel("The Side", picks[3]) + "</div>"
           "<div style='text-align:center;margin:10px 0;'><span style='font-family:var(--disp);"
           "font-weight:700;letter-spacing:1px;background:linear-gradient(90deg,var(--acc),"
           "var(--acc2));color:#000;padding:9px 22px;border-radius:6px;'>"
           "⚖  RETURN THE VERDICT</span></div>")
    out = landing._verdict_card_html(res) if res else ""
    return ("<div class='osp-sec'>— Put Your Play On Trial —</div>"
            "<div class='osp-trial-lede'>Assemble a play. The People return a "
            "<b>Conviction Rate</b> — the model's read on how often it cashes — "
            "with the evidence.</div>" + row + out)


def build(data: dict | None) -> str:
    slates = {}
    if data:
        slates = data.get("slates") or (
            {data.get("date", "latest"): data["sports"]} if "sports" in data else {})
    primary = (data or {}).get("primary_date")
    if primary not in slates:
        primary = next(iter(slates), None)
    day = slates.get(primary, {}) if primary else {}

    stats = landing.headline_stats((data or {}).get("performance"))
    counts = landing.teaser_counts(day, config.MIN_EDGE)
    rows = landing.redacted_rows(day, config.MIN_EDGE)
    chips = "".join(f"<span class='osp-chip'>{s}</span>" for s in landing._SPORTS)
    gen = str((data or {}).get("generated_at", ""))[:16].replace("T", " ")
    updated = f" · case file updated {gen} ET" if gen else ""

    # static stand-in for the Web-Audio DUN-DUN button (no JS in a flat file)
    dun = ("<div style='text-align:center;margin:14px 0;'>"
           "<span style='font-family:var(--mono);background:#000;color:var(--acc);"
           "border:2px solid var(--line);border-radius:6px;padding:9px 20px;"
           "font-weight:700;letter-spacing:3px;'>▶ DUN-DUN</span></div>")
    enter = ("<div style='text-align:center;margin:10px 0 4px;'>"
             "<span style='display:inline-block;font-family:var(--disp);font-weight:700;"
             "letter-spacing:1px;background:linear-gradient(90deg,var(--acc),var(--acc2));"
             "color:#000;padding:12px 28px;border-radius:6px;'>"
             "⚖️  ENTER THE COURTROOM  →</span></div>")

    body = (
        "<div class='osp-land'>" + landing._cold_open_html() + "</div>"
        + dun
        + "<div class='osp-land'>"
        f"<div class='osp-chips'><span class='lbl'>Jurisdictions</span>{chips}</div>"
        "<div class='osp-sec'>— The Conviction Record —</div>"
        + landing._stat_tiles_html(stats)
        + "<div class='osp-sec'>— In Session —</div>"
        + landing._board_html(counts, rows)
        + _trial_section(day)
        + "<div class='osp-sec'>— Due Process —</div>"
        + landing._how_html()
        + "<div class='osp-sec'>— The Firm —</div>"
        + landing._firm_html()
        + f"<div class='osp-signoff'>{theme.SIGNOFF}</div>"
        + f"<div class='osp-motto'>{theme.MOTTO}</div>"
        + enter
        + "<div class='osp-foot'>For research and entertainment only — "
        "<b>not financial advice</b>. The People's estimates carry no guarantee; "
        "even a strong case loses. 21+. If gambling stops being fun, call "
        f"1-800-GAMBLER.{updated}</div>"
        "</div>")

    reset = ("<style>body{margin:0;background:var(--bg);padding:26px 18px;}"
             "section[data-testid='stSidebar']{display:none;}</style>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>True Bill — the cold open</title>"
            # the real themed stylesheet (vars + fonts + case-file flourishes)
            f"{theme.css('docket')}{landing._LANDING_CSS}{reset}</head>"
            f"<body>{body}</body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="landing.html")
    args = ap.parse_args()
    path = config.OUTPUT_DIR / "latest.json"
    data = json.loads(path.read_text()) if path.exists() else None
    Path(args.out).write_text(build(data))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
