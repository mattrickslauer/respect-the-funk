"""Artist roster — register, list, update, and record likeness consent.

PRODUCT.md's gap #1 closed: the artist is an entity that owns an identity, songs, and
rights, rather than a string on a song.
"""

from __future__ import annotations

import logging

from typing import Callable

from remixkit.auth.provider import Principal
from remixkit.domain.models import (
    ApprovalState,
    Artist,
    Identity,
    Kit,
    LikenessConsent,
    Song,
    slugify,
    utcnow,
)
from remixkit.ports.repository import DocumentRepository
from remixkit.services.errors import Conflict, NotFound

log = logging.getLogger(__name__)

COLLECTION = "artists"


class ArtistService:
    def __init__(self, repo: DocumentRepository) -> None:
        self._repo = repo
        # Set by the composition root. Cascading has to delete songs, identities and kits
        # through *their* services so each cleans up its own bucket objects — but those
        # services import this one, so importing them back would be a cycle. A callback
        # injected by `deps` keeps the dependency pointing one way.
        self._cascade: Callable[[Principal, str], None] | None = None

    def on_cascade(self, fn: "Callable[[Principal, str], None]") -> None:
        """Register how to remove an artist's dependents. See `deps.Container`."""
        self._cascade = fn

    def list(self, principal: Principal) -> list[Artist]:
        artists = self._repo.list(principal.tenant_id, COLLECTION, Artist)
        return sorted(artists, key=lambda a: a.name.lower())

    def get(self, principal: Principal, artist_id: str) -> Artist:
        artist = self._repo.get(principal.tenant_id, COLLECTION, artist_id, Artist)
        if artist is None:
            raise NotFound(f"No artist {artist_id!r}")
        return artist

    def create(
        self,
        principal: Principal,
        *,
        name: str,
        bio: str | None = None,
        links: dict[str, str] | None = None,
    ) -> Artist:
        name = name.strip()
        if not name:
            raise Conflict("An artist needs a name.")

        slug = slugify(name)
        if any(a.slug == slug for a in self._repo.list(principal.tenant_id, COLLECTION, Artist)):
            raise Conflict(f"An artist with the handle {slug!r} already exists.")

        artist = Artist(
            tenant_id=principal.tenant_id,
            slug=slug,
            name=name,
            bio=bio or None,
            links={k: v for k, v in (links or {}).items() if v},
        )
        self._repo.put(principal.tenant_id, COLLECTION, artist.id, artist)
        return artist

    def update(
        self,
        principal: Principal,
        artist_id: str,
        *,
        name: str | None = None,
        bio: str | None = None,
        links: dict[str, str] | None = None,
    ) -> Artist:
        artist = self.get(principal, artist_id)
        if name and name.strip():
            artist.name = name.strip()
        if bio is not None:
            artist.bio = bio or None
        if links is not None:
            artist.links = {k: v for k, v in links.items() if v}
        artist.touch()
        self._repo.put(principal.tenant_id, COLLECTION, artist.id, artist)
        return artist

    def set_consent(
        self,
        principal: Principal,
        artist_id: str,
        *,
        granted: bool,
        signed_by: str | None = None,
        notes: str | None = None,
    ) -> Artist:
        """Record (or withdraw) likeness rights.

        Withdrawal is deliberately as easy as granting, and takes effect on the next
        kit rather than retroactively — consent that cannot be revoked is not consent,
        and pretending already-generated assets vanish would be a lie in the audit trail.
        """
        artist = self.get(principal, artist_id)
        artist.consent = LikenessConsent(
            granted=granted,
            signed_by=(signed_by or None) if granted else None,
            signed_at=utcnow() if granted else None,
            document_key=artist.consent.document_key,
            notes=notes or None,
        )
        artist.touch()
        self._repo.put(principal.tenant_id, COLLECTION, artist.id, artist)
        return artist

    def set_approval(self, principal: Principal, artist_id: str, state: ApprovalState) -> Artist:
        artist = self.get(principal, artist_id)
        artist.approval = state
        artist.touch()
        self._repo.put(principal.tenant_id, COLLECTION, artist.id, artist)
        return artist

    def dependents(self, principal: Principal, artist_id: str) -> dict[str, int]:
        """What else would be orphaned by deleting this artist.

        Read through the repository rather than through `SongService`/`KitService`,
        because those import this module and the reverse import would be a cycle. The
        collection names are literals for the same reason; they are asserted against the
        services' own constants in `tests/test_crud`.
        """
        tenant = principal.tenant_id
        return {
            "songs": sum(
                1 for s in self._repo.list(tenant, "songs", Song) if s.artist_id == artist_id
            ),
            "identities": sum(
                1 for i in self._repo.list(tenant, "identities", Identity) if i.artist_id == artist_id
            ),
            "kits": sum(
                1 for k in self._repo.list(tenant, "kits", Kit) if k.artist_id == artist_id
            ),
        }

    def delete(self, principal: Principal, artist_id: str, *, cascade: bool = False) -> dict[str, int]:
        """Remove an artist, refusing by default if anything still points at them.

        The old implementation deleted the row and nothing else, which left songs and kits
        referencing an artist that no longer existed — a roster that looks clean while
        `kits.request` 404s on the artist lookup and the catalogue page counts songs
        nobody can open. Silent orphaning is the worst of the three options.

        So: refuse, and say what is in the way. `cascade` is the caller saying delete it
        all, which the console asks about by name and count rather than behind a generic
        "are you sure". Cascading goes through the owning services so that each dependent
        cleans up its own bucket objects — masters, frames, generated assets, manifests.
        """
        artist = self.get(principal, artist_id)  # 404 rather than a silent no-op
        counts = self.dependents(principal, artist_id)
        total = sum(counts.values())

        if total and not cascade:
            detail = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
            raise Conflict(
                f"{artist.name} still has {detail}. Deleting the artist would leave those "
                "pointing at nobody. Delete them first, or send cascade to remove them together."
            )

        if cascade and self._cascade is not None:
            self._cascade(principal, artist_id)

        self._repo.delete(principal.tenant_id, COLLECTION, artist_id)
        log.info("artist %s deleted (cascade=%s, dependents=%s)", artist_id, cascade, counts)
        return counts
