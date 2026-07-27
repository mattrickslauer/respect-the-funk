#!/usr/bin/env python3
"""Render an rtf.edit timeline to an mp4.

    python3 render_edit.py california-test.edit.yaml [--check]

Resolves the edit against its format and song (see rtf.py), resolves every clip against
its rtf.clip/v1 descriptor, validates the cuts it was asked to make, quantizes every
post-drop cut to the song's beat grid, and drives ffmpeg. Stills that don't exist yet
render as labelled placeholder cards, so an edit is watchable before a single image is
generated — the point is to see the timing, not the pictures.

Requires: ffmpeg on PATH, pyyaml.
"""

import argparse
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

import rtf

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
PLACEHOLDER_COLORS = ["0x1b4965", "0x5fa8d3", "0xf4a261", "0xe76f51", "0x2a9d8f", "0x264653"]
DEFAULT_HOLD_MS = 500  # fallback when the track's BPM is unknown


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def todo(v) -> bool:
    return v is None or v == "TODO"


def esc(s: str) -> str:
    """Escape text for ffmpeg drawtext."""
    return s.replace("\\", "").replace(":", r"\:").replace("'", "").replace("%", "")


# ---------------------------------------------------------------- validation


CLEARED_SOURCES = {"original", "ai", "stock_licensed", "public_domain"}


def validate_clip(desc: dict, path: Path) -> list[str]:
    """CLIP-SPEC rule 4: beats must tile the clip. Rights are reported, not enforced.

    This used to be a hard gate — anything outside CLEARED_SOURCES could not enter an
    edit at all. That is the right rule for a publish step and the wrong one for a build
    step: it makes a proof of concept off found footage impossible to even render, which
    is a decision about distribution being taken by a renderer. The source is still
    printed on every run, so nothing is hidden; deciding what may be *published* is
    `research/06`'s job and a human's.
    """
    errs = []
    src = desc.get("rights", {}).get("source")
    if src not in CLEARED_SOURCES:
        print(f"  ! {path.name}: rights.source is {src!r} — not cleared for publication")

    dur = desc["source_meta"]["duration_ms"]
    beats = desc.get("beats", [])
    if beats:
        if beats[0]["t_in"] != 0:
            errs.append(f"{path.name}: first beat starts at {beats[0]['t_in']}, not 0")
        if beats[-1]["t_out"] != dur:
            errs.append(f"{path.name}: last beat ends at {beats[-1]['t_out']}, duration is {dur}")
        for a, b in zip(beats, beats[1:]):
            if a["t_out"] != b["t_in"]:
                errs.append(f"{path.name}: gap/overlap at {a['t_out']} → {b['t_in']}")
    return errs


def validate_cut(desc: dict, t: int, path: Path) -> list[str]:
    """CLIP-SPEC rule 6: you may only cut where the descriptor says you may."""
    legal = {c["t"] for c in desc.get("cut_points", [])}
    if t not in legal:
        return [f"{path.name}: cut at {t}ms is not a declared cut_point {sorted(legal)}"]
    return []


# ---------------------------------------------------------------- beat grid


def beat_grid(items: list[dict], bpm, fps: int) -> tuple[list[int], bool]:
    """Frame count for each item, quantized to the beat.

    Positions are absolute on the grid and durations are their differences, so rounding
    error never accumulates: the 16th cut is still exactly on the beat.
    """
    locked = not todo(bpm)
    beat_ms = 60000.0 / float(bpm) if locked else DEFAULT_HOLD_MS

    pos_beats, acc = [0.0], 0.0
    for it in items:
        acc += float(it.get("beats", 1)) if locked else 1.0
        pos_beats.append(acc)

    frames = [round(b * beat_ms / 1000.0 * fps) for b in pos_beats]
    return [max(1, b - a) for a, b in zip(frames, frames[1:])], locked


# ---------------------------------------------------------------- filtergraph


# Graded looks, applied to stills after normalization. Kept as plain filter strings so
# a colourist can read and argue with them.
LOOKS = {
    "none": "",
    "sunbleached": ("eq=contrast=1.06:saturation=1.22:gamma=1.03,"
                    "colorbalance=rs=0.06:gs=0.01:bs=-0.07,vignette=PI/7"),
    "film": ("eq=contrast=1.12:saturation=0.92:gamma=0.98,"
             "noise=alls=7:allf=t+u,vignette=PI/4.5"),
    "vhs": ("chromashift=cbh=3:crh=-3,eq=contrast=1.1:saturation=1.35,"
            "noise=alls=12:allf=t,vignette=PI/4"),
}


