"""Generate the 360Five home-screen app icons (PWA / iOS).

The mark (docs/BRAND.md): a 360° ring around a 5. Dark navy field, gold ring,
cream 5. Writes PNGs into app/pwa/ which build_static.py copies into the site.
Run once (or whenever the mark changes); the PNGs are committed.

    python scripts/make_app_icons.py
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "pwa"

NAVY = (26, 34, 38)        # #1a2226 field
GOLD = (168, 125, 34)      # #a87d22 ring
GOLD2 = (143, 106, 26)     # #8f6a1a inner shade
CREAM = (250, 246, 236)    # #faf6ec 5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

M = 1024                   # master size


def _master(maskable: bool = False) -> Image.Image:
    """Render the mark at the master size. ``maskable`` adds safe-area padding
    (Android adaptive icons crop to a circle) and fills the whole field."""
    img = Image.new("RGB", (M, M), NAVY)
    d = ImageDraw.Draw(img)
    pad = int(M * (0.16 if maskable else 0.085))   # extra breathing room when masked
    # rounded field for non-maskable (iOS applies its own squircle mask anyway)
    box = [pad, pad, M - pad, M - pad]
    ring_w = int(M * 0.055)
    # 360° ring — two concentric strokes for a little depth
    d.ellipse(box, outline=GOLD, width=ring_w)
    inset = ring_w + int(M * 0.006)
    d.ellipse([box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset],
              outline=GOLD2, width=max(2, int(M * 0.006)))
    # the 5, centred
    size = int(M * (0.46 if maskable else 0.52))
    try:
        font = ImageFont.truetype(FONT, size)
    except OSError:
        font = ImageFont.load_default()
    l, t, r, b = d.textbbox((0, 0), "5", font=font)
    d.text(((M - (r - l)) / 2 - l, (M - (b - t)) / 2 - t), "5",
           font=font, fill=CREAM)
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    std = _master(maskable=False)
    mask = _master(maskable=True)
    targets = [
        (std, "icon-512.png", 512),
        (std, "icon-192.png", 192),
        (std, "apple-touch-icon.png", 180),
        (mask, "icon-512-maskable.png", 512),
    ]
    for src, name, size in targets:
        src.resize((size, size), Image.LANCZOS).save(OUT / name)
        print(f"  wrote {name} ({size}x{size})")
    print(f"done -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
