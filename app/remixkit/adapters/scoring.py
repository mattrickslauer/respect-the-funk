"""Laying the master under the picture.

Some clips are a picture the *record* plays over. Everything upstream of here treats that
as a negative — `recipes.SILENT_NEGATIVES` asks the model not to compose a soundtrack, and
PIPELINE-SPEC §4 proposes `-an` as the deterministic version of the same wish. Both are
half the job. A silent loop is not what a label asked for either: the asset that leaves
this system has to carry the song it was cut for, or the kit is a stock clip with the
right prompt attached.

*Some* clips, not all of them — and the difference is a format's to declare. This module
does exactly what it is told and does it exhaustively, so the decision about whether it
should run at all belongs to the caller: see `Recipe.score_with_master` and the guard in
`services.scoring`. Handed a talking-head clip, the code below will happily replace the
speech with the song.

So this is the other half, and it is the one ffmpeg is actually good at: **discard whatever
the model composed and map the master's own bytes in its place.** The picture is
stream-copied, so the frames delivered are the frames the provider rendered — this is a
container rewrite plus an audio encode, not a re-render, and the pixels a manifest
describes are untouched by it.

Three choices worth stating, because each one is a different clip:

**The cut starts at the hook and runs as long as the picture does.** Not as long as the
hook: a model snaps 9.3 seconds to 8 or to 12, and the loop is whatever the model rendered.
Truncating the audio to the hook window would leave a 12-second clip with 3.7 seconds of
silence at the end, and looping the window would put a seam in the middle of a bar. Running
on into the record is musically continuous and is *the actual song*, which is the whole
point of this module.

**Silence is padded, never wrapped.** `apad` under `-shortest` means a clip longer than
what remains of the master ends in silence rather than in the top of the track. A hook near
the end of a record is the normal case for an outro, and wrapping around to bar 1 there
would sound like an edit nobody made.

**The original audio track is not mapped at all.** `-map 0:v:0 -map 1:a:0` is exhaustive:
whatever Veo, Sora or Seedance composed is not muted, it is absent from the output. That is
the deterministic fix §4 asked for, arrived at from the other side.

ffmpeg is already this repo's one non-Python dependency (`adapters/mock_media`, the
analyser's decode path, the Dockerfile's pinned static build). A process without it reports
unavailable and the clip is delivered as the provider returned it, with the reason attached
— never silently unscored.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# A 10-second 9:16 clip and a 4-minute master. Generous against a cold container on a
# throttled CPU, and bounded so a wedged encode cannot hold a worker slot forever.
TIMEOUT_S = 180

# What the audio is encoded to. AAC because the container is MP4 and every browser,
# phone and editing tool reads it; 192k stereo at 48 kHz because this is a *master*
# going under a video a label will publish, and the frugal setting is audible.
AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


class ScoringUnavailable(RuntimeError):
    """This process cannot mux audio, and says which piece is missing."""


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def unavailable_reason() -> str:
    return (
        "ffmpeg is not on PATH, so the master cannot be laid under a clip. "
        "Install it (brew install ffmpeg) — the container image already ships it."
    )


def score(
    video: bytes,
    master: bytes,
    *,
    start_ms: int = 0,
    video_suffix: str = ".mp4",
    master_suffix: str = ".wav",
) -> bytes:
    """One clip with the master under it, starting `start_ms` into the record.

    Returns the new MP4's bytes. Raises `ScoringUnavailable` when ffmpeg is missing or
    refuses the inputs — the caller decides whether that is a failed kit or an unscored
    delivery, and in this system it is always the second.
    """
    if not available():
        raise ScoringUnavailable(unavailable_reason())
    if not video:
        raise ScoringUnavailable("There is no clip to score.")
    if not master:
        raise ScoringUnavailable("There is no master to score it with.")

    # The extension must survive into the temp path: ffmpeg infers both the demuxer and
    # the muxer from the filename, and a master written to `.bin` is a probe failure
    # rather than a decode. `mock_media._run` learned this the same way.
    with tempfile.TemporaryDirectory() as tmp:
        room = Path(tmp)
        clip = room / f"clip{video_suffix or '.mp4'}"
        record = room / f"master{master_suffix or '.wav'}"
        out = room / "scored.mp4"
        clip.write_bytes(video)
        record.write_bytes(master)

        command = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(clip),
            # Input seeking on the master — ffmpeg jumps to the keyframe and decodes
            # forward from there rather than decoding the whole record to throw it away.
            "-ss", f"{max(0, start_ms) / 1000:.3f}",
            "-i", str(record),
            # Exhaustive by design: the picture from the clip, the sound from the record,
            # and nothing the model composed for itself.
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            *AUDIO_CODEC,
            # Pad the master with silence so `-shortest` is always the video, which makes
            # the output exactly as long as the clip the provider rendered.
            "-af", "apad",
            "-shortest",
            "-movflags", "+faststart",
            str(out),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise ScoringUnavailable(f"ffmpeg timed out after {TIMEOUT_S}s") from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            stderr = getattr(exc, "stderr", b"") or b""
            detail = stderr.decode(errors="replace").strip().splitlines()
            raise ScoringUnavailable(
                f"ffmpeg could not lay the master under this clip: "
                f"{detail[-1] if detail else exc}"
            ) from exc

        if not out.exists() or out.stat().st_size == 0:
            raise ScoringUnavailable("ffmpeg produced no output.")
        return out.read_bytes()


def duration_ms(data: bytes, *, suffix: str = ".mp4") -> int | None:
    """How long a piece of media actually is, or `None` if that cannot be established.

    Used to say on the record how many seconds of the master a clip carries. `None` rather
    than a guess for the same reason the analyser refuses to invent a BPM: a length nobody
    measured, printed next to lengths that were, is indistinguishable from one that was.
    """
    if shutil.which("ffprobe") is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=suffix or ".mp4", delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        done = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            timeout=30,
        )
        if done.returncode != 0:
            return None
        return int(round(float(done.stdout.decode().strip()) * 1000))
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None
    finally:
        # Not a TemporaryDirectory because ffprobe wants the file closed on Windows and
        # this is the smaller of the two awkwardnesses.
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover — a missing temp file is not a failure
            pass
