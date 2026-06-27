"""Render an HTML research card to a PNG, for embedding in the workbook.

The dashboard cards (``app.ui.matchup_card_html``) are themed with CSS
variables, so here we wrap the markup in a self-contained document that
defines those variables with the brand's concrete cream/graphite palette,
screenshot it with the **pre-installed Chromium** binary (no Playwright
needed), and auto-crop to the card. Pure best-effort: every entry point
returns ``None`` rather than raising if a browser isn't available, so the
workbook build never depends on it.
"""

from __future__ import annotations

import glob
import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Brand cream/graphite palette (docs/BRAND.md) as concrete values, so the
# var(--…)-themed card renders identically to the dashboard's light theme.
THEME_CSS = """
:root{
  --text:#1f2328; --muted:#5a6066; --faint:#9b937f;
  --good:#2f7a4a; --neg:#b03636; --mid:#c8941a;
  --bg:#ece2c8; --card:#fbf5e6; --card2:#efe2c2; --line:#d8ccab;
  --disp:'Oswald',system-ui,sans-serif;
  --font:'DM Sans',system-ui,sans-serif;
}
"""
_FONTS = ("@import url('https://fonts.googleapis.com/css2?family=Oswald:"
          "wght@400;500;600;700&family=DM+Sans:wght@400;500;600;700&display=swap');")


def chrome_path() -> str | None:
    """Locate the Chromium/Chrome binary (Playwright's bundle or a system one)."""
    env = os.environ.get("CHROME_BIN")
    if env and Path(env).exists():
        return env
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")]
    pats = ["chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux/headless_shell",
            "chromium_headless_shell-*/chrome-linux/headless_shell"]
    for root in roots:
        for pat in pats:
            hits = sorted(glob.glob(str(Path(root) / pat)))
            if hits:
                return hits[-1]
    for cand in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                 "/usr/bin/google-chrome", "/usr/bin/chrome"):
        if Path(cand).exists():
            return cand
    return None


def available() -> bool:
    return chrome_path() is not None


def wrap_html(card_html: str, width: int = 1140) -> str:
    """Self-contained document: brand theme vars + the card markup."""
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{_FONTS}{THEME_CSS}"
            f"body{{margin:0;background:var(--bg);padding:20px;"
            f"width:{width}px;}}</style></head>"
            f"<body>{card_html}</body></html>")


def _autocrop(png: bytes) -> bytes:
    """Trim the uniform background border so the card fills the image."""
    try:
        import io

        from PIL import Image, ImageChops
        im = Image.open(io.BytesIO(png)).convert("RGB")
        bg = Image.new("RGB", im.size, im.getpixel((0, 0)))
        diff = ImageChops.difference(im, bg)
        bbox = diff.getbbox()
        if bbox:
            pad = 16
            l, t, r, b = bbox
            bbox = (max(0, l - pad), max(0, t - pad),
                    min(im.width, r + pad), min(im.height, b + pad))
            im = im.crop(bbox)
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except Exception as e:  # noqa: BLE001 — cropping is a nicety, never fatal
        log.debug("autocrop skipped: %s", e)
        return png


def render_png(card_html: str, width: int = 1140, scale: int = 2,
               max_height: int = 4000, timeout: float = 45.0) -> bytes | None:
    """Screenshot the card to PNG bytes, or None if no browser / on error."""
    chrome = chrome_path()
    if not chrome:
        log.info("no Chromium found — skipping card image")
        return None
    html = wrap_html(card_html, width=width)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "card.html"
        out = Path(td) / "card.png"
        src.write_text(html, encoding="utf-8")
        cmd = [chrome, "--headless", "--no-sandbox", "--disable-gpu",
               "--hide-scrollbars", "--disable-dev-shm-usage",
               f"--force-device-scale-factor={scale}",
               f"--window-size={width + 40},{max_height}",
               f"--screenshot={out}", f"file://{src}"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
            if not out.exists() or out.stat().st_size == 0:
                log.warning("Chromium produced no screenshot")
                return None
            return _autocrop(out.read_bytes())
        except Exception as e:  # noqa: BLE001
            log.warning("card render failed: %s", e)
            return None


def render_matchup_png(sport: str, g: dict, matchup: dict, window: str = "l5",
                       **kw) -> bytes | None:
    """Convenience: build the matchup card HTML and render it to PNG."""
    try:
        from app import ui
    except Exception as e:  # noqa: BLE001 — app import is optional at build time
        log.info("ui import unavailable for card image: %s", e)
        return None
    html = ui.matchup_card_html(sport, g, matchup, window=window)
    return render_png(html, **kw)
