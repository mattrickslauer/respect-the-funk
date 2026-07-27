#!/usr/bin/env python3
"""Measure the cut rhythm of a video on its own beat grid, and how much of that rhythm
two videos actually share.

FORMAT-SPEC opens its cadence section with the one load-bearing claim in either spec
that has no number under it:

    "Congruence between two videos is felt as RHYTHM before it is understood as
     meaning: visual, beat, line, beat, drop."

Everything else in this project got measured. `rtf.check()` enforces the congruence of
the *ground* — axis coverage, run length, escalation. `rtf.cadence_drift()` reports how
far a filmed clip sits off its grid. But nothing measures the rhythm itself, and nothing
compares two of them, so "these two videos feel like the same channel" is still a taste
argument at 2am — exactly the kind this repo converts into arithmetic.

This does the conversion. A video goes in; an ONSET TRAIN comes out — every cut expressed
in beats relative to the drop, negative before and positive after. Every number below is
arithmetic on that train:

    density      cuts per beat, hook vs carousel
    regularity   coefficient of variation of the gaps (0.00 = metronomic)
    seam         the ratio of the last pre-drop gap to the first post-drop gap
    phase        where each cut sits in the 4/4 bar, which requires knowing the drop's
                 own bar phase — see BAR PHASE below
    congruency   Jaccard over two trains: how many cut positions two videos share

    .venv/bin/python bin/measure_pacing.py --all
    .venv/bin/python bin/measure_pacing.py california-test generated-test

BAR PHASE. `measure_beat.py` already reports whether the drop landed on the downbeat, and
for `losing-sleep` it did not: the drop at 124784ms is one beat after the bar line at
124304ms. The song file records the drop and not that offset, so this tool re-derives it
from `measured.drop_ms` against the nearest bar line and says so out loud. It matters
unevenly, which is the point — a carousel cutting on every beat hits all four bar
positions equally and cannot be off-phase, while a cadence cutting every two to seven
beats is audible phase and nothing else.

WHAT IS A CUT. The cadence schema declares `motion` and `move` per slot but never says
whether a slot boundary is a CUT or a continuation of one shot. So this tool infers:
`requires.single_take` means zero internal cuts, and otherwise every slot boundary is one.
The inference is printed with the result rather than hidden inside it — it is the first
thing a `transition:` field on the slot would make unnecessary.
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import rtf  # noqa: E402


# ---------------------------------------------------------------- trains


class Train:
    """A video's cuts, in beats relative to the drop. Negative before, positive after.

    The drop is 0.0 and is always present: it is the one cut every video of every format
    in this project shares, and the origin the whole grid is counted from.
    """

    def __init__(self, name: str, kind: str, pre: list, post: list, beat_ms: float,
                 drop_bar_pos: int, notes: list = None):
        self.name, self.kind = name, kind
        self.pre = sorted(set(float(x) for x in pre))       # all ≤ 0
        self.post = sorted(set(float(x) for x in post))     # all ≥ 0
        self.beat_ms, self.drop_bar_pos = beat_ms, drop_bar_pos
        self.notes = notes or []

    @property
    def onsets(self) -> list:
        return sorted(set(self.pre) | set(self.post))

    def gaps(self, side: str) -> list:
        xs = self.pre if side == "pre" else self.post
        return [round(b - a, 4) for a, b in zip(xs, xs[1:])]

    def span(self, side: str) -> float:
        xs = self.pre if side == "pre" else self.post
        return abs(xs[-1] - xs[0]) if len(xs) > 1 else 0.0

    def density(self, side: str) -> float:
        """Cuts per beat. The drop is not counted as belonging to either side — it is the
        boundary, and counting it twice would flatter both."""
        g = self.gaps(side)
        return len(g) / self.span(side) if self.span(side) else 0.0

    def regularity(self, side: str) -> float:
        """Coefficient of variation of the gaps. 0.00 is a metronome."""
        g = self.gaps(side)
        if len(g) < 2:
            return 0.0
        m = sum(g) / len(g)
        var = sum((x - m) ** 2 for x in g) / len(g)
        return (var ** 0.5) / m if m else 0.0

    def seam(self):
        """(last gap before the drop) / (first gap after it). The rate change AT the cut.

        This is the rhythmic quantity a viewer meets head-on. Everything either side of it
        is an average; this is a single edge.
        """
        a, b = self.gaps("pre"), self.gaps("post")
        if not b:
            return None
        if not a:
            return float("inf")     # a single take: no pre-drop rate to accelerate from
        return a[-1] / b[0]

    def bar_positions(self, side: str = None) -> list:
        """Where each cut sits in the 4/4 bar, as 1–4, given the drop's own bar position."""
        xs = {"pre": self.pre, "post": self.post}.get(side, self.onsets)
        return [int((self.drop_bar_pos + round(b)) % 4) + 1 for b in xs]


