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

    def delete(self, principal: Principal, identity_id: str) -> None:
        """Remove an identity version and its reference frames.

        Frames are uploaded images in the bucket, and an identity is the one entity a
        label makes several versions of — so orphaned frames accumulate faster here than
        anywhere else. Deleting a version does not touch kits already generated from it:
        their manifests record the identity id and version they used, and that record is
        supposed to outlive the thing it describes.
        """
        identity = self.get(principal, identity_id)
        for frame in identity.reference_frames:
            key = getattr(frame, "key", None)
            if not key:
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
