#!/usr/bin/env python3
"""Fit every scene to what was actually said, and burn the result onto the take.

    python3 compose.py                    # full build -> out/edit/spindle-cut.mp4
    python3 compose.py --preview 30 45    # just that slice, for looking at
    python3 compose.py --skip-frames      # re-encode from frames already rendered

Depends on out/edit/align.json, which `align.py` writes.

## The idea

Scenes are pure functions of time — `window.seek(t)` puts a scene wherever it
belongs at t. That property was originally about making renders reproducible, and
it turns out to buy something better: a scene can be rendered at ANY duration
without re-authoring it, by sampling its clock faster or slower.

So nothing here trims or loops. Each scene is *stretched or squeezed* onto the
window its own paragraphs occupy in the take. Scene 01 was authored at 26s and the
words it belongs to run 24.1s, so it is sampled at 0.93x and every beat inside it
still lands in the same relative place.

A trim would have thrown away the end of the scene, which is where the settled
state is. A loop would have restarted it mid-sentence. Rescaling keeps the whole
arc and just changes how fast it plays.

## One frame sequence, one overlay pass

The obvious build is thirteen overlay filters chained in ffmpeg. That decodes
thirteen ProRes 4444 streams at once for a 2:41 output and spends most of its time
on inputs that are transparent.

Instead every scene renders into ONE master frame sequence covering the whole
take, with fully transparent frames in the gaps between paragraphs. A transparent
1920x1080 PNG is about a kilobyte, so the gaps cost nothing, and the composite
becomes a single overlay of one image sequence onto one video.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screen_cues                                          # noqa: E402

HERE = Path(__file__).resolve().parent
SCENES = HERE / "scenes"
EDIT = HERE / "out" / "edit"
BASE = HERE.parent / "BaseLayer.mov"
FPS = 24                      # the take is 24fps; matching it avoids a resample
W, H = 1920, 1080

# Which paragraphs each scene is cut against. The paragraph indices are the ones
# align.py prints, which are the blank-line-separated blocks of the voiceover.
#
# ## What is cut, and what came back
#
# The brief was "don't always have overlay — only when necessary to showcase
# something". A first pass took that to mean dropping the four scenes the pipeline
# README ranks lowest, which left four windows of unobstructed presenter. Three of
# those cuts were wrong and are reversed: **02-spam, 05-pgvector and 09-token are
# back**. They are the scenes with the best sound cues on them — the pgvector check
# lands on its own effect — and losing them cost more than the bare frames gained.
#
# What stays cut is the second half of 12-replay, which leaves **137.7-144.4** as the
# one window with nothing drawn over him. That is the right one to keep: "No audit
# table. No event log. No versioned copy of anything. Postgres cannot do that at any
# price" is the hardest claim in the script, and it is said to camera with the frame
# otherwise empty.
#
# None of the three restored scenes has a panel beside it, so all three play full
# frame at their authored size, with their labels and their effects intact.
#
# **04-tenant was cut for a different reason.** Its whole content is that a column
# name comes first, and `capture_sql.py`'s `prefix` proof prints
#
#     seq_in_index | column_name
#     -------------+------------
#                1 | tenant_id
#
# straight off the cluster. Where the database states the claim in its own words, an
# animation restating it is the weaker of the two and both at once is noise.
PLAN: list[tuple[str, list[int]]] = [
    ("01-disappear", [0, 1]),
    ("02-spam",      [3]),
    ("05-pgvector",  [6]),
    ("03-subspace",  [4]),
    ("06-filling",   [7]),
    ("07-fleet",     [8, 9]),
    ("08-lease",     [10]),
    ("09-token",     [11]),
    ("10-residency", [12, 13]),
    ("11-money",     [14]),
    ("12-replay",    [15, 16, 17]),
    # Paragraph 21 — the URL, the last thing said — is deliberately NOT in this
    # window. 13-close settles into a state made only of type, and type is faded out
    # in the left column, so carrying it to the end of the film put an EMPTY bordered
    # box in the left third under the closing line. Ending at paragraph 20 leaves the
    # last six seconds to him and the live site, which is the better close anyway.
    ("13-close",     [20]),
]

# ---------------------------------------------------------------------------
# Where a scene sits, which is not a property of the scene
#
# He is centred in this take. Frames sampled at 8s, 22s, 45s, 90s and 140s put his
# head and shoulders inside x 620-1250 at the tightest framing, so the composition is
# three columns: animation left of 660, him in the middle, evidence right of 1260.
#
# A scene is full-frame by default and moves into the left column exactly when a
# screen panel is up — never on a schedule of its own. `screen_cues.py` is the single
# list both halves read, because the one arrangement that ruins the frame is a panel
# landing on top of a full-frame diagram, and that is invisible until a render ends.
# ---------------------------------------------------------------------------
# A scene shrunk into a corner and left to fend for itself disappears — the first
# preview of this put 03-subspace over a patterned wall at a third of size and it
# read as three stray purple marks. Two things fix it, and both are needed:
#
#   * a BED. The same dark rounded plate the evidence panel sits on, mirrored on the
#     left, so the frame is two instruments flanking a person rather than one panel
#     and some debris. It also gives the figure a ground with known contrast, which
#     is the thing scene.css says it can never assume.
#   * bigger DOTS. The groove field draws at r=3.4 for a 1920-wide canvas; scaled
#     with the scene it lands near one pixel and vanishes under h.264. Strokes are
#     already immune (`vector-effect: non-scaling-stroke`), the dots are not, so
#     they are grown back by hand.
LEFT_CX, LEFT_CY = 350, 465   # centre of the animation column
LEFT_SCALE = 0.42
BED = (420, 420)              # w, h of the plate behind it
DOT_BOOST = 2.2               # what a dot's radius is multiplied by at full left
RAMP = 0.55                   # seconds a scene takes to move aside, and to come back

# A graphic that snaps on exactly as the first word lands reads as a mistake. It
# comes up just under the breath before, and holds just past the last word.
LEAD, TAIL = 0.35, 0.45
FADE = 0.30                   # seconds of alpha ramp at each edge


def left_amount(t: float) -> float:
    """0 = full frame, 1 = parked in the left column, smoothed at both edges.

    A hard switch reads as a glitch, and a scene that slides aside while the panel
    is still fading in reads as the panel having pushed it — which is exactly the
    causality the frame wants a viewer to infer.
    """
    best = 0.0
    for s, e, *_ in screen_cues.CUES:
        a, b = s - screen_cues.FADE, e + screen_cues.FADE
        if t < a - RAMP or t > b + RAMP:
            continue
        if t < a:
            v = (t - (a - RAMP)) / RAMP
        elif t > b:
            v = 1.0 - (t - b) / RAMP
        else:
            v = 1.0
        best = max(best, min(1.0, max(0.0, v)))
    # smoothstep, so it eases out of and into rest rather than arriving at speed
    return best * best * (3 - 2 * best)


def place(u: float) -> tuple[float, float, float]:
    """The SVG transform for a scene that is `u` of the way into the left column."""
    s = 1.0 + (LEFT_SCALE - 1.0) * u
    cx = 960 + (LEFT_CX - 960) * u
    cy = 540 + (LEFT_CY - 540) * u
    return cx - 960 * s, cy - 540 * s, s


# Injected into the scene page rather than added to nine scene files: placement is a
# property of the edit, not of any scene, and a scene should stay renderable on its
# own at full frame.
PLACE_JS = """([a, tx, ty, sc, lu, cx, cy, bw, bh, boost]) => {
    const svg = document.getElementById('svg');
    const st  = document.getElementById('stage');
    st.style.opacity = a;
    st.setAttribute('transform', `translate(${tx} ${ty}) scale(${sc})`);

    // the bed, behind everything, created once
    let bed = document.getElementById('leftbed');
    if (!bed) {
        bed = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        bed.setAttribute('id', 'leftbed');
        bed.setAttribute('rx', 14);
        bed.setAttribute('fill', '#0b0a09');
        bed.setAttribute('stroke', '#d97f4a');
        bed.setAttribute('stroke-width', 3);
        svg.insertBefore(bed, st);
    }
    bed.setAttribute('x', cx - bw / 2);
    bed.setAttribute('y', cy - bh / 2);
    bed.setAttribute('width', bw);
    bed.setAttribute('height', bh);
    // Never fully opaque: the wall behind stays faintly readable, which keeps the
    // plate looking like it is in the room instead of pasted over it.
    bed.setAttribute('opacity', (lu * 0.88).toFixed(4));

    // At a third of full size a 26px label renders around 8px and is illegible, so
    // the words go rather than shrink — the panel on the right and the burned
    // subtitles are both carrying text here, and a third illegible layer of it is
    // only noise. The figure still reads.
    const o = (1 - lu).toFixed(4);
    for (const n of document.querySelectorAll('.lbl')) n.style.opacity = o;

    for (const n of document.querySelectorAll('.dot')) {
        if (!n.dataset.r0) n.dataset.r0 = n.getAttribute('r') || '3.4';
        n.style.r = (parseFloat(n.dataset.r0) * (1 + (boost - 1) * lu)).toFixed(2) + 'px';
    }
}"""


def place_args(a: float, t: float) -> list:
    lu = left_amount(t)
    tx, ty, sc = place(lu)
    return [round(a, 4), round(tx, 2), round(ty, 2), round(sc, 4), round(lu, 4),
            LEFT_CX, LEFT_CY, BED[0], BED[1], DOT_BOOST]


def write_screen_inputs() -> None:
    """Hand the browser the cue sheet and the captured proofs.

    Both are emitted as classic scripts rather than fetched: a page opened from
    `file://` cannot XHR a sibling file, and finding that out costs a confusing
    half hour every time.
    """
    d = HERE / "out" / "screen"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cues.js").write_text(
        "window.CUES = " + json.dumps(
            {"fade": screen_cues.FADE, "cues": screen_cues.CUES,
             "plates": screen_cues.PLATES}, indent=1) + ";\n")
    src = HERE / "screen" / "proofs.json"
    proofs = json.loads(src.read_text()) if src.exists() else {"proofs": []}
    (d / "proofs.js").write_text(
        "window.PROOFS = " + json.dumps(proofs, indent=1) + ";\n")

    have = {p["slug"] for p in proofs["proofs"]}
    want = set(screen_cues.slugs())
    if want - have:
        print(f"  NOTE: cue sheet wants proofs not yet captured: "
              f"{', '.join(sorted(want - have))} — run capture_sql.py",
              file=sys.stderr)


def probe_duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def build_frames(align: list, total_frames: int) -> Path:
    frames = EDIT / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-lcd-text"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # one transparent plate, copied into every gap
        blank = EDIT / "blank.png"
        pg.goto("about:blank")
        pg.screenshot(path=str(blank), omit_background=True)

        written: set[int] = set()
        for stem, paras in PLAN:
            rows = [align[i] for i in paras if align[i]]
            if not rows:
                print(f"  {stem}: no aligned paragraphs, skipped", file=sys.stderr)
                continue
            start = max(0.0, min(r["start"] for r in rows) - LEAD)
            end = max(r["end"] for r in rows) + TAIL
            span = end - start

            scene = SCENES / f"{stem}.html"
            pg.goto(scene.as_uri())
            pg.wait_for_function("window.SCENE_READY === true", timeout=15000)
            authored = float(pg.evaluate("window.SCENE_DURATION"))

            f0 = int(round(start * FPS))
            n = int(round(span * FPS))
            stage = pg.query_selector("#stage")

            for k in range(n):
                u = k / max(1, n - 1)              # 0..1 through the window
                pg.evaluate("(t) => window.seek(t)", u * authored)
                # edge fade, applied to the whole stage so no scene needs to know
                sec = k / FPS
                a = min(1.0, sec / FADE, max(0.0, (span - sec)) / FADE)
                idx = f0 + k

                # Placement is on the TAKE's clock, not the scene's — a scene is
                # stretched onto its paragraphs, but it has to move aside at the
                # instant the panel actually appears.
                pg.evaluate(PLACE_JS, place_args(a, idx / FPS))

                if idx >= total_frames:
                    break
                pg.screenshot(path=str(frames / f"{idx:05d}.png"), omit_background=True)
                written.add(idx)

            print(f"  {stem:14} {start:7.2f} → {end:7.2f}  "
                  f"{span:5.2f}s  authored {authored:4.1f}s  x{span / authored:.3f}")

        b.close()
        if errs:
            print("PAGE ERRORS:", *errs, sep="\n  ", file=sys.stderr)
            raise SystemExit(1)

    # fill the gaps
    gaps = 0
    for i in range(total_frames):
        if i not in written:
            shutil.copyfile(blank, frames / f"{i:05d}.png")
            gaps += 1
    print(f"  {len(written)} scene frames, {gaps} transparent, {total_frames} total")
    return frames


def build_app_layer(total_frames: int) -> Path | None:
    """The evidence, in the right third, as its own frame sequence.

    Unlike every other scene this one is NOT fitted to a paragraph — it owns the
    whole timeline and its cue times are absolute, because a result wants to appear
    at the moment the words and the picture agree, which is not always a paragraph
    edge.

    Kept as a separate sequence rather than drawn into the graphics frames so the
    two can be re-rendered independently. Re-typesetting a terminal is cheap;
    re-rendering nine scenes is not, and during an edit the panel is the half that
    changes.
    """
    write_screen_inputs()
    scene = SCENES / "screen-layer.html"
    if not scene.exists():
        return None
    frames = EDIT / "appframes"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-lcd-text"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(scene.as_uri())
        pg.wait_for_function("window.SCENE_READY === true", timeout=20000)

        # One transparent plate, reused for every frame with no panel up. The panels
        # occupy about a third of the running time, so screenshotting the other two
        # thirds would be several thousand identical empty PNGs and most of the
        # build's wall clock.
        blank = EDIT / "blank.png"
        if not blank.exists():
            pg2 = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            pg2.goto("about:blank")
            pg2.screenshot(path=str(blank), omit_background=True)
            pg2.close()

        drawn = 0
        for i in range(total_frames):
            t = i / FPS
            p = frames / f"{i:05d}.png"
            if not screen_cues.active(t):
                shutil.copyfile(blank, p)
                continue
            pg.evaluate("(t) => window.seek(t)", t)
            pg.screenshot(path=str(p), omit_background=True)
            drawn += 1
        b.close()
        if errs:
            print("SCREEN LAYER ERRORS:", *errs, sep="\n  ", file=sys.stderr)
            raise SystemExit(1)
    print(f"  screen layer: {drawn} drawn, {total_frames - drawn} transparent")
    return frames


# ---------------------------------------------------------------------------
# grade and audio
#
# Both are applied HERE, in the same pass that lays on the graphics, straight
# from BaseLayer.mov. Nothing in this file ever reads its own output. Grading a
# finished h264 and re-encoding it would put two generations of the same lossy
# codec on the picture for no reason.
# ---------------------------------------------------------------------------

# Measured on the source with signalstats, averaged over every 60th frame:
#   Y 102.6 (dim) · U 118.7 · V 135.7 (both off neutral the same way — a warm
#   orange cast) · SAT 14.5 (flat) · YLOW 37.3 / YHIGH 172.4 (low contrast)
#
# This lands it at Y 106 · U 123 · V 132 · SAT 16.2. Deliberately NOT neutral:
# it is a warm interior with a wood door in shot, and driving U/V to 128 makes a
# real room look like a rendering. The cast is corrected about two thirds.
GRADE = (
    "colorbalance=rm=-0.075:bm=0.095:rh=-0.045:bh=0.060:rs=-0.035:bs=0.055,"
    "eq=contrast=1.09:brightness=0.022:saturation=1.10:gamma=1.03,"
    "vibrance=intensity=0.18"
)

# The source arrives at -2.76 LUFS with a +11.25 dBTP true peak — around 12 dB
# hotter than anything should be, and guaranteed to clip the moment a player
# converts it to 16-bit integer.
#
# The fix is one number. `Flat factor: 0.0` says the waveform is not actually
# flat-topped, so nothing was destroyed on the way in and no declipper is needed
# — it is simply too loud. -12.8 dB of pure gain puts it at -15.56 LUFS and
# -1.55 dBTP, both in spec, and a gain is arithmetic: it cannot degrade anything.
#
# What CANNOT be undone, and is worth knowing: LRA is 2.5, so the dynamics were
# already squashed before this file existed. Nothing here restores them.
GAIN_DB = -12.8
SFX_BED = HERE / "out" / "sfx" / "bed.wav"


MUSIC = Path.home() / "Music" / "Daniel Avery - Drone Logic [PHLP02].mp3"
MUSIC_IN = 221.0        # 3:41 — the section asked for
MUSIC_DB = -17.0        # before ducking. -21 read as too far back; the sidechain
                        # ducker is what protects the words, so the resting level
                        # can sit higher than a static mix would allow.

SUBS = EDIT / "subs.ass"


def audio_chain(with_sfx: bool, with_music: bool, first_input: int,
                tail: float = 0.0, end: float = 0.0) -> tuple[str, list[str]]:
    """The audio half of the filtergraph, plus any extra inputs it needs.

    `tail` is the silence appended to the voice so the mix runs as long as the
    picture does. Without it `amix=duration=first` ends the moment the take does and
    the end card plays in silence with the music cut off mid-phrase.
    """
    # 40Hz rather than 65: the effects carry real sub-bass now and a 65Hz corner
    # was eating the bottom of every thunk and drop along with the room rumble.
    voice = f"[0:a]volume={GAIN_DB}dB,highpass=f=40:p=2"
    if tail:
        voice += f",apad=pad_dur={tail}"
    voice += "[voice]"

    # The limiter goes AFTER the mix, not on the voice. Dialogue peaks at -1.7
    # and the bed at -6; summed, those can cross zero, and a limiter upstream of
    # the sum cannot see it. This is the only thing standing between a loud cue
    # landing on a loud consonant and a clipped master.
    guard = "alimiter=limit=0.841:level=disabled"     # -1.5 dBFS, so true peak lands near -1.2

    use_sfx = with_sfx and SFX_BED.exists()
    use_music = with_music and MUSIC.exists()
    if not use_sfx and not use_music:
        return f"{voice};[voice]{guard}[a]", []

    parts, mix, extra = [], [], []
    # 0 = take, 1 = graphics frames, 2 = app layer when present
    idx = first_input

    if use_music:
        # The voice is split so one copy can key the ducker. Music is broadband —
        # unlike the effects it DOES overlap speech — so spectral separation is
        # not available and the level has to be managed dynamically instead.
        parts.append("[voice]asplit=2[voicemix][key]")
        # A dip in the presence band as well as the gain: taking 1.9kHz down by
        # 4 dB clears the consonants that decide whether a word is legible, and
        # costs the music almost nothing anyone notices.
        # The fade-out is pinned to the END OF THE FILM, not to a number typed once.
        # It used to be `st=153:d=6`, which was the end of the take; with the card
        # appended that fade landed eight seconds early and the card ran dry.
        fo = max(4.0, (end or 159.0) - 4.5)
        parts.append(
            f"[{idx}:a]volume={MUSIC_DB}dB,equalizer=f=1900:t=q:w=1.7:g=-4,"
            f"afade=t=in:st=0:d=4,afade=t=out:st={fo:.2f}:d=4.5[musicraw]")
        # Ducking is what buys "prioritise legibility of words". 6:1 above a low
        # threshold with a slow release so it breathes back between sentences
        # rather than pumping on every syllable.
        parts.append("[musicraw][key]sidechaincompress=threshold=0.018:ratio=6:"
                     "attack=18:release=420:makeup=1[music]")
        mix.append("[voicemix]")
        mix.append("[music]")
        extra += ["-ss", str(MUSIC_IN), "-i", str(MUSIC)]
        idx += 1
    else:
        mix.append("[voice]")

    if use_sfx:
        parts.append(f"[{idx}:a]anull[sfx]")
        mix.append("[sfx]")
        extra += ["-i", str(SFX_BED)]

    # normalize=0 matters: amix otherwise divides every input by the input count,
    # which would quietly drop the dialogue to make room for everything else.
    parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:"
                 f"dropout_transition=0:normalize=0,{guard}[a]")
    return voice + ";" + ";".join(parts), extra


def encode(frames: Path, out: Path, clip: tuple[float, float] | None,
           grade: bool = True, sfx: bool = True, master: bool = False,
           app: Path | None = None, music: bool = True, subs: bool = True,
           base_secs: float = 0.0) -> None:
    pre = ["-ss", str(clip[0]), "-to", str(clip[1])] if clip else []
    # A --preview slice keeps the take's own length; only a full render grows a tail.
    tail = 0.0 if clip else screen_cues.TITLE_SECS
    achain, extra = audio_chain(sfx, music, 3 if app else 2,
                                tail=tail, end=base_secs + tail)

    # 10-bit through the grade so the contrast curve has somewhere to put the
    # values it moves; an 8-bit path bands visibly in the wall behind him.
    #
    # `tpad` then adds black past the last frame of the take, which is the ground the
    # end card plays on. Appending it here rather than concatenating a second file
    # afterwards keeps the promise the rest of this pipeline makes: nothing ever reads
    # its own output, so the card costs no extra generation of h.264 on the picture.
    vhead = f"[0:v]format=yuv444p10le,{GRADE}" if grade else "[0:v]null"
    if not clip:
        vhead += f",tpad=stop_mode=add:stop_duration={screen_cues.TITLE_SECS}:color=black"
    vhead += "[g];"

    # The app layer sits ABOVE the graphics: a screenshot of the product is the
    # literal thing, and an abstract diagram drawn on top of it would be arguing
    # with its own evidence.
    if clip:
        shift = (f"[1:v]trim=start={clip[0]}:end={clip[1]},setpts=PTS-STARTPTS[ov];")
        appshift = (f"[2:v]trim=start={clip[0]}:end={clip[1]},setpts=PTS-STARTPTS[ap];"
                    if app else "")
    else:
        shift = "[1:v]null[ov];"
        appshift = "[2:v]null[ap];" if app else ""

    chain = vhead + shift + appshift
    chain += "[g][ov]overlay=0:0:format=auto:shortest=1"
    chain += "[gfx];[gfx][ap]overlay=0:0:format=auto:shortest=1" if app else ""
    chain += ",format=yuv420p"
    if subs and SUBS.exists():
        # Burned in rather than a soft track: the file gets uploaded to places
        # that will not carry a sidecar, and the stated priority is that the
        # words are legible without anyone switching anything on.
        esc = str(SUBS).replace("\\", "/").replace(":", "\\:")
        if clip:
            # `-ss` before `-i` resets PTS to zero, so the subtitle filter would
            # render the top of the file over the middle of the video — a preview
            # that lies about what the full render will do. Push the clock back to
            # where the clip really sits, burn, then pull it forward again.
            chain += (f",setpts=PTS+{clip[0]}/TB,subtitles='{esc}'"
                      f",setpts=PTS-{clip[0]}/TB")
        else:
            chain += f",subtitles='{esc}'"
    chain += "[v];"

    vcodec = (["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
               "-c:a", "pcm_s24le"]
              if master else
              ["-c:v", "libx264", "-preset", "slow", "-crf", "16",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
               "-movflags", "+faststart"])

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-stats",
        *pre, "-i", str(BASE),
        "-framerate", str(FPS), "-start_number", "0", "-i", str(frames / "%05d.png"),
    ]
    if app:
        cmd += ["-framerate", str(FPS), "-start_number", "0",
                "-i", str(app / "%05d.png")]
    cmd += [*extra, "-filter_complex", chain + achain,
            "-map", "[v]", "-map", "[a]", *vcodec, str(out)]

    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", nargs=2, type=float, metavar=("FROM", "TO"))
    ap.add_argument("--skip-frames", action="store_true")
    ap.add_argument("--no-grade", action="store_true", help="skip the colour work")
    ap.add_argument("--no-sfx", action="store_true", help="voice only")
    ap.add_argument("--master", action="store_true",
                    help="ProRes 422 HQ + 24-bit PCM instead of h264/aac")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-subs", action="store_true")
    # On by default now, and the reason is the whole point of this edit: the film
    # asserts a series of things about CockroachDB, and the panel is where it shows
    # them being true. A render without it is the old cut.
    ap.add_argument("--no-screen", action="store_true",
                    help="drop the evidence panel — animations and take only")
    ap.add_argument("--skip-app-frames", action="store_true")
    args = ap.parse_args()

    if not BASE.exists():
        print(f"no base layer at {BASE}", file=sys.stderr)
        return 1
    apath = EDIT / "align.json"
    if not apath.exists():
        print("run align.py first", file=sys.stderr)
        return 1

    align = json.loads(apath.read_text())
    dur = probe_duration(BASE)
    # The film runs past the take: `encode` appends black for the end card, and both
    # overlay sequences have to be long enough to cover it or ffmpeg's image2 demuxer
    # stops early and takes the card with it.
    total = int(round((dur + screen_cues.TITLE_SECS) * FPS))
    print(f"base {dur:.2f}s + {screen_cues.TITLE_SECS:.1f}s card · "
          f"{total} frames @ {FPS}fps")

    frames = EDIT / "frames"
    if not args.skip_frames:
        frames = build_frames(align, total)
    elif not frames.exists():
        print("--skip-frames given but out/edit/frames is missing", file=sys.stderr)
        return 1

    app = None
    if not args.no_screen:
        appdir = EDIT / "appframes"
        if args.skip_app_frames and appdir.exists():
            app = appdir
        else:
            app = build_app_layer(total)

    clip = tuple(args.preview) if args.preview else None
    ext = "mov" if args.master else "mp4"
    name = "preview" if clip else ("spindle-master" if args.master else "spindle-cut")
    out = EDIT / f"{name}.{ext}"
    encode(frames, out, clip,
           grade=not args.no_grade, sfx=not args.no_sfx, master=args.master,
           app=app, music=not args.no_music, subs=not args.no_subs,
           base_secs=dur)
    print(f"\n{out.relative_to(HERE)}  {out.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