def animate(kind: str, n: int, w: int, h: int, fps: int) -> str:
    """A zoompan move per still. `on` is the output frame index; n is this still's length.

    Every still getting the same slow push reads as a slideshow however well it cuts —
    the variety is what makes it a title sequence.
    """
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    moves = {
        "push_in":  (f"1+0.10*on/{n}", cx, cy),
        "pull_out": (f"1.10-0.10*on/{n}", cx, cy),
        "pan_r":    ("1.12", f"(iw-iw/zoom)*on/{n}", cy),
        "pan_l":    ("1.12", f"(iw-iw/zoom)*(1-on/{n})", cy),
        "tilt_d":   ("1.12", cx, f"(ih-ih/zoom)*on/{n}"),
        "tilt_u":   ("1.12", cx, f"(ih-ih/zoom)*(1-on/{n})"),
        # hits hard on the cut and settles inside the first third of the beat
        "snap":     (f"1.25-0.25*min(1,on/({n}*0.33))", cx, cy),
    }
    if kind not in moves:
        return ""
    z, x, y = moves[kind]
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps},"


def vnorm(w: int, h: int, fps: int) -> str:
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps={fps},setsar=1,format=yuv420p"
    )


def placeholder_src(text: str, idx: int, w: int, h: int, secs: float, label: str = "") -> str:
    color = PLACEHOLDER_COLORS[idx % len(PLACEHOLDER_COLORS)]
    wrapped = esc("\n".join(textwrap.wrap(text, 26)[:8]))
    return (
        f"color=c={color}:s={w}x{h}:r=30,"
        f"drawtext=fontfile={FONT}:text='{wrapped}':fontsize=52:fontcolor=white:"
        f"line_spacing=14:x=(w-text_w)/2:y=(h-text_h)/2,"
        f"drawtext=fontfile={FONT}:text='{esc(label or f'PLACEHOLDER {idx + 1}')}':fontsize=34:"
        f"fontcolor=white@0.5:x=(w-text_w)/2:y=h*0.86"
    )


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edit", help="video id, video dir, or path to edit.yaml")
    ap.add_argument("--check", action="store_true", help="validate only, render nothing")
    ap.add_argument("--bpm", type=float, help="preview at this BPM without editing the file")
    args = ap.parse_args()

    edit_path = rtf.find_edit(args.edit)
    base = edit_path.parent
    # An rtf.edit/v2 instance is compiled against its format + song first; a v1 flat edit
    # comes back untouched. Format invariants join this render's own error list, so one
    # --check reports both "you cut somewhere illegal" and "this isn't the format you
    # said it was".
    edit, format_errs = rtf.resolve(edit_path)

    out = edit["output"]
    W, H, FPS = out["width"], out["height"], out["fps"]
    out_path = (base / out["file"]).resolve()
    music = edit.get("music", {}) or {}
    if args.bpm:
        music["bpm"] = args.bpm

    errs: list[str] = list(format_errs)
    inputs: list[str] = []
    vfilters: list[str] = []
    vlabels: list[str] = []
    n_in = 0            # ffmpeg input index
    clip_ms = 0         # runtime before the drop
    seq_ms = 0.0        # runtime after it
    audio_src = None    # (input index, ms)
    speech_norm = ""    # loudnorm chain for the spoken line, if requested
    notes: list[str] = []

    for entry in edit["timeline"]:
        # ---- a real clip, cut against its descriptor -------------------------
        if "clip" in entry:
            cpath = (base / f"{entry['clip']}.clip.yaml").resolve()
            if not cpath.exists():
                errs.append(f"missing descriptor: {cpath}")
                continue
            desc = load(cpath)
            errs += validate_clip(desc, cpath)
            errs += validate_cut(desc, entry["t_in"], cpath)
            errs += validate_cut(desc, entry["t_out"], cpath)

            media = cpath.parent / desc["media"]
            t_in, t_out = entry["t_in"], entry["t_out"]
            secs = (t_out - t_in) / 1000

            if media.exists():
                inputs += ["-ss", f"{t_in / 1000}", "-t", f"{secs}", "-i", str(media)]
                if entry.get("audio") == "source":
                    audio_src = (n_in, t_out - t_in)
                    lufs = entry.get("speech_lufs")
                    speech_norm = f"loudnorm=I={lufs}:TP=-1.5:LRA=11," if lufs else ""
            else:
                # Same doctrine as a missing still: an edit is watchable before its media
                # exists. A hook that has not been shot or generated yet renders as a card
                # carrying its own line, so you can still watch the drop land on the cut —
                # which is the only thing worth checking before the media is real.
                card = (desc.get("audio", {}).get("quote")
                        or desc.get("logline") or entry["clip"])
                inputs += ["-f", "lavfi", "-t", f"{secs}",
                           "-i", placeholder_src(card, 0, W, H, secs,
                                                 label=f"HOOK NOT YET GENERATED — {desc['media']}")]
                notes.append(f"! {desc['media']} is missing — hook is a placeholder card "
                             f"and the spoken line is absent")
            vfilters.append(f"[{n_in}:v]{vnorm(W, H, FPS)}[v{n_in}]")
            vlabels.append(f"[v{n_in}]")
            clip_ms += t_out - t_in
            n_in += 1

        # ---- the beat-locked still sequence ----------------------------------
        elif "sequence" in entry:
            items = entry["items"]
            durs, locked = beat_grid(items, music.get("bpm"), FPS)
            if not locked:
                notes.append(
                    f"! music.bpm is TODO — stills held at a flat {DEFAULT_HOLD_MS}ms. "
                    "Cuts will NOT be on the beat."
                )
            flash = int(entry.get("flash_frames", 0))
            moves = entry.get("animate") or []
            look_name = entry.get("look", "none")
            if look_name not in LOOKS:
                errs.append(f"unknown look {look_name!r} — have {sorted(LOOKS)}")
            look = LOOKS.get(look_name, "")
            total = 0

            for n, (item, nframes) in enumerate(zip(items, durs)):
                secs = nframes / FPS
                total += nframes
                still = (base / entry["source"] / f"{item['still']}.png").resolve()
                prompt = edit["still_prompts"]["items"].get(item.get("prompt_ref"), item["still"])

                if still.exists():
                    # -framerate matters: image2 defaults to 25fps and the segment would
                    # come out the wrong length on a 30fps timeline.
                    inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{secs}",
                               "-i", str(still)]
                else:
                    inputs += ["-f", "lavfi", "-t", f"{secs}",
                               "-i", placeholder_src(prompt, n, W, H, secs)]

                chain = []
                # d=1: one output frame per input frame. d=nframes would emit nframes
                # frames *per input frame* — that is the 62-second-render bug.
                if moves:
                    mv = animate(moves[n % len(moves)], nframes, W, H, FPS)
                    if mv:
                        chain.append(mv.rstrip(","))
                elif entry.get("ken_burns"):
                    chain.append(
                        f"zoompan=z='min(zoom+0.0015,1.12)':d=1:"
                        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}"
                    )
                chain.append(vnorm(W, H, FPS))
                if look:
                    chain.append(look)
                if flash:                       # white snap on the cut
                    chain.append(f"fade=t=in:st=0:d={flash / FPS}:color=white")
                if item.get("title"):
                    chain.append(
                        f"drawtext=fontfile={FONT}:text='{esc(item['title'])}':fontsize=96:"
                        f"fontcolor=white:borderw=4:bordercolor=black@0.6:"
                        f"x=(w-text_w)/2:y=h*0.42"
                    )
                vfilters.append(f"[{n_in}:v]{','.join(chain)}[v{n_in}]")
                vlabels.append(f"[v{n_in}]")
                n_in += 1

            seq_ms += total / FPS * 1000
            notes.append(
                f"  sequence '{entry['sequence']}': {len(items)} cuts / {total} frames "
                f"({total / FPS:.2f}s), {'beat-locked' if locked else 'UNLOCKED'}"
            )

    if errs:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errs:
            print("  •", e, file=sys.stderr)
        return 1
    print(f"✓ validated — {len(vlabels)} segments, drop cut at {clip_ms}ms")

    if cad := edit.get("cadence"):
        # The hook's pacing, on the same grid as the carousel but counted backward from
        # the cut. `drift` is how far the clip's own beat boundaries sit from it — small
        # numbers mean the picture was already cutting on the music.
        print(f"  cadence '{cad['id']}' — {cad['hook_beats']} beats "
              f"@ {cad['beat_ms']:.0f}ms, backward from the cut")
        hook_desc = load((base / f"{edit['timeline'][0]['clip']}.clip.yaml").resolve())
        drift = {t: d for t, _, d, _ in rtf.cadence_drift(hook_desc, cad["grid"], cad["beat_ms"])}
        for g in cad["grid"]:
            near = min(drift, key=lambda t: abs(t - g["t_in"])) if drift else None
            off = f"{drift[near]:+5.0f}ms" if near is not None and abs(near - g["t_in"]) < 400 else "    —"
            print(f"    beat {g['at']:>4.0f}  {g['t_in']:>5}–{g['t_out']:<5}ms  "
                  f"{g['role']:<10} clip is {off}")

    for n in notes:
        print(n)
    shown = len(notes)          # audio notes are appended below; print them after
    if args.check:
        return 0

    fc = ";".join(vfilters)
    fc += f";{''.join(vlabels)}concat=n={len(vlabels)}:v=1:a=0[vout]"

    maps = ["-map", "[vout]"]
    # Music can carry the timeline on its own. When the hook is a placeholder there is no
    # line to mix, but the drop still has to land exactly on the cut — and that is the
    # whole reason to render before the media exists.
    if not todo(music.get("file")) and not todo(music.get("drop_ms")):
        mpath = (base / music["file"]).resolve()
        gain = float(music.get("pre_drop_gain", 0.0))
        # atrim, not -ss. Input seeking on an mp3 lands on a frame boundary (~26ms), which
        # put the drop 45ms ahead of the cut. atrim is sample-accurate; decoding the head
        # of the file to get there costs nothing.
        inputs += ["-i", str(mpath)]
        pre = music.get("pre_roll")

        if pre and not todo(music.get("bpm")):
            # Bring in the track's real run-up N bars early and swell it into the drop.
            # The pre-roll is the music that actually precedes the drop, not a fade of the
            # drop itself — so the crescendo lands on the downbeat because the arrangement
            # already does.
            bars = float(pre.get("bars", 2))
            beat = 60000.0 / float(music["bpm"])
            pre_ms = int(round(bars * 4 * beat))
            if pre_ms > clip_ms:
                notes.append(f"! pre_roll {pre_ms}ms is longer than the {clip_ms}ms cold "
                             f"open — clamped")
                pre_ms = clip_ms
            start_s = (clip_ms - pre_ms) / 1000          # when the swell begins
            drop_s = clip_ms / 1000
            g0 = float(pre.get("from_gain", 0.0))
            g1 = float(pre.get("to_gain", 1.0))
            p = {"linear": 1, "quadratic": 2, "cubic": 3}.get(pre.get("curve", "quadratic"), 2)
            delay = (f"atrim=start={(music['drop_ms'] - pre_ms) / 1000},asetpts=N/SR/TB,"
                     f"adelay={clip_ms - pre_ms}|{clip_ms - pre_ms},")
            # power curve: a linear ramp reads as already-loud halfway through, because
            # loudness is not linear in amplitude
            duck = (f"volume='if(lt(t,{start_s}),{g0},"
                    f"if(lt(t,{drop_s}),{g0}+({g1}-{g0})*pow((t-{start_s})/{pre_ms / 1000},{p}),"
                    f"1))':eval=frame,")
            notes.append(f"  crescendo: {bars:g} bars / {pre_ms}ms, {start_s:.3f}s → "
                         f"{drop_s:.3f}s, {pre.get('curve', 'quadratic')} {g0:g}→{g1:g}")
        elif gain == 0.0:
            # Silence before the drop by construction: start the track AT the drop and
            # delay it onto the cut, so no pre-drop audio enters the graph at all. A gain
            # envelope would leave the run-up sitting there at volume 0 — same result
            # when nothing goes wrong, but this cannot leak.
            delay = (f"atrim=start={music['drop_ms'] / 1000},asetpts=N/SR/TB,"
                     f"adelay={clip_ms}|{clip_ms},")
            duck = ""
        else:
            offset = music["drop_ms"] - clip_ms      # where in the track to start
            delay = (f"atrim=start={offset / 1000},asetpts=N/SR/TB," if offset >= 0
                     else f"adelay={-offset}|{-offset},")
            duck = f"volume='if(lt(t,{clip_ms / 1000}),{gain},1)':eval=frame,"
        # apad makes the source-audio branch infinite, so amix's `shortest` never fires —
        # the mix has to be trimmed to the timeline explicitly.
        total_s = (clip_ms + seq_ms) / 1000
        fade = max(0.0, total_s - 0.35)
        fc += f";[{n_in}:a]{delay}{duck}aresample=async=1[m]"
        if audio_src is not None:
            # normalize=0: amix otherwise divides every input by the input count, which
            # halves the spoken line for no reason. The line also needs bringing up to a
            # broadcast-ish target — phone audio at -34 LUFS is inaudible next to a
            # mastered track.
            fc += f";[{audio_src[0]}:a]{speech_norm}apad[s]"
            head = "[s][m]amix=inputs=2:duration=first:normalize=0,"
        else:
            head = "[m]"
            notes.append("  music only — no source audio in the timeline")
        fc += f";{head}atrim=0:{total_s},asetpts=N/SR/TB,afade=t=out:st={fade}:d=0.35[aout]"
        maps += ["-map", "[aout]"]
    elif audio_src is not None:
        fc += f";[{audio_src[0]}:a]apad[aout]"
        maps += ["-map", "[aout]", "-shortest"]
        print("! no music track yet — source audio, then silence after the cut")

    cmd = ["ffmpeg", "-y", "-v", "error", "-stats", *inputs, "-filter_complex", fc, *maps,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_path)]
    for n in notes[shown:]:
        print(n)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print("→", out_path)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