def _bar_pos_of_drop(song: dict, beat_ms: float) -> tuple:
    """Which beat of the bar the drop landed on, 0-indexed.

    Prefers `measured.downbeat_ms` — a field `rtf.song/v1` does not yet have, and should.
    Without it there is no way to tell a bar line from any other beat, and the fallback
    below is an ASSUMPTION, not a measurement: drops land on bar lines, and for
    `losing-sleep` measure_structure.py confirms it (5 of the 6 largest arrangement
    changes near the drop share one residue mod 4, and that residue is the drop's).

    What must NOT be done is what an earlier draft of this file did — take
    `drop_ms % bar_ms` and call it phase. That measures the drop against the start of the
    FILE, and a track's bar grid has no reason to be aligned to its first sample.
    """
    meas = song.get("measured", {})
    drop = float(meas.get("drop_ms", 0))
    db = meas.get("downbeat_ms")
    if db is None:
        return 0, True                                   # assumed
    return int(round((drop - float(db)) / beat_ms)) % 4, False


def train_from_cadence(root: Path, cad_id: str, song_id: str) -> Train:
    """The hook half only: a cadence is pacing and nothing else, so it has no carousel."""
    cad = rtf.load(root / "lib" / "cadences" / f"{cad_id}.cadence.yaml")
    song = rtf.load(root / "lib" / "songs" / f"{song_id}.song.yaml")
    beat_ms = 60000.0 / float(song["measured"]["bpm"])
    bar_pos, assumed = _bar_pos_of_drop(song, beat_ms)
    phase_note = ([] if not assumed else
                  ["bar phase ASSUMED (drop = downbeat): song has no measured.downbeat_ms"])

    ats = sorted({float(s["at"]) for s in cad.get("slots", [])}, reverse=True)
    notes = list(phase_note)
    if cad.get("requires", {}).get("single_take"):
        pre = [-ats[0], 0.0]          # the in-point and the drop; nothing between them
        notes.append("single_take: slot boundaries are move phases, not cuts — 0 internal cuts")
    else:
        pre = [-a for a in ats]
        notes.append("cuts inferred: every slot boundary (no `transition:` field in the schema)")
    return Train(cad_id, "cadence", pre, [0.0], beat_ms, bar_pos, notes)


def train_from_edit(path: Path) -> Train:
    """A whole promise-payoff video: cadence hook + beat-locked carousel."""
    ed, _ = rtf.resolve(path)
    inst = rtf.load(path)
    root = Path(ed["_root"])
    song = rtf.load(root / "lib" / "songs" / f"{inst['song']}.song.yaml")
    beat_ms = 60000.0 / float(song["measured"]["bpm"])
    bar_pos, assumed = _bar_pos_of_drop(song, beat_ms)
    phase_note = ([] if not assumed else
                  ["bar phase ASSUMED (drop = downbeat): song has no measured.downbeat_ms"])
    notes = list(phase_note)

    # --- pre: the cadence grid if there is one, else the clip's own declared beats -----
    cad_id = inst.get("cadence")
    cad = rtf.load(root / "lib" / "cadences" / f"{cad_id}.cadence.yaml") if cad_id else None
    if cad and cad.get("requires", {}).get("single_take"):
        pre = [-float(cad["hook_beats"]), 0.0]
        notes.append("single_take cadence: 0 internal cuts")
    elif cad:
        pre = [-float(s["at"]) for s in cad["slots"]]
        notes.append(f"pre-drop from cadence {cad_id} (planned, not as filmed)")
    else:
        pre = [0.0]

    # --- post: the carousel, from the items' own beat counts ---------------------------
    car = ed["timeline"][-1]
    post, t = [], 0.0
    for it in car.get("items", []):
        post.append(t)
        t += float(it.get("beats", 1))
    post.append(t)
    return Train(inst["id"], "video", pre, post, beat_ms, bar_pos, notes)


def train_from_plan(path: Path) -> Train:
    """A meme-engine render. No cadence and no hook — a build section and a post section,
    both cut by the solver rather than by a person."""
    plan = yaml.safe_load(path.read_text())
    root = rtf.find_root(path.parent)
    song = rtf.load(root / "lib" / "songs" / f"{plan['song']}.song.yaml")
    beat_ms = 60000.0 / float(song["measured"]["bpm"])
    bar_pos, assumed = _bar_pos_of_drop(song, beat_ms)
    phase_note = ([] if not assumed else
                  ["bar phase ASSUMED (drop = downbeat): song has no measured.downbeat_ms"])
    fps, drop_f = float(plan["fps"]), float(plan["drop_frame"])
    beat_f = beat_ms / 1000.0 * fps

    pre, post, f = [], [], 0.0
    for s in plan["slots"]:
        b = (f - drop_f) / beat_f
        (pre if b < -1e-6 else post).append(round(b, 3))
        f += float(s["frames"])
    pre.append(0.0)
    post.append(round((f - drop_f) / beat_f, 3))
    return Train(path.stem.replace(".plan", ""), "memeplan", pre, post, beat_ms, bar_pos,
                 phase_note + ["cuts are literal: every slot boundary in the plan is a hard cut"])


