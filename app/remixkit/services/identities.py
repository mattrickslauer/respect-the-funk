"""The identity — "build it once" made operational.

MEMORY-SPEC's economic claim is that the second video for an artist is cheap because
this exists. So the service is versioned: editing an identity mints a new version
rather than mutating the old one, because a kit generated last week was generated
against a specific description of how the artist reads on screen, and losing that
makes the run unreproducible.
"""

from __future__ import annotations

import logging

from remixkit.auth.provider import Principal
from remixkit.domain.models import (
    ApprovalState,
    BodyBuild,
    HeightBand,
    Identity,
    Presentation,
    ReferenceFrame,
)
from remixkit.ports.repository import DocumentRepository
from remixkit.ports.storage import Storage
from remixkit.services.errors import NotFound

log = logging.getLogger(__name__)

COLLECTION = "identities"


class IdentityService:
    def __init__(self, repo: DocumentRepository, storage: Storage) -> None:
        self._repo = repo
        self._storage = storage

    def list_for_artist(self, principal: Principal, artist_id: str) -> list[Identity]:
        identities = [
            i
            for i in self._repo.list(principal.tenant_id, COLLECTION, Identity)
            if i.artist_id == artist_id
        ]
        return sorted(identities, key=lambda i: i.version, reverse=True)

    def current(self, principal: Principal, artist_id: str) -> Identity | None:
        """The highest version. What generation uses unless told otherwise."""
        identities = self.list_for_artist(principal, artist_id)
        return identities[0] if identities else None

    def get(self, principal: Principal, identity_id: str) -> Identity:
        identity = self._repo.get(principal.tenant_id, COLLECTION, identity_id, Identity)
        if identity is None:
            raise NotFound(f"No identity {identity_id!r}")
        return identity

    def create_version(
        self,
        principal: Principal,
        artist_id: str,
        *,
        structural_features: str | None = None,
        wardrobe: list[str] | None = None,
        negatives: list[str] | None = None,
        reference_frames: list[ReferenceFrame] | None = None,
        presentation: Presentation | None = None,
        build: BodyBuild | None = None,
        height: HeightBand | None = None,
    ) -> Identity:
        previous = self.current(principal, artist_id)

        def carried(given, attribute, empty):
            """A value the caller omitted is inherited; one they cleared is honoured.

            The distinction matters for the three standardised fields specifically. They
            are enums with an `UNSPECIFIED` member, so "not sent" and "set back to
            unspecified" are different intentions that arrive as different values —
            `None` and the member. Collapsing them would make the silhouette fields the
            only ones on this form that cannot be un-set.
            """
            if given is not None:
                return given
            return getattr(previous, attribute) if previous else empty

        identity = Identity(
            tenant_id=principal.tenant_id,
            artist_id=artist_id,
            version=(previous.version + 1) if previous else 1,
            structural_features=structural_features or (previous.structural_features if previous else None),
            wardrobe=wardrobe if wardrobe is not None else (previous.wardrobe if previous else []),
            negatives=negatives if negatives is not None else (previous.negatives if previous else []),
            reference_frames=(
                reference_frames
                if reference_frames is not None
                else (previous.reference_frames if previous else [])
            ),
            presentation=carried(presentation, "presentation", Presentation.UNSPECIFIED),
            build=carried(build, "build", BodyBuild.UNSPECIFIED),
            height=carried(height, "height", HeightBand.UNSPECIFIED),
        )
        self._repo.put(principal.tenant_id, COLLECTION, identity.id, identity)
        return identity

    def add_reference_frame(
        self, principal: Principal, identity_id: str, frame: ReferenceFrame
    ) -> Identity:
        """Frames land on the current version in place.

        Adding evidence of how the artist looks is not a change of intent, so it does
        not mint a version — unlike editing the description, which is.
        """
        identity = self.get(principal, identity_id)
        identity.reference_frames.append(frame)
        identity.touch()
        self._repo.put(principal.tenant_id, COLLECTION, identity.id, identity)
        return identity

    def remove_reference_frame(
        self, principal: Principal, identity_id: str, index: int
    ) -> Identity:
        """Drop one frame, and the object behind it.

        By position, for the same reason `briefs.apply_overrides` keys on position: the
        list is rendered from this order, so index N means the same frame to the screen
        that drew it and to the handler that removes it. The alternative — keying on the
        object key — is wrong here in a way that is easy to miss: the key is derived from
        the content digest alone, so the same photograph uploaded once as `front` and once
        as `profile-left` is two frames sharing one key, and deleting by key would take
        both.

        Removing a frame does not mint a version, matching `add_reference_frame`. Frames
        are evidence of how the artist looks rather than a statement of intent, and a
        mis-classified upload that costs a version to correct is one nobody corrects.

        The object goes with the row. An orphaned still is a bucket paying for something
        no screen can reach — the same rule `KitService.delete` follows.
        """
        identity = self.get(principal, identity_id)
        if not 0 <= index < len(identity.reference_frames):
            raise NotFound(f"No reference frame at position {index}")

        frame = identity.reference_frames.pop(index)
        # Only if nothing else still points at those bytes. Two classes of the same
        # photograph share a key, so deleting the object on the first removal would break
        # the image the second one still renders.
        if frame.key and not any(f.key == frame.key for f in identity.reference_frames):
            try:
                self._storage.delete(frame.key)
            except Exception as exc:
                log.warning("identity %s: could not delete frame %s (%s)", identity_id, frame.key, exc)

        identity.touch()
        self._repo.put(principal.tenant_id, COLLECTION, identity.id, identity)
        return identity

    def restore_version(self, principal: Principal, identity_id: str) -> Identity:
        """Make an older version current again — by copying it forward, never by reviving.

        A restore is a new version whose content is an old one's. Rewinding in place would
        mean a kit generated last week points at a version number whose content has since
        changed underneath it, which is exactly the failure the versioning exists to
        prevent. So history only ever grows, and "we went back to v2" is itself a
        recorded event rather than an erasure.

        Reference frames are copied by reference, not by value — they are immutable rows
        pointing at immutable objects, so both versions can name the same still without
        either owning it. `delete` is the one place that has to know this.
        """
        source = self.get(principal, identity_id)
        return self.create_version(
            principal,
            source.artist_id,
            structural_features=source.structural_features,
            wardrobe=list(source.wardrobe),
            negatives=list(source.negatives),
            reference_frames=list(source.reference_frames),
            presentation=source.presentation,
            build=source.build,
            height=source.height,
        )

    def delete(self, principal: Principal, identity_id: str) -> None:
        """Remove an identity version and its reference frames.

        Frames are uploaded images in the bucket, and an identity is the one entity a
        label makes several versions of — so orphaned frames accumulate faster here than
        anywhere else. Deleting a version does not touch kits already generated from it:
        their manifests record the identity id and version they used, and that record is
        supposed to outlive the thing it describes.
        """
        identity = self.get(principal, identity_id)

        # Only the objects no *surviving* version still points at.
        #
        # This was safe when every version owned its own uploads. `restore_version` copies
        # reference frames forward by reference — two versions naming one immutable still —
        # so deleting a version's objects unconditionally would blank the frames of the
        # version it was restored from, or restored into. Nothing would error; the rows
        # would remain and the images would 404, which is the shape of data loss that is
        # hardest to notice.
        survivors = {
            frame.key
            for other in self.list_for_artist(principal, identity.artist_id)
            if other.id != identity_id
            for frame in other.reference_frames
            if frame.key
        }

        for frame in identity.reference_frames:
            key = getattr(frame, "key", None)
            if not key or key in survivors:
                continue
            try:
                self._storage.delete(key)
            except Exception as exc:
                log.warning("identity %s: could not delete frame %s (%s)", identity_id, key, exc)
        self._repo.delete(principal.tenant_id, COLLECTION, identity_id)
        log.info("identity %s deleted", identity_id)

    def set_approval(self, principal: Principal, identity_id: str, state: ApprovalState) -> Identity:
        identity = self.get(principal, identity_id)
        identity.approval = state
        identity.touch()
        self._repo.put(principal.tenant_id, COLLECTION, identity.id, identity)
        return identity
