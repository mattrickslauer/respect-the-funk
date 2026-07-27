"""Artist roster — register, list, update, and record likeness consent.

PRODUCT.md's gap #1 closed: the artist is an entity that owns an identity, songs, and
rights, rather than a string on a song.
"""

from __future__ import annotations

from remixkit.auth.provider import Principal
from remixkit.domain.models import Artist, ApprovalState, LikenessConsent, slugify, utcnow
from remixkit.ports.repository import DocumentRepository
from remixkit.services.errors import Conflict, NotFound

COLLECTION = "artists"


class ArtistService:
    def __init__(self, repo: DocumentRepository) -> None:
        self._repo = repo

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

    def delete(self, principal: Principal, artist_id: str) -> None:
        self.get(principal, artist_id)  # 404 rather than a silent no-op
        self._repo.delete(principal.tenant_id, COLLECTION, artist_id)
