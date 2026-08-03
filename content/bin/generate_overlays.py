#!/usr/bin/env python3
"""Render transparent-background overlay cards from an rtf.overlay/v1 spec.

    python3 generate_overlays.py ../lib/overlays/demo-founder.overlays.yaml
    python3 generate_overlays.py <spec> --check
    python3 generate_overlays.py <spec> --only 04-fifty-to-one --formats png
    python3 generate_overlays.py <spec> --aspect 9:16 --out ../videos/demo-overlays-9x16

These are the graphics that sit *on top of a person talking*, so two things are true of
every card here and neither is decoration:

**The background is transparent, the card is not.** White type composited straight onto
video disappears the moment the speaker moves in front of something bright. So each card
carries its own scrim — a panel or a gradient — and the transparency is what surrounds it.
An overlay with a genuinely empty background is only legible against footage you control,
and nobody controls a founder's shirt.

**Nothing is drawn in the middle of the frame.** `placement` keeps every card in a lower
third or a side panel, out of the region a centred talking head occupies.

The type and colour come from the deck (`docs/deck/remixkit-deck.html`) — Futura for
display, Avenir Next for labels, the same validated categorical palette — so the demo
video and the deck read as one object rather than two designs of the same company.

Requires: ffmpeg on PATH (for the video formats), pillow, pyyaml.

---

## Schema — `rtf.overlay/v1`

```yaml
schema: rtf.overlay/v1
id: <slug>                      # the set's id; names the output directory
title / subtitle: <str>
target: <path>                  # the script these cards are cut against

canvas: { width, height, fps }  # overridden by --aspect
defaults:                       # any per-overlay key may be defaulted here
  duration_ms / in_ms / out_ms: <int>
  placement / scrim / accent: <str>

overlays:
  - id: <NN-slug>               # ordinal prefix = running order
    vo_line: <int>              # line in `target` this card sits under
    cue: <str>                  # the spoken words it lands on — place it by ear
    kind: stat | ratio | bar | steps | chips | kicker | lower_third | title
    placement: lower_left | lower_right | left_panel | right_panel
             | lower_band | top_left | center
    scrim: panel | gradient | none
    accent: gold | s1 | s2 | s3 | s4 | s5
    ...                         # per-kind fields, see LAYOUTS below
```

Per-kind fields:

| kind | fields |
|---|---|
| `stat` | `value`, `unit?`, `label`, `note?`, `source?` |
| `ratio` | `left`, `sep`, `right`, `label`, `note?`, `source?` |
| `bar` | `pct`, `value`, `label`, `note?`, `source?` |
| `steps` | `label`, `items[]`, `active`, `note?` |
| `chips` | `label`, `items[]` (`{text, note?}`), `note?` |
| `kicker` | `text`, `note?` |
| `lower_third` | `name`, `role?` |
| `title` | `word`, `tag?` |

## Outputs

Per overlay, into `<out>/`:

- `<id>.png` — RGBA still, the card fully built. Drop it on a track and animate it
  yourself, or use it as a thumbnail of what the animated version does.
- `<id>.webm` — VP9 with alpha (`yuva420p`). Animated. Plays in a browser and in OBS.
- `<id>.mov` — ProRes 4444 with alpha, only with `--formats mov`. Large, and the one
  Premiere / Resolve / Final Cut actually want.

Plus `manifest.yaml`: every card's id, cue, vo_line, timing, files and sha256. The
running order and the script line are recorded so an editor never has to guess which
graphic belongs to which sentence.
"""

import argparse
import hashlib
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------- design tokens

# Lifted verbatim from docs/deck/remixkit-deck.html :root. If the deck's palette moves,
# move it here too — two copies of a brand is how a deck and a video stop matching.
PALETTE = {
    "bg":      (0x14, 0x12, 0x1a),
    "bg_2":    (0x0e, 0x0c, 0x14),
    "panel":   (0x1c, 0x19, 0x26),
    "panel_2": (0x23, 0x20, 0x30),
    "ink":     (0xff, 0xff, 0xff),
    "ink_2":   (0xc8, 0xc4, 0xd4),
    "ink_3":   (0x8b, 0x86, 0x98),
    "gold":    (0xf5, 0xb9, 0x40),
    "s1":      (0x39, 0x87, 0xe5),
    "s2":      (0xd9, 0x59, 0x26),
    "s3":      (0x19, 0x9e, 0x70),
    "s4":      (0xc9, 0x85, 0x00),
    "s5":      (0xd5, 0x51, 0x81),
    "muted":   (0x3a, 0x36, 0x48),
}

