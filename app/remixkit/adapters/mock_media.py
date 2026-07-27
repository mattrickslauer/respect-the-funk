"""Real media for the mock provider.

Genblaze's `MockProvider` returns assets pointing at `https://mock.test/…`, which the
sink then tries to fetch — so out of the box a mock run fails at asset transfer, not at
generation. It also means a "working" demo would show broken tiles.

So the mock path synthesises **actual playable files** with ffmpeg and hands the
provider `file://` URLs. The sink transfers them exactly as it transfers a real
provider's output: same code path, same hashing, same manifest. What is synthetic is
the pixels, and nothing else.

Files go under the system temp directory because Genblaze's transfer layer allowlists
`file://` reads to temp roots only (`ALLOWED_FILE_ROOTS`) — a deliberate anti-SSRF
measure that this respects rather than works around.

If ffmpeg is missing, the modality is reported unavailable and skipped, with the same
machinery a missing provider uses. A demo that silently degrades to still images
labelled as video would be worse than one that says what it could not make.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(tempfile.gettempdir()) / "remixkit-mock-assets"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _hue(seed: str) -> tuple[int, int, int]:
    """A stable colour per prompt, so distinct shots look distinct."""
    digest = hashlib.sha256(seed.encode()).digest()
    # Kept dark and saturated so white overlay type stays legible, matching the
    # "templatable backdrop with a clean centre" the brief actually asks for.
    return (40 + digest[0] % 90, 20 + digest[1] % 70, 60 + digest[2] % 120)


def _run(cmd: list[str], target: Path) -> bool:
    """Render to a unique temp path, then `os.replace` into place.

    The cache check is `if path.exists(): return path`, and ffmpeg writes its output
    incrementally — so a second caller asking for the same prompt while the first is
    still rendering would see the path appear and hand back a half-written file. Two
    concurrent generations of one prompt is not hypothetical: the pipeline fans out
    across threads, and a redelivered job renders the same shots again.

    A torn mp4 is the worst possible version of this bug. It is still structurally a
    video — ffprobe reports the right duration — but it carries a duplicated `moov`
    atom, so it plays incorrectly and silently refuses to hold an embedded manifest.
    That surfaces far away, as unverifiable provenance, with nothing pointing back here.
    """
    # The extension must stay LAST. ffmpeg infers the container from the output
    # filename, so a temp path ending in `.part` fails with "Error initializing the
    # muxer" — and because the failure is per-shot, the run then quietly falls back to
    # whatever is already cached, which is how a stale file survives a "clean" rerun.
    tmp = target.with_name(f".{target.stem}.{os.getpid()}.{threading.get_ident()}.part{target.suffix}")
    try:
        subprocess.run([*cmd, str(tmp)], check=True, capture_output=True, timeout=120)
        if not tmp.exists() or tmp.stat().st_size == 0:
            return False
        os.replace(tmp, target)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        stderr = getattr(exc, "stderr", b"")
        log.warning("ffmpeg failed: %s", stderr.decode(errors="replace")[-400:] if stderr else exc)
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


def video(prompt: str, seconds: float = 6.0) -> Path | None:
    """A 9:16 loop: a colour wash with drifting grain. Vertical, because that is the
    only aspect ratio the product ships."""
    path = _ROOT / f"v_{hashlib.sha256(f'{prompt}{seconds}'.encode()).hexdigest()[:16]}.mp4"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    r, g, b = _hue(prompt)
    ok = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s=540x960:d={seconds}:r=24",
            "-vf", "noise=alls=14:allf=t+u,gblur=sigma=1.4,vignette",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        ],
        path,
    )
    return path if ok and path.exists() else None


def image(prompt: str) -> Path | None:
    """A vertical card. The lyric text is not drawn — drawtext needs a font that may
    not be present, and a failed font lookup would take the whole kit down."""
    path = _ROOT / f"i_{hashlib.sha256(prompt.encode()).hexdigest()[:16]}.png"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    r, g, b = _hue(prompt)
    ok = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s=540x960",
            "-vf", "noise=alls=10:allf=t,vignette",
            "-frames:v", "1",
        ],
        path,
    )
    return path if ok and path.exists() else None


def audio(prompt: str, seconds: float = 3.0) -> Path | None:
    path = _ROOT / f"a_{hashlib.sha256(prompt.encode()).hexdigest()[:16]}.mp3"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    freq = 180 + (int(hashlib.sha256(prompt.encode()).hexdigest()[:4], 16) % 300)
    ok = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={seconds}",
            "-af", "afade=t=in:d=0.3,afade=t=out:st=%.1f:d=0.3" % max(0.0, seconds - 0.3),
            "-b:a", "96k",
        ],
        path,
    )
    return path if ok and path.exists() else None
