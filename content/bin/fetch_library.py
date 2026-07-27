#!/usr/bin/env python3
"""Build a starting library: brief -> YouTube -> normalised vertical shots + sidecars.

    .venv/bin/python bin/fetch_library.py nocturnal

A brief (`lib/briefs/<id>.brief.yaml`) names what each picture has to *do* and a query
that tends to find it. This resolves the query, downloads a bounded window of the source,
and then does the part that actually matters:

**It picks one continuous shot.** A downloaded 150s window is not a clip — it is a reel
containing several. Cutting a 480ms beat out of the middle of somebody else's cut is the
one thing that reads as broken no matter how good the grade is. So the motion curve is
segmented at its own discontinuities, and the chosen window never crosses one.

**It reframes on the motion, not on the centre.** A 16:9 source centre-cropped to 9:16
throws away 66% of the width, and the subject is very often not in the third that
survives. The crop x is the centroid of column-wise motion energy over the chosen window,
which costs nothing because the frames are already decoded.

Everything mechanical is written to a `rtf.clip/v1` sidecar — duration, palette, motion,
and the provenance of where it came from. Nothing here gates on `rights.source`; it
records it. The gate is a publishing decision, not a download decision.

Requires: ffmpeg, ffprobe, numpy, Pillow, pyyaml. Uses `bin/yt-dlp` if present.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

import rtf

LOW_W = 128          # analysis width; the curve does not get better above this
LOW_FPS = 8          # 125ms per sample — finer than any cut we will ever make
CUT_FLOOR = 14.0     # mean abs frame delta that always counts as a shot change


# ---------------------------------------------------------------- shelling out


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, **kw)


def ytdlp() -> str:
    local = Path(__file__).parent / "yt-dlp"
    if local.is_file():
        return str(local)
    found = shutil.which("yt-dlp")
    if not found:
        sys.exit("no yt-dlp — put the standalone binary at bin/yt-dlp")
    return found


def probe_display_size(path: Path) -> tuple[int, int]:
    """True post-rotation dimensions.

    Read off a decoded frame rather than off the stream metadata, because a phone-shot
    source carries a rotation side-packet and the stream's own width/height lie about it.
    """
    tmp = path.with_suffix(".probe.png")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-frames:v", "1", str(tmp)])
    if not tmp.exists():
        raise RuntimeError(f"could not decode a frame from {path.name}")
    with Image.open(tmp) as im:
        size = im.size
    tmp.unlink(missing_ok=True)
    return size


def decode_gray(path: Path, w: int, h: int, fps: float, ss: float = None,
                t: float = None) -> np.ndarray:
    """Decode to a (frames, h, w) uint8 array. Seek before -i so it stays fast."""
    pre = ["-ss", f"{ss:.3f}"] if ss is not None else []
    dur = ["-t", f"{t:.3f}"] if t is not None else []
    out = run(["ffmpeg", "-v", "error", *pre, "-i", str(path), *dur,
               "-vf", f"fps={fps},scale={w}:{h}", "-pix_fmt", "gray",
               "-f", "rawvideo", "-"]).stdout
    n = len(out) // (w * h)
    if n < 2:
        raise RuntimeError(f"decoded {n} frames from {path.name}")
    return np.frombuffer(out[: n * w * h], np.uint8).reshape(n, h, w)


def decode_rgb(path: Path, ss: float, t: float, w: int = 48, h: int = 27) -> np.ndarray:
    out = run(["ffmpeg", "-v", "error", "-ss", f"{ss:.3f}", "-i", str(path),
               "-t", f"{t:.3f}", "-vf", f"fps=4,scale={w}:{h}", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "-"]).stdout
    n = len(out) // (w * h * 3)
    if n < 1:
        return np.zeros((1, h, w, 3), np.uint8)
    return np.frombuffer(out[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)


# ---------------------------------------------------------------- shot picking


def motion_curve(frames: np.ndarray) -> np.ndarray:
    """Mean absolute frame-to-frame delta. curve[i] is the change entering frame i+1."""
    f = frames.astype(np.float32)
    return np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))


def shot_bounds(curve: np.ndarray) -> list[tuple[int, int]]:
    """Split the curve at its own discontinuities — those are somebody else's cuts.

    The threshold is adaptive because a locked-off night shot and a handheld crowd shot
    have motion floors an order of magnitude apart; a fixed number would call every frame
    of the crowd shot a cut. CUT_FLOOR keeps it honest at the quiet end.
    """
    if len(curve) < 3:
        return [(0, len(curve))]
    thr = max(CUT_FLOOR, float(curve.mean() + 3.0 * curve.std()))
    cuts = [0, *(int(i) + 1 for i in np.where(curve > thr)[0]), len(curve)]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= 2]


def pick_window(frames: np.ndarray, curve: np.ndarray, cap: dict) -> tuple[float, float]:
    """Choose (start_s, dur_s): the best target-length window inside one shot.

    "Best" is energetic but not blown out and not near-black — a window whose luma sits
    at 3/255 is technically a shot and is visibly nothing.
    """
    target = cap["target_ms"] / 1000.0
    lo, hi = cap["min_ms"] / 1000.0, cap["max_ms"] / 1000.0
    luma = frames.astype(np.float32).mean(axis=(1, 2))

    best, best_score = None, -1e9
    for a, b in shot_bounds(curve):
        span = (b - a) / LOW_FPS
        if span < lo:
            continue
        dur = min(max(min(target, span), lo), hi)
        n = max(1, int(round(dur * LOW_FPS)))
        for s in range(a, max(a + 1, b - n + 1)):
            e = min(s + n, b)
            if e - s < 2:
                continue
            m = float(curve[s:e].mean())
            L = float(luma[s:e].mean())
            if L < 10 or L > 245:            # black frames and blown whites
                continue
            # motion is the signal; the luma term only pulls it off the extremes
            score = m + 6.0 * math.exp(-((L - 118.0) / 70.0) ** 2) + 0.4 * span
            if score > best_score:
                best_score, best = score, (s / LOW_FPS, (e - s) / LOW_FPS)
    if best:
        return best

    # No shot survived — take the most energetic legal window and accept the cut inside it.
    n = max(2, int(round(min(target, len(curve) / LOW_FPS) * LOW_FPS)))
    if len(curve) <= n:
        return 0.0, max(lo, len(curve) / LOW_FPS)
    k = int(np.argmax(np.convolve(curve, np.ones(n) / n, "valid")))
    return k / LOW_FPS, n / LOW_FPS


def crop_x(frames: np.ndarray, s: float, dur: float, src_w: int, src_h: int) -> int:
    """Left edge of a 9:16 crop, centred on where the movement actually is."""
    want = int(round(src_h * 9 / 16))
    if want >= src_w:
        return 0
    a, b = int(s * LOW_FPS), int((s + dur) * LOW_FPS)
    seg = frames[a:max(a + 2, b)].astype(np.float32)
    cols = np.abs(np.diff(seg, axis=0)).mean(axis=(0, 1))   # per-column motion energy
    if cols.sum() <= 1e-6:
        centre = frames.shape[2] / 2.0
    else:
        centre = float((cols * np.arange(len(cols))).sum() / cols.sum())
    x = int(round(centre / frames.shape[2] * src_w - want / 2))
    return max(0, min(x, src_w - want))


# ---------------------------------------------------------------- features


def features(path: Path, frames: np.ndarray, s: float, dur: float,
             curve: np.ndarray) -> dict:
    a, b = int(s * LOW_FPS), max(int(s * LOW_FPS) + 2, int((s + dur) * LOW_FPS))
    win = frames[a:b].astype(np.float32)
    mot = float(curve[a:min(b, len(curve))].mean()) if a < len(curve) else 0.0

    rgb = decode_rgb(path, s, dur).astype(np.float32)
    mean_rgb = rgb.mean(axis=(0, 1, 2))
    sat = float((rgb.max(axis=3) - rgb.min(axis=3)).mean() / 255.0)
    gy, gx = np.gradient(win.mean(axis=0))
    detail = float(np.hypot(gx, gy).mean() / 40.0)

    f = {
        "energy": round(min(1.0, mot / 22.0), 3),
        "luma": round(float(win.mean()) / 255.0, 3),
        "contrast": round(min(1.0, float(win.std()) / 64.0), 3),
        "warmth": round(float(np.clip((mean_rgb[0] - mean_rgb[2]) / 96.0 * 0.5 + 0.5, 0, 1)), 3),
        "saturation": round(min(1.0, sat), 3),
        "detail": round(min(1.0, detail), 3),
    }
    # How far past a resting shot this picture goes. Ranked into 1-5 by the engine,
    # because "intense" only means anything relative to the rest of the library.
    f["intensity_raw"] = round(
        0.45 * f["energy"] + 0.2 * f["saturation"] + 0.2 * f["contrast"] + 0.15 * f["detail"], 3
    )
    return f


def axes(f: dict) -> dict:
    """The ground, on declared axes, so the engine can invert it deliberately.

    Each axis is a 0-1 position between two named poles. This is the machine-readable
    half of congruence: hold the figure (grade, format, cut grid), move these.
    """
    def pole(v, lo, hi):
        return lo if v < 0.4 else (hi if v > 0.6 else "mid")
    return {
        "light":  {"v": f["luma"], "pole": pole(f["luma"], "dark", "bright")},
        "motion": {"v": f["energy"], "pole": pole(f["energy"], "still", "kinetic")},
        "temp":   {"v": f["warmth"], "pole": pole(f["warmth"], "cool", "warm")},
        "scale":  {"v": f["detail"], "pole": pole(f["detail"], "wide", "close")},
    }


# ---------------------------------------------------------------- per source


def resolve_query(yt: str, src: dict) -> list[dict]:
    """Candidates of a sane length, best first.

    Returns a list rather than a winner because the top hit is regularly the one that
    fails to download — age-gated, region-locked, or formats stripped. A one-shot resolve
    turns that into a permanently missing library slot for no reason.
    """
    target = src["url"] if src.get("url") else f"ytsearch8:{src['query']}"
    out = run([yt, "--no-warnings", "--flat-playlist", "--skip-download",
               "--print", "%(id)s\t%(title)s\t%(duration)s\t%(channel)s",
               target]).stdout.decode()
    good, unknown = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        vid, title, dur, chan = parts[0], parts[1], parts[2], parts[3]
        rec = {"id": vid, "title": title, "channel": chan,
               "url": f"https://www.youtube.com/watch?v={vid}"}
        try:
            d = float(dur)
        except ValueError:
            # A flat listing often reports no duration at all. Discarding those was
            # losing whole library slots to a metadata gap rather than to a bad video,
            # so they go to the back of the queue instead of the bin.
            unknown.append({**rec, "duration_s": 300.0})
            continue
        # No upper bound worth having: the best night-drive and ambience footage is
        # published as four-hour uploads, and we only ever download a window of it.
        if 15 <= d <= 6 * 3600:
            good.append({**rec, "duration_s": d})
    return good + unknown


def section_for(duration_s: float, window_s: float) -> str:
    """Where to look inside the source.

    A fixed `30-180` is right for a three-minute upload and wrong for a four-hour one,
    where it lands on the title card. Take the window from a quarter of the way in:
    past the intro on short sources, into the body on long ones.
    """
    start = min(max(30.0, duration_s * 0.25), max(0.0, duration_s - window_s - 5))
    return f"{start:.0f}-{start + window_s:.0f}"


def download(yt: str, meta: dict, section: str, dest: Path) -> Path | None:
    run([yt, "--no-warnings", "--no-playlist", "--force-overwrites",
         "-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]",
         "--download-sections", f"*{section}", "--max-filesize", "120M",
         "-o", str(dest.with_suffix(".%(ext)s")), meta["url"]])
    hits = sorted(dest.parent.glob(dest.stem + ".*"))
    return next((h for h in hits if h.suffix in {".mp4", ".mkv", ".webm"}), None)


def normalise(src: Path, out: Path, s: float, dur: float, x: int, w: int, h: int) -> None:
    """One shot, 1080x1920, 30fps, no audio. The figure the whole library holds constant."""
    want = int(round(h * 9 / 16))
    crop = f"crop={min(want, w)}:{h}:{x}:0," if want < w else ""
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.3f}", "-i", str(src),
         "-t", f"{dur:.3f}",
         "-vf", f"{crop}scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,fps=30,setsar=1,format=yuv420p",
         "-an", "-c:v", "libx264", "-crf", "19", "-preset", "veryfast",
         "-movflags", "+faststart", str(out)], check=False)


def sidecar(sid: str, out: Path, src_meta: dict, s: float, dur: float,
            f: dict, wants: str, query: str) -> dict:
    return {
        "schema": "rtf.clip/v1",
        "id": sid,
        "media": out.name,
        "title": wants or sid,
        "logline": wants,
        "source_meta": {
            "duration_ms": int(round(dur * 1000)),
            "width": 1080, "height": 1920, "fps": 30, "aspect": "9:16",
        },
        "rights": {
            # Recorded, not enforced. Downloading is not publishing; see research/06
            # before this library becomes paid creative.
            "source": "youtube",
            "owner": src_meta.get("channel"),
            "notes": "third-party source, proof-of-concept library",
        },
        "provenance": {
            "query": query,
            "url": src_meta.get("url"),
            "video_id": src_meta.get("id"),
            "video_title": src_meta.get("title"),
            "channel": src_meta.get("channel"),
            "section_start_s": round(s, 3),
            "section_dur_s": round(dur, 3),
        },
        "features": f,
        "axes": axes(f),
        "role": {"primary": "b_roll", "can_lead": True, "can_follow": True,
                 "min_useful_ms": 400},
        "cut_points": [{"t": 0, "kind": "in"},
                       {"t": int(round(dur * 1000)), "kind": "hard_cut"}],
    }


def fetch_one(yt: str, src: dict, cap: dict, outdir: Path, work: Path,
              rejected: set[str]) -> str:
    sid = src["id"]
    out = outdir / f"{sid}.mp4"
    if out.exists():
        return f"  {sid:<18} skip (exists)"

    cands = [c for c in resolve_query(yt, src) if c["id"] not in rejected]
    if not cands:
        return f"  {sid:<18} FAIL no usable search result"

    raw, meta = None, None
    for cand in cands[:4]:
        got = download(yt, cand, section_for(cand["duration_s"], cap["window_s"]),
                       work / sid)
        if got and got.exists() and got.stat().st_size > 100_000:
            raw, meta = got, cand
            break
        for leftover in work.glob(sid + ".*"):
            leftover.unlink(missing_ok=True)
    if raw is None:
        return f"  {sid:<18} FAIL download ({len(cands[:4])} candidates tried)"

    try:
        w, h = probe_display_size(raw)
        lh = max(2, int(round(LOW_W * h / w / 2)) * 2)
        frames = decode_gray(raw, LOW_W, lh, LOW_FPS)
        curve = motion_curve(frames)
        s, dur = pick_window(frames, curve, cap)
        x = crop_x(frames, s, dur, w, h)
        f = features(raw, frames, s, dur, curve)
        normalise(raw, out, s, dur, x, w, h)
        if not out.exists():
            return f"  {sid:<18} FAIL normalise"
        desc = sidecar(sid, out, meta, s, dur, f, src.get("wants", ""), src.get("query", ""))
        (outdir / f"{sid}.clip.yaml").write_text(yaml.safe_dump(desc, sort_keys=False))
    except Exception as exc:                                    # noqa: BLE001
        return f"  {sid:<18} FAIL {type(exc).__name__}: {exc}"
    finally:
        for leftover in work.glob(sid + ".*"):
            leftover.unlink(missing_ok=True)

    return (f"  {sid:<18} {dur:4.1f}s  @{s:6.1f}s  E{f['energy']:.2f} "
            f"L{f['luma']:.2f} W{f['warmth']:.2f}  {meta['title'][:44]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("brief", help="brief id or path to a .brief.yaml")
    ap.add_argument("--out", help="library dir (default lib/stock/<brief id>)")
    ap.add_argument("--only", nargs="*", help="only these source ids")
    ap.add_argument("--limit", type=int, help="stop after N sources")
    args = ap.parse_args()

    p = Path(args.brief)
    if not p.is_file():
        p = rtf.find_root(Path.cwd()) / "lib" / "briefs" / f"{args.brief}.brief.yaml"
    if not p.is_file():
        sys.exit(f"no brief at {p}")
    brief = yaml.safe_load(p.read_text())

    root = rtf.find_root(p)
    outdir = Path(args.out) if args.out else root / "lib" / "stock" / brief["id"]
    outdir.mkdir(parents=True, exist_ok=True)
    work = root / ".cache" / "fetch"
    work.mkdir(parents=True, exist_ok=True)

    cap = {"window_s": 150, "target_ms": 4000, "min_ms": 1200, "max_ms": 10000}
    cap.update(brief.get("capture") or {})

    sources = brief["sources"]
    if args.only:
        sources = [s for s in sources if s["id"] in set(args.only)]
    if args.limit:
        sources = sources[: args.limit]

    # Videos screen_clips.py has already thrown out. Without this the refill just
    # re-downloads the same tutorial that was rejected a minute ago, forever.
    rej_path = outdir / "rejected.yaml"
    rejected = set(yaml.safe_load(rej_path.read_text()) or []) if rej_path.exists() else set()

    yt = ytdlp()
    print(f"{brief['id']}: {len(sources)} sources -> {outdir}"
          + (f"  ({len(rejected)} blacklisted)" if rejected else ""))
    fails = 0
    for src in sources:
        line = fetch_one(yt, src, cap, outdir, work, rejected)
        if "FAIL" in line:
            fails += 1
        print(line, flush=True)

    have = len(list(outdir.glob("*.mp4")))
    print(f"\nlibrary: {have} clips, {fails} failed this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