FUTURA = "/System/Library/Fonts/Supplemental/Futura.ttc"
AVENIR = "/System/Library/Fonts/Avenir Next.ttc"

# face name -> (file, ttc index). Deck rule: Futura displays, Avenir Next says things.
FACES = {
    "display":     (FUTURA, 2),  # Futura Bold
    "display_med": (FUTURA, 0),  # Futura Medium
    "label":       (AVENIR, 2),  # Avenir Next Demi Bold
    "strong":      (AVENIR, 0),  # Avenir Next Bold
    "body":        (AVENIR, 5),  # Avenir Next Medium
    "text":        (AVENIR, 7),  # Avenir Next Regular
}

ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}

KINDS = {"stat", "ratio", "bar", "steps", "chips", "kicker", "lower_third", "title"}
PLACEMENTS = {"lower_left", "lower_right", "left_panel", "right_panel", "lower_band", "top_left", "center"}
SCRIMS = {"panel", "gradient", "none"}

RISE_PX = 26        # how far a content element travels on the way in, at S=1
STAGGER = 0.075     # delay between successive elements, as a fraction of in_ms
STAGGER_MAX = 0.5


@lru_cache(maxsize=256)
def font(face: str, size: int):
    from PIL import ImageFont

    path, index = FACES[face]
    if not Path(path).exists():
        sys.exit(f"missing font: {path}\nThese cards are drawn with the deck's typefaces; "
                 f"install Futura and Avenir Next, or edit FACES to point somewhere real.")
    return ImageFont.truetype(path, max(1, int(size)), index=index)


def fit_font(face: str, text: str, size: int, max_w: float, floor: float = 0.55):
    """Largest size at or below `size` whose `text` fits `max_w`, never below floor*size."""
    lo = max(8, int(size * floor))
    while size > lo and font(face, size).getlength(text) > max_w:
        size -= 2
    return font(face, size)


# ---------------------------------------------------------------- drawing helpers


@dataclass
class El:
    """One drawn thing: a tile, where it goes, and how it arrives."""
    img: Image.Image
    x: int
    y: int
    anim: str = "rise"          # rise | fade | wipe_x
    delay: float = 0.0          # fraction of in_ms
    layer: str = "content"      # content | scrim (scrim never staggers)


