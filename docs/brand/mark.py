"""Emit the radial bar group inside `thumbnail.svg`, for pasting back into it.

The 64 bars are the one part of the thumbnail nobody should hand-edit — they are a
formula, and nudging individual rects would only make the symmetry drift. Everything
*else* in the SVG is hand-authored and this script does not touch it: run this, paste the
output over the `<g transform="translate(640, 274)">` group, re-render the PNG.

    python3 mark.py

Symmetry comes from using cosine harmonics only. `cos` is even, so height is a function of
the angle's distance from vertical and the left half mirrors the right. Integer harmonics
keep it seamless where the circle wraps at 360 degrees.
"""

import math

CX, CY = 640.0, 274.0
R0 = 96.0   # bars start here, just clear of the disc edge at r=88
N = 64      # bar count
W = 5.0     # bar width


def norm(theta: float) -> float:
    """Bar height in 0..1 — three cosine harmonics summed into a spectrum."""
    s = (0.50 * math.cos(3 * theta)
         + 0.28 * math.cos(7 * theta)
         + 0.22 * math.cos(11 * theta))
    return 0.5 + 0.5 * s


def main() -> None:
    print(f'    <g transform="translate({CX:.0f}, {CY:.0f})" fill="#d97f4a">')
    for i in range(N):
        deg = 360.0 * i / N
        n = norm(math.radians(deg))
        height = 15.0 + 54.0 * n
        # Opacity tracks height so the long bars read as the loud ones.
        opacity = 0.30 + 0.65 * n
        # Each bar is authored pointing straight up from the origin and then rotated into
        # place, so `rotate` does the trigonometry rather than the coordinates.
        print(
            f'      <rect x="{-W / 2:.2f}" y="{-(R0 + height):.2f}" '
            f'width="{W:.2f}" height="{height:.2f}" rx="{W / 2:.2f}" '
            f'transform="rotate({deg:.3f})" opacity="{opacity:.3f}"/>'
        )
    print("    </g>")


if __name__ == "__main__":
    main()
