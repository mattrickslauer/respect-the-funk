"""Transcription — hear an uploaded master, once, and keep the words it found.

The same split as kits and analysis, for the same reason: `request()` is what an HTTP
handler calls and must stay fast, `run()` is what the worker calls and spends a provider
round trip on a whole song. A speech-to-text call on a four-minute master is tens of
seconds of somebody else's latency, which is not work an HTTP request holds (§2b rule 3),
so this is a queued job like everything else — inline on a laptop, SQS + Batch in the
deployment, no code difference either side.

The gates before anything is enqueued are the analysis service's, plus one that only
exists here:

1. **A master, and the bytes really there.** `register_master` checks storage before the
   key is recorded, so a song claiming a master it does not have cannot reach this.
2. **A transcriber that can actually run.** A deployment with no ElevenLabs key refuses
   here, with the missing piece named, rather than queueing a job that will 401 in a
   container nobody is watching.
3. **Corrections are not silently discarded.** A re-transcription replaces the lyric
   wholesale (`services.songs.apply_transcript` explains why it cannot merge), so a song
   whose lines a person has already fixed refuses the second run and says how many lines
   would be lost. `force=True` is the caller saying yes anyway — which the console asks
   with a confirmation and the API takes as a query parameter, so neither can do it by
   accident.

`run()` is idempotent in the way that matters: transcribing the same bytes twice produces
the same lines and replaces the previous measured ones, so a redelivered message costs a
provider call and changes nothing (§2b rule 4).
"""

from __future__ import annotations

import hashlib
import logging

from remixkit.auth.provider import Principal
from remixkit.domain.models import AnalysisStatus, Song
from remixkit.ports.lyrics import Transcriber
from remixkit.ports.queue import JobQueue
from remixkit.ports.storage import Storage
from remixkit.services.errors import Conflict, ServiceError, TranscriptionUnavailable
from remixkit.services.songs import SongService

log = logging.getLogger(__name__)

JOB_TYPE = "transcribe-song"


class TranscriptionService:
    def __init__(
        self,
        songs: SongService,
        storage: Storage,
        queue: JobQueue,
        transcriber: Transcriber,
        *,
        language: str = "",
    ) -> None:
        self._songs = songs
        self._storage = storage
        self._queue = queue
        self._transcriber = transcriber
        # Empty means "let the provider detect it". A configured code is a deployment
        # saying it knows what its catalogue is in, which is a claim worth honouring on a
        # heavily processed vocal where detection is the weakest step.
        self._language = language.strip()

    @property
    def available(self) -> bool:
        return bool(getattr(self._transcriber, "available", False))

    @property
    def unavailable_reason(self) -> str:
        return getattr(self._transcriber, "unavailable_reason", "")

    @property
    def transcriber_name(self) -> str:
        return getattr(self._transcriber, "name", "unknown")

    # -- the fast path ----------------------------------------------------------
    def request(self, principal: Principal, song_id: str, *, force: bool = False) -> Song:
        """Queue a transcription of this song's master. Must stay well under 200 ms."""
        song = self._songs.get(principal, song_id)
        if not song.master_key:
            raise Conflict(
                "There is no master to transcribe. Upload the audio first — the lyric is "
                "read off the recording, not off the metadata."
            )
        if not self.available:
            raise TranscriptionUnavailable(self.unavailable_reason)
        if not getattr(self._queue, "can_execute", True):
            raise Conflict(
                getattr(self._queue, "unavailable_reason", "The job queue cannot execute jobs.")
            )

        edited = song.lyrics.edited_lines if song.lyrics else []
        if edited and not force:
            raise Conflict(
                f"This lyric has {len(edited)} hand-corrected line(s). Re-transcribing "
                "replaces the whole transcript and those corrections would be lost — "
                "re-run with force to do it anyway."
            )

        song = self._songs.mark_transcription(
            principal, song_id, AnalysisStatus.QUEUED, transcriber=self.transcriber_name
        )
        self._queue.enqueue(
            JOB_TYPE,
            {"tenant_id": principal.tenant_id, "song_id": song.id},
            # Prefixed for the same reason the analysis job's key is: kits, analyses and
            # transcriptions share one inline queue's in-flight set, and a song being
            # measured must not deduplicate away the request to transcribe it.
            dedupe_key=f"{JOB_TYPE}:{song.id}",
        )
        return song

    # -- the slow path (worker) -------------------------------------------------
    def run(self, tenant_id: str, song_id: str) -> Song:
        """Fetch, hear, write. Called by the queue consumer, never by a handler."""
        principal = Principal(tenant_id=tenant_id, subject="worker")
        song = self._songs.get(principal, song_id)
        if not song.master_key:
            return self._songs.mark_transcription(
                principal, song_id, AnalysisStatus.FAILED, error="No master to transcribe."
            )

        self._songs.mark_transcription(
            principal, song_id, AnalysisStatus.RUNNING, transcriber=self.transcriber_name
        )
        try:
            data = self._storage.get(song.master_key)
            transcription = self._transcriber.transcribe(
                data,
                filename=song.master_key,
                language=self._language or None,
            )
        except ServiceError as exc:
            log.warning("transcription of %s refused: %s", song_id, exc)
            return self._songs.mark_transcription(
                principal, song_id, AnalysisStatus.FAILED, error=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - the job records its own failure
            log.exception("transcription of %s failed", song_id)
            return self._songs.mark_transcription(
                principal, song_id, AnalysisStatus.FAILED, error=str(exc)
            )

        song = self._songs.apply_transcript(
            principal,
            song_id,
            transcription,
            transcriber=self.transcriber_name,
            source_key=song.master_key,
            source_sha256=hashlib.sha256(data).hexdigest(),
        )
        log.info(
            "transcribed %s — %s lines, language %s",
            song_id,
            len(transcription.lines),
            transcription.language or "undetected",
        )
        return song