def text_tile(s: str, f, color, tracking: float = 0.0, opacity: int = 255) -> Image.Image:
    """Render one line to a tight RGBA tile whose top edge is the font's ascender.

    Positioning off the ascender rather than the ink box is what keeps a column of
    mixed-size text on a rhythm — otherwise a line with no descender sits high.
    """
    asc, desc = f.getmetrics()
    if tracking:
        width = sum(f.getlength(c) for c in s) + tracking * max(0, len(s) - 1)
    else:
        width = f.getlength(s)
    img = Image.new("RGBA", (max(1, math.ceil(width) + 6), asc + desc + 6), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = tuple(color) + (opacity,)
    if tracking:
        x = 0.0
        for c in s:
            d.text((x, 0), c, font=f, fill=fill, anchor="la")
            x += f.getlength(c) + tracking
    else:
        d.text((0, 0), s, font=f, fill=fill, anchor="la")
    return img


def line_h(f) -> int:
    asc, desc = f.getmetrics()
    return asc + desc


def wrap(s: str, f, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if not cur or f.getlength(trial) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def rule_tile(w: int, h: int, color) -> Image.Image:
    img = Image.new("RGBA", (max(1, w), max(1, h)), tuple(color) + (255,))
    return img


ARROW_SEPS = {"→", "->", "⇒", "=>", "»"}


def arrow_tile(size: float, color) -> Image.Image:
    """Futura has no U+2192, and a card that renders a tofu box where the arrow goes is
    worse than one with no arrow at all. So draw it, and get to pick its weight."""
    w, h = int(size * 1.4), int(size)
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    sw = max(2, int(round(size * 0.09)))
    cy, tip, head = h / 2, w - sw, size * 0.33
    fill = tuple(color) + (255,)
    d.line([(0, cy), (tip, cy)], fill=fill, width=sw)
    d.line([(tip - head, cy - head), (tip, cy)], fill=fill, width=sw)
    d.line([(tip - head, cy + head), (tip, cy)], fill=fill, width=sw)
    return img


def paste_over(canvas: Image.Image, tile: Image.Image, x: int, y: int) -> None:
    """alpha_composite that tolerates tiles hanging off any edge."""
    cw, ch = canvas.size
    sx = max(0, -x)
    sy = max(0, -y)
    ex = min(tile.width, cw - x)
    ey = min(tile.height, ch - y)
    if ex <= sx or ey <= sy:
        return
    if (sx, sy, ex, ey) != (0, 0, tile.width, tile.height):
        tile = tile.crop((sx, sy, ex, ey))
    canvas.alpha_composite(tile, (x + sx, y + sy))


def with_alpha(img: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
    return out


# ---------------------------------------------------------------- layouts
#
# Every layout draws into a content box whose origin is (0, 0) and returns
# (elements, width, height). The card builder then wraps it in a scrim and puts the
# whole block where `placement` says. No layout knows where on screen it ends up.


class Block:
    """A vertical stack. Tracks the cursor and the widest thing put on it."""

    def __init__(self, S: float, max_w: float):
        self.S = S
        self.max_w = max_w
        self.els: list[El] = []
        self.y = 0.0
        self.w = 0.0

    def put(self, img: Image.Image, x: float = 0, anim: str = "rise", advance: float = None):
        self.els.append(El(img, int(round(x)), int(round(self.y)), anim=anim))
        self.w = max(self.w, x + img.width)
        self.y += img.height if advance is None else advance
        return self.els[-1]

    def at(self, img: Image.Image, x: float, y: float, anim: str = "rise"):
        """Place without advancing the cursor — for things that sit beside something."""
        self.els.append(El(img, int(round(x)), int(round(y)), anim=anim))
        self.w = max(self.w, x + img.width)
        return self.els[-1]

    def gap(self, px: float):
        self.y += px * self.S

    def lines(self, s: str, face: str, size: float, color, lead: float = 1.14, tracking=0.0):
        f = font(face, size * self.S)
        for ln in wrap(s, f, self.max_w):
            self.put(text_tile(ln, f, color, tracking=tracking), advance=line_h(f) * lead)

    def accent_rule(self, color, w: float = 56, h: float = 4):
        self.put(rule_tile(int(w * self.S), int(h * self.S), color), anim="wipe_x", advance=h * self.S)

    def label(self, s: str, color=None):
        f = font("label", 25 * self.S)
        self.put(text_tile(s, f, color or PALETTE["ink_3"], tracking=2.6 * self.S), advance=line_h(f))

    def note(self, s: str, color=None, size: float = 27):
        self.lines(s, "body", size, color or PALETTE["ink_2"])

    def source(self, s: str):
        f = font("text", 21 * self.S)
        self.put(text_tile(f"— {s}", f, PALETTE["ink_3"]), advance=line_h(f))

    def done(self):
        return self.els, self.w, self.y


def _big_value(b: Block, value: str, unit: str, color):
    """The number, with an optional smaller unit sitting on its baseline."""
    fv = fit_font("display", value, 150 * b.S, b.max_w * (0.82 if unit else 1.0))
    tv = text_tile(value, fv, color)
    asc_v, _ = fv.getmetrics()
    b.put(tv, advance=line_h(fv) * 0.92)
    if unit:
        fu = font("display_med", 56 * b.S)
        asc_u, _ = fu.getmetrics()
        # align the unit's baseline to the value's
        b.at(text_tile(unit, fu, PALETTE["ink_3"]), tv.width + 10 * b.S, b.els[-1].y + (asc_v - asc_u))


def layout_stat(o, b: Block, accent):
    b.accent_rule(accent)
    b.gap(24)
    b.label(o["label"])
    b.gap(14)
    _big_value(b, str(o["value"]), o.get("unit"), PALETTE["ink"])
    if o.get("note"):
        b.gap(12)
        b.note(o["note"])
    if o.get("source"):
        b.gap(18)
        b.source(o["source"])
    return b.done()


def layout_ratio(o, b: Block, accent):
    b.accent_rule(accent)
    b.gap(24)
    b.label(o["label"])
    b.gap(14)

    # An arrow means "became", so the right side is the answer and carries the accent.
    # A colon means a ratio, so the left magnitude is the shock and carries it.
    sep = str(o.get("sep", ":"))
    arrow = sep in ARROW_SEPS
    left_c = PALETTE["ink_3"] if arrow else accent
    right_c = accent if arrow else PALETTE["ink"]

    fb = font("display", 150 * b.S)
    asc_b, _ = fb.getmetrics()
    pad = 26 * b.S

    tl = text_tile(str(o["left"]), fb, left_c)
    tr = text_tile(str(o["right"]), fb, right_c)

    # A separator between two big numerals wants to sit on the optical centre of the
    # digits, not on their baseline — a baseline-aligned colon reads as having slipped.
    y = b.y
    d_top, d_bot = fb.getbbox("0")[1], fb.getbbox("0")[3]
    d_mid = y + (d_top + d_bot) / 2

    if arrow:
        ts = arrow_tile(72 * b.S, PALETTE["ink_3"])
        sep_y = d_mid - ts.height / 2
    else:
        fs = font("display_med", 84 * b.S)
        ts = text_tile(sep, fs, PALETTE["ink_3"])
        s_top, s_bot = fs.getbbox(sep)[1], fs.getbbox(sep)[3]
        sep_y = d_mid - (s_top + s_bot) / 2

    b.at(tl, 0, y)
    b.at(ts, tl.width + pad, sep_y)
    b.at(tr, tl.width + pad + ts.width + pad, y)
    b.y = y + line_h(fb) * 0.92

    if o.get("note"):
        b.gap(12)
        b.note(o["note"])
    if o.get("source"):
        b.gap(18)
        b.source(o["source"])
    return b.done()


def layout_bar(o, b: Block, accent):
    b.accent_rule(accent)
    b.gap(24)
    b.label(o["label"])
    b.gap(14)
    _big_value(b, str(o["value"]), o.get("unit"), PALETTE["ink"])
    b.gap(18)

    pct = max(0.0, min(1.0, float(o["pct"])))
    track_w = int(b.max_w)
    track_h = int(16 * b.S)
    r = track_h // 2

    track = Image.new("RGBA", (track_w, track_h), (0, 0, 0, 0))
    ImageDraw.Draw(track).rounded_rectangle([0, 0, track_w - 1, track_h - 1], r,
                                            fill=PALETTE["panel_2"] + (255,))
    fill_w = max(track_h, int(track_w * pct))
    fill = Image.new("RGBA", (fill_w, track_h), (0, 0, 0, 0))
    ImageDraw.Draw(fill).rounded_rectangle([0, 0, fill_w - 1, track_h - 1], r, fill=accent + (255,))

    y = b.y
    b.at(track, 0, y, anim="fade")
    b.at(fill, 0, y, anim="wipe_x")
    b.y = y + track_h

    if o.get("note"):
        b.gap(16)
        b.note(o["note"])
    if o.get("source"):
        b.gap(16)
        b.source(o["source"])
    return b.done()


def layout_steps(o, b: Block, accent):
    b.label(o["label"], accent)
    b.gap(22)

    items = o["items"]
    active = int(o.get("active", -1))
    dot = int(34 * b.S)
    gap_y = 30 * b.S
    text_x = dot + 22 * b.S
    ft = font("label", 33 * b.S)
    fn = font("display_med", 19 * b.S)

    rows = []
    for i, it in enumerate(items):
        txt = it["text"] if isinstance(it, dict) else str(it)
        on = i == active
        chip = Image.new("RGBA", (dot, dot), (0, 0, 0, 0))
        cd = ImageDraw.Draw(chip)
        if on:
            cd.ellipse([0, 0, dot - 1, dot - 1], fill=accent + (255,))
            num_c = PALETTE["bg_2"]
        else:
            cd.ellipse([1, 1, dot - 2, dot - 2], outline=PALETTE["muted"] + (255,), width=max(2, int(2 * b.S)))
            num_c = PALETTE["ink_3"]
        nt = text_tile(str(i + 1), fn, num_c)
        chip.alpha_composite(nt, ((dot - nt.width) // 2, (dot - line_h(fn)) // 2 + int(1 * b.S)))
        rows.append((chip, text_tile(txt, ft, PALETTE["ink"] if on else PALETTE["ink_3"])))

    row_h = max(dot, line_h(ft)) + gap_y
    rail_h = int(row_h * (len(rows) - 1))
    if rail_h > 0:
        rail = rule_tile(max(2, int(2 * b.S)), rail_h, PALETTE["muted"])
        b.at(rail, (dot - max(2, int(2 * b.S))) / 2, b.y + dot, anim="fade")

    top = b.y
    for i, (chip, tt) in enumerate(rows):
        y = top + i * row_h
        b.at(chip, 0, y, anim="fade")
        b.at(tt, text_x, y + (dot - line_h(ft)) / 2)
    b.y = top + row_h * len(rows) - gap_y

    if o.get("note"):
        b.gap(26)
        b.note(o["note"], accent, size=25)
    return b.done()


def layout_chips(o, b: Block, accent):
    b.label(o["label"], accent)
    b.gap(24)

    ft = font("label", 36 * b.S)
    fn = font("text", 25 * b.S)
    mark = int(11 * b.S)
    text_x = mark + 20 * b.S

    for i, it in enumerate(o["items"]):
        txt = it["text"] if isinstance(it, dict) else str(it)
        sub = it.get("note") if isinstance(it, dict) else None
        y = b.y
        sq = Image.new("RGBA", (mark, mark), (0, 0, 0, 0))
        ImageDraw.Draw(sq).rectangle([0, 0, mark - 1, mark - 1], fill=accent + (255,))
        b.at(sq, 1 * b.S, y + (line_h(ft) - mark) / 2)
        b.at(text_tile(txt, ft, PALETTE["ink"]), text_x, y)
        b.y = y + line_h(ft)
        b.w = max(b.w, text_x + ft.getlength(txt))
        if sub:
            b.gap(2)
            for ln in wrap(sub, fn, b.max_w - text_x):
                b.at(text_tile(ln, fn, PALETTE["ink_3"]), text_x, b.y)
                b.y += line_h(fn)
        if i < len(o["items"]) - 1:
            b.gap(22)

    if o.get("note"):
        b.gap(26)
        b.note(o["note"], accent, size=25)
    return b.done()


def layout_kicker(o, b: Block, accent):
    b.accent_rule(accent)
    b.gap(26)
    b.lines(o["text"], "display", 66, PALETTE["ink"], lead=1.1)
    if o.get("note"):
        b.gap(16)
        b.note(o["note"], size=30)
    return b.done()


def layout_lower_third(o, b: Block, accent):
    b.accent_rule(accent, w=88, h=5)
    b.gap(24)
    f = fit_font("display", o["name"], 78 * b.S, b.max_w)
    b.put(text_tile(o["name"], f, PALETTE["ink"]), advance=line_h(f) * 0.98)
    if o.get("role"):
        b.gap(10)
        b.note(o["role"], PALETTE["ink_2"], size=30)
    return b.done()


def layout_title(o, b: Block, accent):
    b.accent_rule(accent, w=120, h=5)
    b.gap(30)
    f = fit_font("display", o["word"], 118 * b.S, b.max_w)
    b.put(text_tile(o["word"], f, PALETTE["ink"]), advance=line_h(f) * 0.98)
    if o.get("tag"):
        b.gap(14)
        b.lines(o["tag"], "body", 34, PALETTE["ink_2"])
    return b.done()


LAYOUTS = {
    "stat": layout_stat,
    "ratio": layout_ratio,
    "bar": layout_bar,
    "steps": layout_steps,
    "chips": layout_chips,
    "kicker": layout_kicker,
    "lower_third": layout_lower_third,
    "title": layout_title,
}


# ---------------------------------------------------------------- card assembly


def content_max_w(placement: str, W: int, S: float, margin_x: float) -> float:
    if placement in {"right_panel", "left_panel"}:
        return min(600 * S, W * 0.44)
    if placement == "lower_band":
        return W - 2 * margin_x
    if placement == "center":
        return min(940 * S, W * 0.62)
    return min(840 * S, W * 0.54)


def place(placement: str, W: int, H: int, pw: float, ph: float, mx: float, my: float):
    if placement == "lower_left":
        return mx, H - my - ph
    if placement == "lower_right":
        return W - mx - pw, H - my - ph
    if placement == "top_left":
        return mx, my
    if placement == "right_panel":
        return W - mx - pw, my + (H - 2 * my - ph) * 0.40
    if placement == "left_panel":
        return mx, my + (H - 2 * my - ph) * 0.40
    if placement == "lower_band":
        return mx, H - my - ph
    return (W - pw) / 2, (H - ph) / 2  # center


def build_card(o: dict, W: int, H: int, S: float) -> list[El]:
    """Lay the content out, wrap it in its scrim, and move the whole thing into place."""
    accent = PALETTE[o.get("accent", "gold")]
    placement = o.get("placement", "lower_left")
    scrim = o.get("scrim", "panel")

    mx, my = 96 * S, 84 * S
    pad_x, pad_y = 46 * S, 42 * S

    b = Block(S, content_max_w(placement, W, S, mx))
    els, cw, ch = LAYOUTS[o["kind"]](o, b, accent)

    scrim_els: list[El] = []
    if scrim == "panel":
        pw, ph = cw + 2 * pad_x, ch + 2 * pad_y
        x, y = place(placement, W, H, pw, ph, mx, my)

        # A soft shadow is the difference between a card that sits on the video and one
        # that floats in front of it. It also buys legibility over a busy background.
        blur = int(20 * S)
        sh = Image.new("RGBA", (int(pw) + blur * 4, int(ph) + blur * 4), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [blur * 2, blur * 2, blur * 2 + pw, blur * 2 + ph], int(18 * S), fill=(0, 0, 0, 110))
        sh = sh.filter(ImageFilter.GaussianBlur(blur))
        scrim_els.append(El(sh, int(x - blur * 2), int(y - blur * 2 + 10 * S), anim="fade", layer="scrim"))

        panel = Image.new("RGBA", (int(pw), int(ph)), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle(
            [0, 0, int(pw) - 1, int(ph) - 1], int(18 * S),
            fill=PALETTE["panel"] + (226,), outline=(255, 255, 255, 28), width=max(1, int(2 * S)))
        scrim_els.append(El(panel, int(x), int(y), anim="fade", layer="scrim"))
        ox, oy = x + pad_x, y + pad_y

    elif scrim == "gradient":
        # A band that fades up out of nothing — the classic way to seat a title card on
        # footage without boxing it in.
        ph = ch + 2 * pad_y
        x, y = place(placement, W, H, W - 2 * mx, ph, mx, my)
        band_top = max(0, int(y - 120 * S))
        band_h = H - band_top
        band = Image.new("RGBA", (W, band_h), (0, 0, 0, 0))
        px = band.load()
        r, g, bl = PALETTE["bg_2"]
        for row in range(band_h):
            t = row / max(1, band_h - 1)
            a = int(238 * (t ** 0.85))
            for col in range(W):
                px[col, row] = (r, g, bl, a)
        scrim_els.append(El(band, 0, band_top, anim="fade", layer="scrim"))
        ox, oy = x, y

    else:
        pw, ph = cw, ch
        x, y = place(placement, W, H, pw, ph, mx, my)
        ox, oy = x, y

    for el in els:
        el.x += int(ox)
        el.y += int(oy)

    # Stagger the content in reading order; the scrim always leads.
    for i, el in enumerate(els):
        el.delay = min(STAGGER_MAX, i * STAGGER)
    return scrim_els + els


# ---------------------------------------------------------------- animation


def ease_out(p: float) -> float:
    return 1 - (1 - p) ** 3


def ease_in(p: float) -> float:
    return p ** 3


def render_frame(els, size, t_ms, dur_ms, in_ms, out_ms, S) -> Image.Image:
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    g_alpha, g_dy = 1.0, 0
    out_start = dur_ms - out_ms
    if out_ms > 0 and t_ms >= out_start:
        q = ease_in(min(1.0, (t_ms - out_start) / out_ms))
        g_alpha, g_dy = 1 - q, int(-12 * S * q)

    for el in els:
        span = max(1.0, in_ms * 0.72)
        p = 1.0 if in_ms <= 0 else min(1.0, max(0.0, (t_ms - el.delay * in_ms) / span))
        e = ease_out(p)
        tile, alpha, dy = el.img, e * g_alpha, 0

        if el.anim == "wipe_x":
            w = max(1, int(round(tile.width * e)))
            if w < tile.width:
                tile = tile.crop((0, 0, w, tile.height))
            alpha = g_alpha
        elif el.anim == "rise":
            dy = int((1 - e) * RISE_PX * S)

        if alpha <= 0.004:
            continue
        paste_over(canvas, with_alpha(tile, alpha), el.x, el.y + dy + g_dy)
    return canvas


# ---------------------------------------------------------------- encoding


def encode(frames, path: Path, size, fps: int, fmt: str) -> None:
    """Pipe raw RGBA frames into ffmpeg. Raw is used over PNG because encoding 100+
    stills per card just to have ffmpeg decode them again is most of the runtime."""
    if fmt == "webm":
        codec = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "26",
                 "-auto-alt-ref", "0", "-row-mt", "1"]
    elif fmt == "mov":
        codec = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                 "-alpha_bits", "16", "-vendor", "apl0"]
    else:
        raise ValueError(fmt)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pixel_format", "rgba",
           "-video_size", f"{size[0]}x{size[1]}", "-framerate", str(fps), "-i", "-",
           *codec, str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for fr in frames:
            proc.stdin.write(fr.tobytes())
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed writing {path.name}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- validation


def validate(spec: dict) -> list[str]:
    errs = []
    if spec.get("schema") != "rtf.overlay/v1":
        errs.append(f"schema is {spec.get('schema')!r}, expected 'rtf.overlay/v1'")
    seen = set()
    for i, o in enumerate(spec.get("overlays", [])):
        who = o.get("id", f"#{i}")
        if not o.get("id"):
            errs.append(f"{who}: no id")
        elif o["id"] in seen:
            errs.append(f"{who}: duplicate id")
        seen.add(o.get("id"))
        if o.get("kind") not in KINDS:
            errs.append(f"{who}: kind {o.get('kind')!r} not one of {sorted(KINDS)}")
        if o.get("placement", "lower_left") not in PLACEMENTS:
            errs.append(f"{who}: placement {o['placement']!r} not one of {sorted(PLACEMENTS)}")
        if o.get("scrim", "panel") not in SCRIMS:
            errs.append(f"{who}: scrim {o['scrim']!r} not one of {sorted(SCRIMS)}")
        if o.get("accent", "gold") not in PALETTE:
            errs.append(f"{who}: accent {o['accent']!r} is not a palette colour")
        if not o.get("cue"):
            errs.append(f"{who}: no cue — an editor cannot place this card by ear")
        req = {"stat": ["value", "label"], "ratio": ["left", "right", "label"],
               "bar": ["pct", "value", "label"], "steps": ["label", "items"],
               "chips": ["label", "items"], "kicker": ["text"],
               "lower_third": ["name"], "title": ["word"]}.get(o.get("kind"), [])
        for k in req:
            if o.get(k) in (None, "", []):
                errs.append(f"{who}: {o.get('kind')} needs {k}")
    return errs


def check_cues(spec: dict, root: Path) -> list[str]:
    """A cue that no longer appears in the script is a graphic about a deleted sentence."""
    target = root / spec["target"] if spec.get("target") else None
    if not target or not target.exists():
        return [f"target {spec.get('target')!r} not found — cues unverified"]
    script = target.read_text()
    warn = []
    for o in spec["overlays"]:
        # The cue is a paraphrase for the ear, so match on its longest distinctive run
        # rather than the whole sentence.
        probe = max(o["cue"].replace("—", "-").split(" - "), key=len).strip(" .,—-")
        words = probe.split()
        stem = " ".join(words[:6])
        if stem and stem.lower() not in script.lower():
            warn.append(f"{o['id']}: cue not found verbatim in {spec['target']} — {stem!r}")
    return warn


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path, help="output dir (default content/videos/<spec id>)")
    ap.add_argument("--formats", default="png,webm", help="png,webm,mov")
    ap.add_argument("--aspect", choices=sorted(ASPECTS), help="override the spec's canvas")
    ap.add_argument("--only", action="append", help="render just these ids (repeatable)")
    ap.add_argument("--check", action="store_true", help="validate the spec, render nothing")
    args = ap.parse_args()

    spec = yaml.safe_load(args.spec.read_text())
    root = args.spec.resolve().parents[3]  # <root>/content/lib/overlays/<spec>

    errs = validate(spec)
    if errs:
        print("\n".join(f"  ! {e}" for e in errs), file=sys.stderr)
        sys.exit(f"{len(errs)} problem(s) in {args.spec.name}")
    for w in check_cues(spec, root):
        print(f"  ~ {w}")

    if args.check:
        print(f"ok — {len(spec['overlays'])} overlays in {args.spec.name}")
        return

    fmts = [f.strip() for f in args.formats.split(",") if f.strip()]
    if {"webm", "mov"} & set(fmts) and not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not on PATH — use --formats png, or install ffmpeg")

    canvas = spec.get("canvas", {})
    if args.aspect:
        W, H = ASPECTS[args.aspect]
    else:
        W, H = int(canvas.get("width", 1920)), int(canvas.get("height", 1080))
    fps = int(canvas.get("fps", 30))
    S = min(W, H) / 1080.0

    out = args.out or (root / "content" / "videos" / spec["id"])
    out.mkdir(parents=True, exist_ok=True)

    defaults = spec.get("defaults", {})
    manifest = {"schema": "rtf.overlayset/v1", "id": spec["id"], "title": spec.get("title"),
                "target": spec.get("target"),
                "canvas": {"width": W, "height": H, "fps": fps},
                "formats": fmts, "overlays": []}

    for o in spec["overlays"]:
        if args.only and o["id"] not in args.only:
            continue
        o = {**defaults, **o}
        dur = int(o.get("duration_ms", 4000))
        in_ms = int(o.get("in_ms", 550))
        out_ms = int(o.get("out_ms", 450))

        els = build_card(o, W, H, S)
        n = max(1, round(dur / 1000 * fps))
        files = {}

        if "png" in fmts:
            # The still is the card fully arrived: past every stagger, before the exit.
            hero = render_frame(els, (W, H), dur - out_ms - 1, dur, in_ms, out_ms, S)
            p = out / f"{o['id']}.png"
            hero.save(p)
            files["png"] = p.name

        for fmt in ("webm", "mov"):
            if fmt not in fmts:
                continue
            p = out / f"{o['id']}.{fmt}"
            frames = (render_frame(els, (W, H), round(i * 1000 / fps), dur, in_ms, out_ms, S)
                      for i in range(n))
            encode(frames, p, (W, H), fps, fmt)
            files[fmt] = p.name

        manifest["overlays"].append({
            "id": o["id"], "kind": o["kind"], "vo_line": o.get("vo_line"), "cue": o.get("cue"),
            "placement": o.get("placement", "lower_left"), "accent": o.get("accent", "gold"),
            "duration_ms": dur, "files": files,
            "sha256": {k: sha256(out / v) for k, v in files.items()},
        })
        sizes = " ".join(f"{k} {(out / v).stat().st_size // 1024}K" for k, v in files.items())
        print(f"  {o['id']:<26} {o['kind']:<12} {sizes}")

    (out / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100))
    print(f"\n{len(manifest['overlays'])} overlays → {out}")


if __name__ == "__main__":
    main()
