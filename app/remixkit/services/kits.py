"""Kits — enqueue, run, approve.

The split matters: `request()` is what an HTTP handler calls and it must stay fast, and
`run()` is what the worker calls and it may take minutes. They are separate methods on
purpose so the worker container can import this module and run a kit with no web
framework in the picture at all.

Two gates before anything is enqueued:

1. **Likeness consent.** A kit that holds an artist's face invariant without recorded
   rights does not get queued. This is PRODUCT.md gap #3 inverted correctly: for a
   label's own signed artists you want explicit, auditable rights so the face *can* be
   held invariant — and the refusal has to be in code, not convention.
2. **Budget before enqueue, never mid-run.** BUILD-SPEC §6 carries the finding that
   per-tenant hard caps mid-run are unreliable, so the estimate is checked here, up
   front, where refusing is free.
"""

from __future__ import annotations

import logging

from remixkit.auth.provider import Principal
from remixkit.domain.models import ApprovalState, Kit, KitStatus, Modality
from remixkit.ports.generator import GenerationRequest, Generator, ShotSpec
from remixkit.ports.queue import JobQueue
from remixkit.ports.repository import DocumentRepository
from remixkit.services.artists import ArtistService
from remixkit.services.briefs import default_shot_plan, hook_windows
from remixkit.services.errors import Conflict, NotFound, RightsError
from remixkit.services.identities import IdentityService
from remixkit.services.songs import SongService

log = logging.getLogger(__name__)

COLLECTION = "kits"
JOB_TYPE = "run-kit"

# Illustrative planning prices, dated and editable — "price it, date it" (BUILD-SPEC §6).
# Estimates only; the ledger records what the provider actually reported.
ESTIMATE_CENTS: dict[Modality, int] = {Modality.VIDEO: 42, Modality.IMAGE: 3, Modality.AUDIO: 2}


