"""Songs — measured once, reused forever.

The one opinion this service holds: **a BPM without a method is not accepted.**
FORMAT-SPEC requires the provenance of a measurement, not just the number, and
PRODUCT.md names catalogue onboarding — not compute — as the real bottleneck. A field
that quietly accepts an unsourced 128 is how that bottleneck gets hidden instead of
measured.
"""

from __future__ import annotations

from remixkit.auth.provider import Principal
from remixkit.domain.models import ApprovalState, HookWindow, Song, slugify
from remixkit.ports.repository import DocumentRepository
from remixkit.ports.storage import Storage
from remixkit.services.errors import Conflict, NotFound

COLLECTION = "songs"


class SongService:
    def __init__(self, repo: DocumentRepository, storage: Storage, *, key_prefix: str = "remixkit") -> None:
        self._repo = repo
        self._storage = storage
        self._prefix = key_prefix

    def list_for_artist(self, principal: Principal, artist_id: str) -> list[Song]:
        songs = [
            s
            for s in self._repo.list(principal.tenant_id, COLLECTION, Song)
            if s.artist_id == artist_id
        ]
        return sorted(songs, key=lambda s: s.title.lower())

    def list(self, principal: Principal) -> list[Song]:
        return sorted(
            self._repo.list(principal.tenant_id, COLLECTION, Song), key=lambda s: s.title.lower()
        )

    def get(self, principal: Principal, song_id: str) -> Song:
        song = self._repo.get(principal.tenant_id, COLLECTION, song_id, Song)
        if song is None:
            raise NotFound(f"No song {song_id!r}")
        return song

    def create(
        self,
        principal: Principal,
        artist_id: str,
        *,
        title: str,
        bpm: float | None = None,
        bpm_method: str | None = None,
        isrc: str | None = None,
        spotify_url: str | None = None,
    ) -> Song:
        title = title.strip()
        if not title:
            raise Conflict("A song needs a title.")
        if bpm is not None and not (bpm_method or "").strip():
            raise Conflict(
                "A BPM needs a method. Record how it was measured "
                "(e.g. 'measure_beat.py, onset autocorrelation, 4 bars from the drop')."
            )
        song = Song(
            tenant_id=principal.tenant_id,
            artist_id=artist_id,
            slug=slugify(title),
            title=title,
            bpm=bpm,
            bpm_method=(bpm_method or None) if bpm is not None else None,
            isrc=isrc or None,
            spotify_url=spotify_url or None,
        )
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def set_measurement(
        self,
        principal: Principal,
        song_id: str,
        *,
        bpm: float | None = None,
        bpm_method: str | None = None,
        drop_ms: int | None = None,
    ) -> Song:
        song = self.get(principal, song_id)
        if bpm is not None:
            if not (bpm_method or song.bpm_method or "").strip():
                raise Conflict("A BPM needs a method. Record how it was measured.")
            song.bpm = bpm
        if bpm_method is not None:
            song.bpm_method = bpm_method or None
        if drop_ms is not None:
            song.drop_ms = drop_ms
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def set_hook(self, principal: Principal, song_id: str, *, start_ms: int, end_ms: int) -> Song:
        """The hook window — Pillar 13's one free lever."""
        if end_ms <= start_ms:
            raise Conflict("The hook window must end after it starts.")
        song = self.get(principal, song_id)
        song.hook = HookWindow(start_ms=start_ms, end_ms=end_ms)
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def master_upload_url(self, principal: Principal, song_id: str, *, content_type: str) -> dict:
        """Presigned PUT — the master goes browser→bucket, never through this process.

        §2b rule 2. It is also the difference between a 128 MB WAV being a storage
        event and it being a request that has to fit in a Lambda body limit.
        """
        song = self.get(principal, song_id)
        suffix = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/flac": "flac"}.get(
            content_type, "bin"
        )
        key = f"{self._prefix}/masters/{principal.tenant_id}/{song.id}.{suffix}"
        url = self._storage.presign_put(key, content_type=content_type)
        song.master_key = key
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return {"url": url, "key": key, "method": "PUT"}

    def set_approval(self, principal: Principal, song_id: str, state: ApprovalState) -> Song:
        song = self.get(principal, song_id)
        song.approval = state
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song
