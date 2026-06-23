"""Render a self-contained HTML preview of the public landing page using the
real renderers (``app/landing.py``) and the live ``latest.json`` data — the
storefront a visitor sees before the password gate. Open the output in a
browser to design the public face without deploying.

    python scripts/make_landing_preview.py [-o landing.html]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import landing  # noqa: E402
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
    updated = f" · updated {gen} ET" if gen else ""

    body = (
        "<div class='osp-land'>"
        "<div class='osp-hero-wrap'>"
        "<div class='osp-logo'>🎯 OneSource</div>"
        "<div class='osp-tag'>The multi-sport model that beats the close.</div>"
        "<div class='osp-sub'>Game and player-prop projections across six "
        "sports, priced against the market and forward-tested every hour. "
        "Edges, not vibes.</div>"
        f"</div><div class='osp-chips'>{chips}</div>"
        + landing._stat_tiles_html(stats)
        + landing._board_html(counts, rows)
        + landing._how_html()
        + "<div style='text-align:center;margin:8px 0 4px;'>"
        "<span style='display:inline-block;background:linear-gradient(90deg,#00e676,#22d3ee);"
        "color:#06210f;font-weight:700;font-family:var(--disp);padding:11px 26px;"
        "border-radius:10px;'>Enter the model  →</span></div>"
        "<div class='osp-foot'>For research and entertainment only — "
        "<b>not financial advice</b>. Model estimates carry no guarantee. "
        f"21+. If gambling stops being fun, call 1-800-GAMBLER.{updated}</div>"
        "</div>")

    # The landing CSS leans on a couple of root vars set by the app shell;
    # supply them here so the standalone file matches the deployed look.
    root = ("<style>@import url('https://fonts.googleapis.com/css2?"
            "family=Space+Grotesk:wght@500;600;700&display=swap');"
            ":root{--disp:'Space Grotesk',system-ui,sans-serif;}"
            "body{margin:0;background:"
            "radial-gradient(1100px 520px at 8% -10%,rgba(0,230,118,0.10),transparent 55%),"
            "radial-gradient(900px 480px at 100% -6%,rgba(34,211,238,0.08),transparent 50%),"
            "#080b12;color:#e6edf3;font-family:system-ui,-apple-system,sans-serif;"
            "padding:24px 18px;}</style>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>OneSource — public landing preview</title>"
            f"{root}{landing._LANDING_CSS}</head><body>{body}</body></html>")


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
