"""Scoring — the kit plays the record it was cut for.

The failure this exists to end: a kit for "Un Poquito Más" came back as a night drive with
a lofi instrumental the model wrote itself, over a song it had never heard. PIPELINE-SPEC
§4 called the generated soundtrack a defect and proposed `ffmpeg -an` — delivering the
loops silent. That is the right diagnosis and half the cure. A silent loop is not the
product either: the master is uploaded, measured, sectioned and hook-windowed by the time a
kit is bought, and a clip that does not carry it is a stock backdrop with this song's
prompt attached.

So every video asset gets a second object written next to it: the same frames, with the
master's own seconds mapped in place of whatever the provider returned.

Three rules hold this together.

**The provider's bytes are never overwritten.** `Asset.key` still points at exactly what
came back, which is what makes its `sha256` and the manifest's content hash mean anything.
The scored cut is a derived object under its own key, and `Asset.playable_key` is what a
screen or a download reaches for. Delete a kit and both go (`KitService._owned_keys`).

**Which seconds is a per-shot fact, not a per-song one.** A brief that names two hooks
deals its loops round-robin across them, so `Song.hook` is the wrong answer for at least
half of them. `ShotSpec.hook_start_ms` travels through the plan onto the asset for exactly
this, and this service reads it there.

**Every video says which happened.** A clip that could not be scored — no master uploaded,
no ffmpeg in this process, a decode that failed — keeps `audio_note` saying so. That is the
same rule the plate path follows: a face silently not sent and a face sent look identical
on a screen that only renders success, and so do a scored clip and one the model scored for
itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

from remixkit.adapters import scoring as mux
from remixkit.auth.provider import Principal
from remixkit.domain.models import Asset, Kit, Modality, Song
from remixkit.ports.storage import Storage
from remixkit.services.songs import SongService

log = logging.getLogger(__name__)


def _clock(ms: int) -> str:
    total = max(0, ms) / 1000.0
    return f"{int(total // 60)}:{total % 60:04.1f}"


class ScoringService:
    def __init__(self, storage: Storage, songs: SongService, *, enabled: bool = True) -> None:
        self._storage = storage
        self._songs = songs
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    # -- the worker path ---------------------------------------------------------
    def score_kit(self, principal: Principal, kit: Kit, song: Song | None = None) -> None:
        """Lay the master under every video in this kit. Mutates the assets in place.

        Called by `KitService.run` after the run has landed and before the kit is written,
        so a kit is never briefly `ready` with unscored clips on it.

        Nothing here can fail a kit. A clip that could not be scored is still a clip
        somebody paid for, and the honest outcome is the frames plus a note saying the
        record is not under them — not a `failed` row and no assets at all.
        """
        videos = [a for a in kit.assets if a.modality is Modality.VIDEO and a.key]
        if not videos:
            return

        if not self._enabled:
            log.info("kit %s: scoring is off (RK_SCORE_WITH_MASTER)", kit.id)
            for asset in videos:
                asset.audio_note = (
                    "Not scored — RK_SCORE_WITH_MASTER is off in this deployment, so this "
                    "clip carries whatever audio the model returned."
                )
            return

        song = song or self._songs.get(principal, kit.song_id)
        master, refusal = self._master(song)
        if master is None:
            for asset in videos:
                asset.audio_note = refusal
            log.warning("kit %s: not scored — %s", kit.id, refusal)
            return

        names = self._window_names(kit)
        for asset in videos:
            start_ms, window_note = self._window(asset, song, names)
            try:
                scored = mux.score(
                    self._storage.get(asset.key),
                    master,
                    start_ms=start_ms,
                    video_suffix=Path(asset.key).suffix or ".mp4",
                    master_suffix=Path(song.master_key or "").suffix or ".wav",
                )
            except Exception as exc:  # ffmpeg missing, a bad decode, a storage read
                asset.audio_note = f"The master is not under this clip — {exc}"
                log.warning("kit %s: asset %s not scored (%s)", kit.id, asset.id, exc)
                continue

            key = self._scored_key(asset.key)
            self._storage.put(key, scored, content_type="video/mp4")
            asset.scored_key = key
            asset.audio_source_key = song.master_key
            asset.audio_start_ms = start_ms
            asset.audio_note = window_note
            log.info("kit %s: asset %s scored from %s", kit.id, asset.id, _clock(start_ms))

    # -- the delivery path -------------------------------------------------------
    def scored_now(
        self, principal: Principal, kit: Kit, asset: Asset
    ) -> tuple[bytes | None, str | None]:
        """One clip with the master under it, minted on the spot and not stored.

        The fallback for a kit that ran before this existed, or one whose worker had no
        ffmpeg. Returns `(None, why)` when it cannot be done, and the caller delivers the
        provider's own bytes with that reason attached rather than refusing the download.
        """
        if not self._enabled or asset.modality is not Modality.VIDEO or not asset.key:
            return None, None

        song = self._songs.get(principal, kit.song_id)
        master, refusal = self._master(song)
        if master is None:
            return None, refusal

        start_ms, note = self._window(asset, song, self._window_names(kit))
        try:
            scored = mux.score(
                self._storage.get(asset.key),
                master,
                start_ms=start_ms,
                video_suffix=Path(asset.key).suffix or ".mp4",
                master_suffix=Path(song.master_key or "").suffix or ".wav",
            )
        except Exception as exc:
            return None, f"The master is not under this clip — {exc}"
        return scored, note

    # -- internals ---------------------------------------------------------------
    def _master(self, song: Song) -> tuple[bytes | None, str | None]:
        """The uploaded master's bytes, or the reason there are none.

        The refusal is written for the person who has to fix it, because there is exactly
        one fix and it is not obvious from a silent clip: upload the master.
        """
        if not song.master_key:
            return None, (
                f'No master has been uploaded for "{song.title}", so this clip carries '
                "whatever audio the model returned. Upload the mastered track on the song "
                "and re-run the kit to put the record under it."
            )
        try:
            return self._storage.get(song.master_key), None
        except Exception as exc:
            return None, f"The master could not be read from storage ({exc})."

    @staticmethod
    def _scored_key(key: str) -> str:
        """The derived object's key, beside the provider's own.

        `.scored.mp4` rather than a separate prefix so the two objects sort together in a
        bucket listing — reading a run's directory should show the clip and its scored cut
        adjacent, not in two places nobody would think to compare.
        """
        suffix = Path(key).suffix
        stem = key[: -len(suffix)] if suffix else key
        return f"{stem}.scored.mp4"

    @staticmethod
    def _window_names(kit: Kit) -> dict[int, str]:
        """Hook start → the name it was bought under, from the brief.

        The brief records the windows as they were when the kit was bought, so this names
        the section even if somebody has since renamed or deleted it — which is the same
        reason the brief stores them at all.
        """
        names: dict[int, str] = {}
        for window in kit.brief.get("hook_windows") or []:
            try:
                names[int(window["start_ms"])] = str(window.get("name") or "")
            except (KeyError, TypeError, ValueError):
                continue
        return names

    def _window(
        self, asset: Asset, song: Song, names: dict[int, str]
    ) -> tuple[int, str]:
        """Where in the record this clip starts, and the sentence that says so.

        The shot's own window when it has one. A song with no measured hook falls back to
        the top of the master — and *says* it fell back, because a `0` that looks like a
        measured hook start is exactly the class of number the analyser refuses to invent.
        """
        start = asset.audio_start_ms
        if start is None and song.hook.duration_ms:
            start = song.hook.start_ms
        if start is None:
            return 0, (
                f'"{song.title}" has no measured hook, so this clip carries the master '
                "from 0:00.0. Measure the song or set a hook window, then re-run the kit "
                "to cut it to the part of the record it is for."
            )

        named = names.get(int(start), "").strip()
        where = f"{_clock(start)}" + (f" · {named}" if named else "")
        return int(start), f"The master is under this clip, from {where}."