# ---------------------------------------------------------------- reporting


def congruency(a: Train, b: Train, side: str = None) -> tuple:
    """How much of two videos' rhythm is literally the same.

    Jaccard over the sets of beat positions carrying a cut. Chosen over a correlation of
    smoothed density curves for one reason: it has no window width and no kernel, so
    there is nothing to tune until it says what you wanted. Positions are rounded to the
    beat, because a viewer's ear quantises to the beat too.
    """
    def s(t):
        xs = {"pre": t.pre, "post": t.post}.get(side, t.onsets)
        return {round(x, 2) for x in xs}
    A, B = s(a), s(b)
    if not A and not B:
        return 1.0, 0, 0
    return len(A & B) / len(A | B), len(A & B), len(A | B)


def report(t: Train) -> None:
    print(f"\n{'=' * 78}\n{t.name}  [{t.kind}]   beat {t.beat_ms:.0f}ms   "
          f"drop on beat {t.drop_bar_pos + 1} of 4")
    for n in t.notes:
        print(f"  note: {n}")

    for side, label in (("pre", "hook / build "), ("post", "carousel    ")):
        g = t.gaps(side)
        if not g:
            print(f"  {label}  (no cuts)")
            continue
        bars = t.bar_positions(side)
        onbar = sum(1 for b in bars if b == 1) / len(bars)
        print(f"  {label}  {len(g):>2} cuts over {t.span(side):>5.1f} beats   "
              f"density {t.density(side):.3f} cuts/beat   CV {t.regularity(side):.2f}   "
              f"on the bar line {onbar:.0%}")
        print(f"                gaps (beats): {', '.join(f'{x:g}' for x in g)}")
        print(f"                bar position: {', '.join(str(b) for b in bars)}")

    s = t.seam()
    if s is not None:
        pre_d, post_d = t.density("pre"), t.density("post")
        lift = (post_d / pre_d) if pre_d else float("inf")
        print(f"  SEAM          last gap / first gap = "
              f"{'∞ (single take)' if s == float('inf') else f'{s:.2f}×'}"
              f"     density lift across the drop = "
              f"{'∞' if lift == float('inf') else f'{lift:.2f}×'}")


def matrix(ts: list, side: str, title: str) -> None:
    print(f"\n{title}")
    w = max(len(t.name) for t in ts) + 2
    print(" " * w + "".join(f"{t.name[:9]:>11}" for t in ts))
    for a in ts:
        row = "".join(f"{congruency(a, b, side)[0]:>11.2f}" for b in ts)
        print(f"{a.name:<{w}}{row}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", help="video ids, edit.yaml paths, plan paths, "
                                               "or cadence ids")
    ap.add_argument("--all", action="store_true", help="every cadence, video and plan found")
    ap.add_argument("--song", default="losing-sleep", help="song to hang bare cadences on")
    a = ap.parse_args()

    root = rtf.find_root(Path.cwd())
    ts = []

    if a.all:
        for c in sorted((root / "lib" / "cadences").glob("*.cadence.yaml")):
            ts.append(train_from_cadence(root, c.name.split(".")[0], a.song))
        for e in sorted((root / "videos").glob("*/edit.yaml")):
            ts.append(train_from_edit(e))
        for p in sorted((root / "videos").glob("*/*.plan.yaml")):
            ts.append(train_from_plan(p))
    for arg in a.targets:
        p = Path(arg)
        if p.is_file() and p.name.endswith(".plan.yaml"):
            ts.append(train_from_plan(p))
        elif (root / "lib" / "cadences" / f"{arg}.cadence.yaml").exists():
            ts.append(train_from_cadence(root, arg, a.song))
        else:
            ts.append(train_from_edit(rtf.find_edit(arg)))

    if not ts:
        ap.error("nothing to measure — pass ids or --all")

    for t in ts:
        report(t)

    if len(ts) > 1:
        print(f"\n{'=' * 78}\nRHYTHMIC CONGRUENCY — Jaccard over cut positions, "
              f"quantised to the beat")
        matrix(ts, "pre", "before the drop (the cadence's own rhythm):")
        matrix(ts, "post", "after the drop (the carousel pulse):")
        matrix(ts, None, "whole video:")


if __name__ == "__main__":
    main()