class KitService:
    def __init__(
        self,
        repo: DocumentRepository,
        queue: JobQueue,
        generator: Generator,
        artists: ArtistService,
        identities: IdentityService,
        songs: SongService,
        *,
        max_shots: int = 8,
    ) -> None:
        self._repo = repo
        self._queue = queue
        self._generator = generator
        self._artists = artists
        self._identities = identities
        self._songs = songs
        self._max_shots = max_shots

    # -- reads ------------------------------------------------------------------
    def list(self, principal: Principal, *, artist_id: str | None = None) -> list[Kit]:
        kits = self._repo.list(principal.tenant_id, COLLECTION, Kit)
        if artist_id:
            kits = [k for k in kits if k.artist_id == artist_id]
        return sorted(kits, key=lambda k: k.created_at, reverse=True)

    def get(self, principal: Principal, kit_id: str) -> Kit:
        kit = self._repo.get(principal.tenant_id, COLLECTION, kit_id, Kit)
        if kit is None:
            raise NotFound(f"No kit {kit_id!r}")
        return kit

    @staticmethod
    def estimate_cents(shots: list[ShotSpec]) -> int:
        return sum(ESTIMATE_CENTS.get(s.modality, 0) for s in shots)

    # -- the fast path ----------------------------------------------------------
    def request(
        self,
        principal: Principal,
        *,
        song_id: str,
        name: str | None = None,
        video_count: int = 3,
        hook_lines: list[str] | None = None,
        tts_text: str | None = None,
        budget_cents: int | None = None,
        section_ids: list[str] | None = None,
    ) -> Kit:
        """Validate, persist a queued kit, enqueue. Must stay well under 200 ms."""
        song = self._songs.get(principal, song_id)
        artist = self._artists.get(principal, song.artist_id)

        if artist.consent.blocks_generation:
            raise RightsError(
                f"{artist.name} has no recorded likeness consent. Record it on the artist "
                "before generating — a kit holds the artist's face invariant, and that "
                "needs auditable rights."
            )

        identity = self._identities.current(principal, artist.id)
        # Sections are resolved against the song *now*, so a brief cannot record the id of
        # a section that was deleted before the request. The worker re-plans from the same
        # stored ids, and `briefs.hook_windows` drops any that have gone since.
        section_ids = [sid for sid in (section_ids or []) if song.section(sid)]
        shots = default_shot_plan(
            song,
            identity,
            video_count=video_count,
            hook_lines=hook_lines,
            tts_text=tts_text,
            max_shots=self._max_shots,
            section_ids=section_ids,
        )
        if not shots:
            raise Conflict("That brief produces no shots. Ask for at least one video or lyric card.")

        # A queue that cannot execute must say so before a kit row is written, not
        # leave one sitting at `queued` forever.
        if not getattr(self._queue, "can_execute", True):
            raise Conflict(getattr(self._queue, "unavailable_reason", "The job queue cannot execute jobs."))

        estimate = self.estimate_cents(shots)
        if budget_cents is not None and estimate > budget_cents:
            raise Conflict(
                f"Estimated {estimate}¢ exceeds the {budget_cents}¢ budget for this kit. "
                "Reduce the shot count or raise the budget."
            )

        kit = Kit(
            tenant_id=principal.tenant_id,
            artist_id=artist.id,
            song_id=song.id,
            name=(name or f"{song.title} — kit").strip(),
            status=KitStatus.QUEUED,
            brief={
                "video_count": video_count,
                "hook_lines": hook_lines or [],
                "tts_text": tts_text,
                "section_ids": section_ids,
                # The windows as they were when the kit was bought. The ids alone would
                # let an edited section change what a finished kit claims it was cut to,
                # and the whole provenance argument is that a delivered asset's record
                # does not move under it.
                "hook_windows": [
                    {"name": name, "start_ms": window.start_ms, "end_ms": window.end_ms}
                    for name, window in hook_windows(song, section_ids)
                ],
                "shot_count": len(shots),
                "estimate_cents": estimate,
                "identity_id": identity.id if identity else None,
                "identity_version": identity.version if identity else None,
                "generator": getattr(self._generator, "name", "unknown"),
            },
        )
        self._repo.put(principal.tenant_id, COLLECTION, kit.id, kit)

        # The dedupe key is the kit id: redelivery is a no-op (§2b rule 4).
        self._queue.enqueue(
            JOB_TYPE,
            {"tenant_id": principal.tenant_id, "kit_id": kit.id},
            dedupe_key=kit.id,
        )
        return kit

    # -- the slow path (worker) -------------------------------------------------
    def run(self, tenant_id: str, kit_id: str) -> Kit:
        """Execute the kit. Called by the queue consumer, never by a request handler."""
        kit = self._repo.get(tenant_id, COLLECTION, kit_id, Kit)
        if kit is None:
            raise NotFound(f"No kit {kit_id!r}")

        # Idempotency: a finished kit that is redelivered costs nothing (§2b rule 4).
        if kit.status is KitStatus.READY:
            log.info("kit %s already ready — redelivery is a no-op", kit_id)
            return kit

        kit.status = KitStatus.RUNNING
        kit.touch()
        self._repo.put(tenant_id, COLLECTION, kit.id, kit)

        principal = Principal(tenant_id=tenant_id, subject="worker")
        try:
            song = self._songs.get(principal, kit.song_id)
            artist = self._artists.get(principal, kit.artist_id)
            identity = self._identities.current(principal, artist.id)

            shots = default_shot_plan(
                song,
                identity,
                video_count=int(kit.brief.get("video_count", 3)),
                hook_lines=list(kit.brief.get("hook_lines") or []),
                tts_text=kit.brief.get("tts_text"),
                max_shots=self._max_shots,
                section_ids=list(kit.brief.get("section_ids") or []),
            )

            result = self._generator.generate(
                GenerationRequest(
                    kit_id=kit.id,
                    tenant_id=tenant_id,
                    artist_name=artist.name,
                    identity=identity,
                    song=song,
                    shots=shots,
                    metadata={"kit_name": kit.name},
                )
            )

            kit.run_id = result.run_id
            kit.assets = result.assets
            kit.manifest_key = result.manifest_key
            kit.manifest_verified = result.manifest_verified
            kit.error = result.error
            kit.recost()

            # `ready` requires assets *and* a verified manifest. An unverified manifest
            # means the provenance claim is unsupported, and the disclosure argument
            # is the product — so it fails rather than shipping a claim we cannot back.
            if result.assets and result.manifest_verified:
                kit.status = KitStatus.READY
            else:
                kit.status = KitStatus.FAILED
                if not kit.error:
                    kit.error = (
                        "No assets were produced."
                        if not result.assets
                        else "Manifest verification failed — provenance cannot be asserted."
                    )
        except Exception as exc:
            log.exception("kit %s failed", kit_id)
            kit.status = KitStatus.FAILED
            kit.error = str(exc)

        kit.touch()
        self._repo.put(tenant_id, COLLECTION, kit.id, kit)
        return kit

    # -- editorial --------------------------------------------------------------
    def set_approval(self, principal: Principal, kit_id: str, state: ApprovalState) -> Kit:
        """The human gate. A label does not auto-publish AI content about its artists."""
        kit = self.get(principal, kit_id)
        if state is not ApprovalState.DRAFT and kit.status is not KitStatus.READY:
            raise Conflict("Only a kit that generated cleanly can be approved.")
        kit.approval = state
        kit.touch()
        self._repo.put(principal.tenant_id, COLLECTION, kit.id, kit)
        return kit
