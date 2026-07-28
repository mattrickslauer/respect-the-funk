"""Analysis — measure an uploaded master, once, and keep what it found.

Same split as kits, for the same reason: `request()` is what an HTTP handler calls and
must stay fast, `run()` is what the worker calls and may take a minute on a long file.
Decoding, a full-track comb fit and a per-beat pass over the spectrum are not work an
HTTP request holds (§2b rule 3), so this is a queued job like everything else — inline on
a laptop, SQS + Batch in the deployment, no code difference either side.

The gates before anything is enqueued are the mirror of the kit service's:

1. **A master, and the bytes really there.** `register_master` checks storage before the
   key is recorded, so a song claiming a master it does not have cannot reach this.
2. **An analyser that can actually run.** A deployment without numpy refuses here, with
   the missing piece named, rather than queueing a job that will fail in a container
   nobody is watching.

`run()` is idempotent in the way that matters: analysing the same bytes twice produces the
same sections and replaces the previous measured ones, so a redelivered message costs a
minute of CPU and changes nothing (§2b rule 4).
"""

from __future__ import annotations

import hashlib
import logging

from remixkit.auth.provider import Principal
from remixkit.domain.models import AnalysisStatus, Song
from remixkit.ports.audio import AudioAnalyzer
from remixkit.ports.queue import JobQueue
from remixkit.ports.storage import Storage
from remixkit.services.errors import AnalysisUnavailable, Conflict, ServiceError
from remixkit.services.songs import SongService

log = logging.getLogger(__name__)

JOB_TYPE = "analyze-song"


class AnalysisService:
    def __init__(
        self,
        songs: SongService,
        storage: Storage,
        queue: JobQueue,
        analyzer: AudioAnalyzer,
    ) -> None:
        self._songs = songs
        self._storage = storage
        self._queue = queue
        self._analyzer = analyzer

    @property
    def available(self) -> bool:
        return bool(getattr(self._analyzer, "available", False))

    @property
    def unavailable_reason(self) -> str:
        return getattr(self._analyzer, "unavailable_reason", "")

    @property
    def analyzer_name(self) -> str:
        return getattr(self._analyzer, "name", "unknown")

    # -- the fast path ----------------------------------------------------------
    def request(self, principal: Principal, song_id: str) -> Song:
        """Queue a measurement of this song's master. Must stay well under 200 ms."""
        song = self._songs.get(principal, song_id)
        if not song.master_key:
            raise Conflict(
                "There is no master to analyse. Upload the audio first — analysis reads "
                "the file, not the metadata."
            )
        if not self.available:
            raise AnalysisUnavailable(self.unavailable_reason)
        if not getattr(self._queue, "can_execute", True):
            raise Conflict(
                getattr(self._queue, "unavailable_reason", "The job queue cannot execute jobs.")
            )

        song = self._songs.mark_analysis(
            principal, song_id, AnalysisStatus.QUEUED, analyzer=self.analyzer_name
        )
        self._queue.enqueue(
            JOB_TYPE,
            {"tenant_id": principal.tenant_id, "song_id": song.id},
            # Prefixed rather than the bare song id: kits and analyses share one inline
            # queue's in-flight set, and a song id colliding with nothing today is not a
            # property worth relying on.
            dedupe_key=f"{JOB_TYPE}:{song.id}",
        )
        return song

    # -- the slow path (worker) -------------------------------------------------
    def run(self, tenant_id: str, song_id: str) -> Song:
        """Decode, measure, write. Called by the queue consumer, never by a handler."""
        principal = Principal(tenant_id=tenant_id, subject="worker")
        song = self._songs.get(principal, song_id)
        if not song.master_key:
            return self._songs.mark_analysis(
                principal, song_id, AnalysisStatus.FAILED, error="No master to analyse."
            )

        self._songs.mark_analysis(
            principal, song_id, AnalysisStatus.RUNNING, analyzer=self.analyzer_name
        )
        try:
            data = self._storage.get(song.master_key)
            analysis = self._analyzer.analyze(
                data,
                filename=song.master_key,
                # The song's own drop, when a person recorded one. A track can have more
                # than one and only a person knows which the release is about — the
                # analyser searches near this rather than taking the largest step anywhere.
                drop_ms=song.drop_ms,
            )
        except ServiceError as exc:
            log.warning("analysis of %s refused: %s", song_id, exc)
            return self._songs.mark_analysis(
                principal, song_id, AnalysisStatus.FAILED, error=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - the job records its own failure
            log.exception("analysis of %s failed", song_id)
            return self._songs.mark_analysis(
                principal, song_id, AnalysisStatus.FAILED, error=str(exc)
            )

        song = self._songs.apply_analysis(
            principal,
            song_id,
            analysis,
            analyzer=self.analyzer_name,
            source_key=song.master_key,
            source_sha256=hashlib.sha256(data).hexdigest(),
        )
        log.info(
            "analysed %s — %s sections, %s BPM, drop %s",
            song_id,
            len(analysis.sections),
            analysis.bpm,
            analysis.drop_ms,
        )
        return song
