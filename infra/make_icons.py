#!/usr/bin/env python3
"""Generate the non-AWS node icons the architecture diagram needs.

    python3 make_icons.py

`diagrams` ships official icons for every AWS service, and for nothing else. The three
things this architecture is actually *about* — Backblaze B2, Genblaze, and the AI
providers Genblaze fans out to — therefore have to be drawn. They are generated rather
than downloaded so the diagram builds from a clean checkout with no vendored binaries
and no logo files of uncertain licence.

These are wordmark tiles, not trademarks: flat colour, the shortest legible label, no
attempt to reproduce anyone's actual logo.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "assets"
SIZE = 320
RADIUS = 58

# Brand-adjacent, not brand-exact. Backblaze red is the one people recognise.
TILES = [
    ("backblaze-b2", "B2", "#D32127", "#FFFFFF"),
    ("genblaze", "GEN\nBLAZE", "#1B1D24", "#FF6B35"),
    ("gmicloud", "GMI", "#2D6CDF", "#FFFFFF"),
    ("elevenlabs", "11", "#111111", "#FFFFFF"),
    ("browser", "WWW", "#4A5568", "#FFFFFF"),
]

FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONTS:
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
    return ImageFont.load_default()


def tile(name: str, label: str, bg: str, fg: str) -> Path:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=bg)

    lines = label.split("\n")
    # Shrink until the widest line fits with a comfortable margin.
    size = int(SIZE * (0.42 if len(lines) == 1 else 0.26))
    while size > 8:
        f = font(size)
        widest = max(d.textbbox((0, 0), ln, font=f)[2] for ln in lines)
        if widest <= SIZE * 0.78:
            break
        size -= 4
    f = font(size)

    line_h = size * 1.15
    total_h = line_h * len(lines)
    y = (SIZE - total_h) / 2
    for ln in lines:
        x0, y0, x1, y1 = d.textbbox((0, 0), ln, font=f)
        d.text(((SIZE - (x1 - x0)) / 2 - x0, y - y0 + (line_h - size) / 2), ln,
               font=f, fill=fg)
        y += line_h

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.png"
    img.save(p)
    return p


def main() -> None:
    for args in TILES:
        print(f"  {tile(*args)}")


if __name__ == "__main__":
    main()
