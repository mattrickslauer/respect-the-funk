"""Drawing primitives for the architecture poster.

Every page is a 1600x1000 CSS-pixel canvas. Boxes are absolutely positioned HTML —
so labels wrap, which SVG text will not — and the connectors are one SVG layer
underneath them, drawn from the *same* coordinates the boxes were placed at. That is
the whole reason this is generated rather than drawn: a node cannot move without its
arrows moving with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

# ---------------------------------------------------------------- page geometry
# A 1800x1090 canvas inside an 1800x1200 sheet. Chromium turns CSS px into points at
# 0.75, so the printed page is 18.75in x 12.5in — a poster you can read at arm's length
# and zoom into for the payload keys.
W, H = 1800, 1090

# ---------------------------------------------------------------- palette
# Carried over from infra/diagram.py so the two documents speak one language.
INK = "#14171F"
PAPER = "#FBFAF7"
RULE = "#DED8CC"
MUTED = "#6E7480"
FAINT = "#F3F0E9"

HOT = "#D32127"    # durable bytes — the storage path
ASYNC = "#7B61FF"  # queued work — never inside a request
DIRECT = "#0B7A4B" # browser <-> bucket, presigned, never through compute
SYNC = "#2F5DA8"   # ordinary request/response
GATE = "#B45309"   # a refusal, on purpose
PROV = "#0E7C86"   # provenance — the claim that travels in the file
EXT = "#7A4FA3"    # a vendor outside the boundary

KIND = {
    #            border   fill       text  accent
    "actor":    (INK,     INK,       "#FFFFFF", "#FFFFFF"),
    "surface":  (RULE,    "#FFFFFF", INK,   SYNC),
    "service":  (RULE,    "#FFFFFF", INK,   INK),
    "store":    ("#EBBFBF", "#FDF4F3", INK, HOT),
    "queue":    ("#D6CDF5", "#F7F4FF", INK, ASYNC),
    "worker":   ("#D6CDF5", "#F4F0FF", INK, ASYNC),
    "ext":      ("#D8C9E8", "#FAF6FE", INK, EXT),
    "gate":     ("#E5C79A", "#FEF7EC", INK, GATE),
    "prov":     ("#AFD4D6", "#F1FAFA", INK, PROV),
    "direct":   ("#B8D9C7", "#F2FAF5", INK, DIRECT),
    "ghost":    (RULE,     FAINT,     MUTED, MUTED),
    "plain":    (RULE,     "#FFFFFF", INK,  MUTED),
}


def esc(s) -> str:
    return escape(str(s), quote=False)


def rounded_poly(pts, r=12) -> str:
    """An orthogonal polyline with its corners eased. Waypoints are absolute."""
    if len(pts) < 2:
        return ""
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        rr = min(r, _dist(x0, y0, x1, y1) / 2, _dist(x1, y1, x2, y2) / 2)
        ux, uy = _unit(x1 - x0, y1 - y0)
        vx, vy = _unit(x2 - x1, y2 - y1)
        d.append(f"L{x1 - ux * rr:.1f},{y1 - uy * rr:.1f}")
        d.append(f"Q{x1:.1f},{y1:.1f} {x1 + vx * rr:.1f},{y1 + vy * rr:.1f}")
    d.append(f"L{pts[-1][0]:.1f},{pts[-1][1]:.1f}")
    return " ".join(d)


def _dist(x0, y0, x1, y1):
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 or 1.0


def _unit(dx, dy):
    m = (dx * dx + dy * dy) ** 0.5 or 1.0
    return dx / m, dy / m


@dataclass
class Node:
    id: str
    x: int
    y: int
    w: int
    h: int
    title: str
    kind: str = "plain"
    sub: str = ""
    items: tuple = ()
    badge: str = ""
    foot: str = ""
    mono: bool = False
    align: str = "left"
    size: str = ""


@dataclass
class Canvas:
    nodes: dict = field(default_factory=dict)
    order: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    decor: list = field(default_factory=list)   # raw SVG drawn under everything
    over: list = field(default_factory=list)    # raw HTML drawn over everything

    # -- placement ---------------------------------------------------------
    def box(self, id, x, y, w, h, title, **kw) -> Node:
        # A bare string here means one bullet, not one bullet per character. A missing
        # trailing comma in a one-element tuple is otherwise a silent, spectacular defect.
        if isinstance(kw.get("items"), str):
            kw["items"] = (kw["items"],)
        n = Node(id=id, x=x, y=y, w=w, h=h, title=title, **kw)
        self.nodes[id] = n
        self.order.append(id)
        return n

    def band(self, x, y, w, h, label, color=RULE, fill="none", dash=None, label_side="tl"):
        """A grouping frame. Drawn under the boxes, labelled on its own edge."""
        d = f'stroke-dasharray="{dash}"' if dash else ""
        self.decor.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" '
            f'stroke="{color}" stroke-width="1.5" {d}/>'
        )
        if label:
            if label_side == "tl":
                lx, ly, anchor = x + 18, y - 8, "start"
            elif label_side == "tr":
                lx, ly, anchor = x + w - 18, y - 8, "end"
            else:
                lx, ly, anchor = x + 18, y + h + 18, "start"
            self.decor.append(
                f'<text x="{lx}" y="{ly}" text-anchor="{anchor}" fill="{color}" '
                f'font-family="Helvetica Neue, Helvetica, Arial" font-size="12.5" '
                f'font-weight="700" letter-spacing="1.6">{esc(label.upper())}</text>'
            )

    # -- anchors -----------------------------------------------------------
    def _anchor(self, ref: str):
        """`id`, `id:right`, `id:right@0.25` -> (x, y, side)."""
        frac = 0.5
        side = None
        if "@" in ref:
            ref, f = ref.split("@")
            frac = float(f)
        if ":" in ref:
            ref, side = ref.split(":")
        n = self.nodes[ref]
        return n, side, frac

    @staticmethod
    def _point(n: Node, side: str, frac: float):
        if side == "right":
            return n.x + n.w, n.y + n.h * frac
        if side == "left":
            return n.x, n.y + n.h * frac
        if side == "top":
            return n.x + n.w * frac, n.y
        return n.x + n.w * frac, n.y + n.h  # bottom

    def _auto_sides(self, a: Node, b: Node):
        ax, ay = a.x + a.w / 2, a.y + a.h / 2
        bx, by = b.x + b.w / 2, b.y + b.h / 2
        if abs(bx - ax) >= abs(by - ay):
            return ("right", "left") if bx > ax else ("left", "right")
        return ("bottom", "top") if by > ay else ("top", "bottom")

    def edge(self, src, dst, *, color=SYNC, dashed=False, label="", bend=0.5,
             width=2.0, arrow=True, back=False, label_pos=0.5, label_dy=0, r=12,
             label_anchor="middle"):
        a, aside, afrac = self._anchor(src)
        b, bside, bfrac = self._anchor(dst)
        if aside is None or bside is None:
            auto_a, auto_b = self._auto_sides(a, b)
            aside = aside or auto_a
            bside = bside or auto_b
        p1 = self._point(a, aside, afrac)
        p2 = self._point(b, bside, bfrac)
        self.edges.append(dict(
            p1=p1, p2=p2, aside=aside, bside=bside, color=color, dashed=dashed,
            label=label, bend=bend, width=width, arrow=arrow, back=back,
            label_pos=label_pos, label_dy=label_dy, r=r, label_anchor=label_anchor,
        ))

    def poly(self, pts, *, color=SYNC, dashed=False, width=2.0, label="", label_at=None,
             arrow=True, back=False, r=12, label_anchor="middle"):
        """A connector routed through explicit waypoints — for rails that have to go
        around the map rather than through it."""
        self.edges.append(dict(
            d=rounded_poly(pts, r), p1=pts[0], p2=pts[-1], aside="right", bside="left",
            color=color, dashed=dashed, label=label, bend=0.5, width=width, arrow=arrow,
            back=back, label_pos=0.5, label_dy=0, r=r, label_anchor=label_anchor,
            label_xy=label_at))

    def link(self, x1, y1, x2, y2, aside="right", bside="left", **kw):
        """A connector between bare coordinates, for anything not box-to-box."""
        kw.setdefault("color", SYNC)
        self.edges.append(dict(
            p1=(x1, y1), p2=(x2, y2), aside=aside, bside=bside,
            dashed=kw.pop("dashed", False), label=kw.pop("label", ""),
            bend=kw.pop("bend", 0.5), width=kw.pop("width", 2.0),
            arrow=kw.pop("arrow", True), back=kw.pop("back", False),
            label_pos=kw.pop("label_pos", 0.5), label_dy=kw.pop("label_dy", 0),
            r=kw.pop("r", 12), label_anchor=kw.pop("label_anchor", "middle"),
            **kw))

    def note(self, x, y, w, text, color=MUTED, size=11.5, align="left", weight=400):
        self.over.append(
            f'<div class="note" style="left:{x}px;top:{y}px;width:{w}px;color:{color};'
            f'font-size:{size}px;text-align:{align};font-weight:{weight}">{text}</div>'
        )

    def tag(self, x, y, text, color=MUTED, size=11):
        self.over.append(
            f'<div class="tag" style="left:{x}px;top:{y}px;color:{color};border-color:{color}33;'
            f'font-size:{size}px">{esc(text)}</div>'
        )

    def html(self, x, y, w, inner, cls="raw", extra=""):
        self.over.append(
            f'<div class="{cls}" style="left:{x}px;top:{y}px;width:{w}px;{extra}">{inner}</div>'
        )

    # -- geometry ----------------------------------------------------------
    @staticmethod
    def _path(p1, p2, aside, bside, bend, r):
        x1, y1 = p1
        x2, y2 = p2
        horiz_start = aside in ("left", "right")
        horiz_end = bside in ("left", "right")

        def corner(px, py, dx1, dy1, dx2, dy2):
            """Rounded turn at (px,py) arriving along (dx1,dy1), leaving along (dx2,dy2)."""
            return (f"L{px - dx1 * r:.1f},{py - dy1 * r:.1f} "
                    f"Q{px:.1f},{py:.1f} {px + dx2 * r:.1f},{py + dy2 * r:.1f} ")

        if abs(y1 - y2) < 1.2 and horiz_start and horiz_end:
            return f"M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}"
        if abs(x1 - x2) < 1.2 and not horiz_start and not horiz_end:
            return f"M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}"

        if horiz_start and horiz_end:
            mx = x1 + (x2 - x1) * bend
            sx = 1 if x2 > x1 else -1
            sy = 1 if y2 > y1 else -1
            rr = min(r, abs(mx - x1), abs(x2 - mx), abs(y2 - y1) / 2)
            d = f"M{x1:.1f},{y1:.1f} "
            d += f"L{mx - sx * rr:.1f},{y1:.1f} Q{mx:.1f},{y1:.1f} {mx:.1f},{y1 + sy * rr:.1f} "
            d += f"L{mx:.1f},{y2 - sy * rr:.1f} Q{mx:.1f},{y2:.1f} {mx + sx * rr:.1f},{y2:.1f} "
            d += f"L{x2:.1f},{y2:.1f}"
            return d
        if (not horiz_start) and (not horiz_end):
            my = y1 + (y2 - y1) * bend
            sx = 1 if x2 > x1 else -1
            sy = 1 if y2 > y1 else -1
            rr = min(r, abs(my - y1), abs(y2 - my), abs(x2 - x1) / 2)
            d = f"M{x1:.1f},{y1:.1f} "
            d += f"L{x1:.1f},{my - sy * rr:.1f} Q{x1:.1f},{my:.1f} {x1 + sx * rr:.1f},{my:.1f} "
            d += f"L{x2 - sx * rr:.1f},{my:.1f} Q{x2:.1f},{my:.1f} {x2:.1f},{my + sy * rr:.1f} "
            d += f"L{x2:.1f},{y2:.1f}"
            return d
        # one elbow
        if horiz_start:
            px, py = x2, y1
            sx = 1 if x2 > x1 else -1
            sy = 1 if y2 > y1 else -1
            rr = min(r, abs(x2 - x1), abs(y2 - y1))
            return (f"M{x1:.1f},{y1:.1f} L{px - sx * rr:.1f},{py:.1f} "
                    f"Q{px:.1f},{py:.1f} {px:.1f},{py + sy * rr:.1f} L{x2:.1f},{y2:.1f}")
        px, py = x1, y2
        sx = 1 if x2 > x1 else -1
        sy = 1 if y2 > y1 else -1
        rr = min(r, abs(x2 - x1), abs(y2 - y1))
        return (f"M{x1:.1f},{y1:.1f} L{px:.1f},{py - sy * rr:.1f} "
                f"Q{px:.1f},{py:.1f} {px + sx * rr:.1f},{py:.1f} L{x2:.1f},{y2:.1f}")

    @staticmethod
    def _label_point(e):
        x1, y1 = e["p1"]
        x2, y2 = e["p2"]
        t = e["label_pos"]
        if e["aside"] in ("left", "right") and e["bside"] in ("left", "right"):
            mx = x1 + (x2 - x1) * e["bend"]
            if t <= 0.5:
                return x1 + (mx - x1) * (t * 2), y1
            return mx + (x2 - mx) * ((t - 0.5) * 2), y2
        if e["aside"] in ("top", "bottom") and e["bside"] in ("top", "bottom"):
            my = y1 + (y2 - y1) * e["bend"]
            if t <= 0.5:
                return x1, y1 + (my - y1) * (t * 2)
            return x2, my + (y2 - my) * ((t - 0.5) * 2)
        return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t

    # -- rendering ---------------------------------------------------------
    def render_svg(self) -> str:
        colors = sorted({e["color"] for e in self.edges} | {HOT, ASYNC, DIRECT, SYNC, GATE, PROV})
        defs = "".join(
            f'<marker id="ah{i}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0.6 L9.6,5 L0,9.4 z" fill="{c}"/></marker>'
            for i, c in enumerate(colors)
        )
        idx = {c: i for i, c in enumerate(colors)}
        out = [f'<svg class="wires" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
               f"<defs>{defs}</defs>"]
        out += self.decor
        labels = []
        for e in self.edges:
            d = e.get("d") or self._path(
                e["p1"], e["p2"], e["aside"], e["bside"], e["bend"], e["r"])
            i = idx[e["color"]]
            attrs = (f'fill="none" stroke="{e["color"]}" stroke-width="{e["width"]}" '
                     f'stroke-linecap="round" stroke-linejoin="round"')
            if e["dashed"]:
                attrs += ' stroke-dasharray="7 5"'
            if e["arrow"]:
                attrs += f' marker-end="url(#ah{i})"'
            if e["back"]:
                attrs += f' marker-start="url(#ah{i})"'
            out.append(f'<path d="{d}" {attrs}/>')
            if e["label"]:
                lx, ly = e.get("label_xy") or self._label_point(e)
                labels.append((lx, ly + e["label_dy"], e["label"], e["color"], e["label_anchor"]))
        for lx, ly, text, color, anchor in labels:
            lines = text.split("|")
            n = len(lines)
            wpx = max(len(s) for s in lines) * 5.9 + 12
            ox = {"middle": -wpx / 2, "start": -6, "end": -wpx + 6}[anchor]
            out.append(
                f'<rect x="{lx + ox:.1f}" y="{ly - 8 - (n - 1) * 6:.1f}" width="{wpx:.1f}" '
                f'height="{16 + (n - 1) * 12}" rx="4" fill="{PAPER}" opacity="0.94"/>')
            for k, s in enumerate(lines):
                out.append(
                    f'<text x="{lx:.1f}" y="{ly + 3.5 - (n - 1) * 6 + k * 12:.1f}" '
                    f'text-anchor="{anchor}" fill="{color}" '
                    f'font-family="Helvetica Neue, Helvetica, Arial" font-size="10.5" '
                    f'font-weight="600">{esc(s)}</text>')
        out.append("</svg>")
        return "".join(out)

    def render_boxes(self) -> str:
        out = []
        for id in self.order:
            n = self.nodes[id]
            border, fill, text, accent = KIND[n.kind]
            style = (f"left:{n.x}px;top:{n.y}px;width:{n.w}px;height:{n.h}px;"
                     f"border-color:{border};background:{fill};color:{text};"
                     f"text-align:{n.align}")
            inner = []
            if n.badge:
                inner.append(f'<div class="badge" style="background:{accent}">{esc(n.badge)}</div>')
            tcls = "t mono" if n.mono else "t"
            tsize = f' style="font-size:{n.size}px"' if n.size else ""
            inner.append(f'<div class="{tcls}"{tsize}>{n.title}</div>')
            if n.sub:
                inner.append(f'<div class="s" style="color:{accent}">{n.sub}</div>')
            if n.items:
                lis = "".join(f"<li>{i}</li>" for i in n.items)
                inner.append(f'<ul class="i">{lis}</ul>')
            if n.foot:
                inner.append(f'<div class="f">{n.foot}</div>')
            out.append(f'<div class="node k-{n.kind}" style="{style}">{"".join(inner)}</div>')
        out += self.over
        return "".join(out)


def page(num: int, total: int, eyebrow: str, title: str, standfirst: str, canvas: Canvas,
         foot: str = "") -> str:
    return f"""
<section class="page">
  <header class="ph">
    <div class="ph-l"><span class="eyebrow">{esc(eyebrow)}</span>
      <h2>{title}</h2></div>
    <div class="ph-r">{standfirst}</div>
    <div class="pn">{num:02d}<span>/{total:02d}</span></div>
  </header>
  <div class="canvas">{canvas.render_svg()}{canvas.render_boxes()}</div>
  <footer class="pf">{foot}</footer>
</section>"""


def chip(text, color=MUTED, solid=False):
    if solid:
        return f'<span class="chip solid" style="background:{color}">{esc(text)}</span>'
    return f'<span class="chip" style="color:{color};border-color:{color}55">{esc(text)}</span>'


def code(text):
    return f'<code>{esc(text)}</code>'
