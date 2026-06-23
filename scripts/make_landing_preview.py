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

from app import landing, theme  # noqa: E402
from onesource import config  # noqa: E402


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
