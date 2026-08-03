"""Build the picture track and marry it to the narration.

    python content/demo/video/build.py            # everything, resuming what exists
    python content/demo/video/build.py --preview  # 1 frame per second, quick look
    python content/demo/video/build.py --from 40 --to 52 --force   # recut one stretch

Three steps, each skippable on its own: synthesise the voice-over (only if the script
changed — see vo.py, the character budget is finite), screenshot every frame of
render.html, then encode. The frames are kept rather than piped straight into ffmpeg,
because a 120-second render is long enough that re-doing all of it to fix eight seconds
is the difference between a minute and twenty.
"""

from __future__ import annotations

import argparse
import http.server
import json
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
OUT = HERE / "out"
FRAMES = OUT / "frames"
PORT = 8931
FPS = 30


def serve(root: Path, port: int) -> http.server.ThreadingHTTPServer:
    """A quiet static server over content/demo, so the page can load video and JSON.

    Threading, not the default one-request-at-a-time handler. The page holds six
    `<video>` elements and seeking them issues overlapping HTTP range requests; served
    serially those queue behind each other and the capture drops from ~11 frames per
    second to under one. That is not a hypothetical — it is what this script did until
    the blocking handler was replaced.

    It also refuses to start on a port somebody else is already on, rather than quietly
    letting the page load from a stranger's server. That failure looks like a slow
    render, which is a miserable thing to debug.
    """

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

    class Server(http.server.ThreadingHTTPServer):
        allow_reuse_address = False
        daemon_threads = True

    try:
        srv = Server(("127.0.0.1", port), Handler)
    except OSError as e:
        sys.exit(f"port {port} is already in use ({e}) — something else is serving there")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def need(cmd: str) -> str:
    p = shutil.which(cmd)
    if not p:
        sys.exit(f"{cmd} is not on PATH")
    return p


# The b-roll clips, and the keys render.html refers to them by.
CLIPS = {
    "perf":     "c0b5c9af-db0b-493e-9580-8519985420d6.mp4",
    "direct":   "82745a41-1479-4f79-b62a-8139f22a14dc.mp4",
    "backdrop": "8888da68-432c-4eb6-8b06-f3440b11c3cd.mp4",
    "extra":    "2dfa811e-607f-48ff-af71-b6e6306cfb99.mp4",
}


def explode_broll() -> None:
    """Turn each b-roll clip into a still per frame, once.

    The renderer used to hold real `<video>` elements and seek them, which is the
    obvious way to do it and was the single worst decision in this pipeline: seeking
    four clips per frame put a decode on the critical path and the capture fell from ten
    frames a second to under one. Frames on disk cost about 60MB and one ffmpeg pass,
    and the capture never touches a decoder again.

    They are numbered from 0 so the renderer's frame index is just `round(t * 30)`.
    """
    for key, name in CLIPS.items():
        dest = OUT / "broll-frames" / key
        if dest.exists() and len(list(dest.glob("*.jpg"))) > 200:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        print(f"exploding {key}")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(DEMO / "broll" / name),
            "-vf", f"fps={FPS},scale=540:-2",
            "-q:v", "4",
            "-start_number", "0",
            str(dest / "%05d.jpg"),
        ], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="1 frame per second, no encode")
    ap.add_argument("--from", dest="start", type=float, default=0.0)
    ap.add_argument("--to", dest="end", type=float, default=None)
    ap.add_argument("--force", action="store_true", help="re-shoot frames that already exist")
    ap.add_argument("--skip-vo", action="store_true")
    args = ap.parse_args()

    need("ffmpeg")
    need("node")

    if not args.skip_vo:
        subprocess.run([sys.executable, str(HERE / "vo.py")], check=True)

    align = OUT / "alignment.json"
    if not align.exists():
        sys.exit("no alignment.json — run vo.py first")
    duration = json.loads(align.read_text())["duration"]
    end = args.end if args.end is not None else duration

    explode_broll()

    if not (HERE / "node_modules" / "playwright-core").exists():
        print("installing playwright-core (browsers are already cached)")
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=HERE, check=True)

    srv = serve(DEMO, PORT)
    try:
        cmd = [
            "node", str(HERE / "capture.js"),
            "--url", f"http://127.0.0.1:{PORT}/video/render.html",
            "--out", str(FRAMES),
            "--fps", str(FPS),
            "--duration", str(duration),
            "--from", str(args.start),
            "--to", str(end),
        ]
        if args.preview:
            cmd += ["--every", str(FPS)]
        if args.force:
            cmd += ["--force"]
        r = subprocess.run(cmd, cwd=HERE)
        if r.returncode == 3:
            print("\n!! some cues did not match the voice-over — see above", file=sys.stderr)
        elif r.returncode:
            sys.exit(r.returncode)
    finally:
        srv.shutdown()

    if args.preview:
        print(f"preview frames in {FRAMES}")
        return

    # ffmpeg's numbered-input reader starts at 0 unless told otherwise, so a partial
    # recut (--from 40) would find no frame 000000 and fail. Start where the frames do.
    shot = sorted(FRAMES.glob("*.png"))
    if not shot:
        sys.exit("no frames to encode")
    first_frame = int(shot[0].stem)

    out = OUT / "remixkit-demo-visuals.mp4"
    print(f"encoding {out.name} from frame {first_frame} ({len(shot)} frames)")
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-start_number", str(first_frame),
        "-i", str(FRAMES / "%06d.png"),
        # The narration is seeked to match, so a recut of one stretch still plays in
        # sync with the words that belong to it.
        "-ss", f"{first_frame / FPS:.3f}",
        "-i", str(OUT / "narration.mp3"),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264",
        "-profile:v", "high", "-preset", "slow",
        # The stage is mostly flat dark panels, where 8-bit 4:2:0 bands badly. A low CRF
        # and a wide GOP cost file size and buy back the gradients.
        "-crf", "16",
        "-pix_fmt", "yuv420p",
        "-x264-params", "keyint=60:min-keyint=30",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ], check=True)

    size = out.stat().st_size / 1e6
    print(f"\n{out}  ·  {duration:.1f}s  ·  {size:.1f} MB")
    print("this is the second half. The on-camera intro goes in front of it.")


if __name__ == "__main__":
    main()
