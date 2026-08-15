"""What is missing — the label's actual daily question.

Every other service answers "what is this record". This one answers "which records are
not yet usable", which is the question a release calendar creates: a song cannot be
generated from until it has a measurement, a hook window, and an artist whose likeness
consent is on file. Those preconditions live on three different collections, so nothing
that reads one collection can see them together.

The wireframe's `catalogue.json` records this as gap #2 — "there is no catalogue view;
'which songs have no approved kit' is the label's actual daily question and it spans
collections."

The banner on that screen carries a warning worth keeping honest: answering these by
scanning every record is what a real implementation must avoid. This one *does* scan,
and that is a deliberate, bounded choice — the document repository has no index and a
label's catalogue is hundreds of songs, not millions. The scan is linear in the tenant's
own records and runs on a page nobody loads in a loop. If a tenant ever makes this slow,
the fix is an index, not a rewrite of the questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from remixkit.domain.models import ApprovalState, Artist, Kit, Song
from remixkit.services.artists import ArtistService
from remixkit.services.kits import KitService
from remixkit.services.songs import SongService
from remixkit.auth.provider import Principal


@dataclass(frozen=True)
class SongGaps:
    """One song, with each precondition marked met or missing.

    `blocked_on` is ordered by what a person would fix first, and is what the row
    renders. Empty means the song is ready to generate from.
    """

    song: Song
    artist: Artist | None
    measured: bool
    has_hook: bool
    has_master: bool
    consent_ok: bool
    approved_kit: bool
    kit_count: int

    @property
    def blocked_on(self) -> list[str]:
        reasons: list[str] = []
        if not self.consent_ok:
            reasons.append("likeness consent")
        if not self.measured:
            reasons.append("measurement")
        if not self.has_hook:
            reasons.append("hook window")
        if not self.has_master:
            reasons.append("master")
        return reasons

    @property
    def ready(self) -> bool:
        """Ready to *generate* — which is not the same as having shipped anything."""
        return not self.blocked_on

    @property
    def complete(self) -> bool:
        return self.ready and self.approved_kit


@dataclass(frozen=True)
class Catalogue:
    rows: list[SongGaps] = field(default_factory=list)

    # Counts are derived rather than stored so they can never disagree with the table
    # underneath them — a dashboard whose headline contradicts its own rows is worse
    # than one with no headline.
    @property
    def unmeasured(self) -> int:
        return sum(1 for r in self.rows if not r.measured)

    @property
    def no_hook(self) -> int:
        return sum(1 for r in self.rows if not r.has_hook)

    @property
    def no_approved_kit(self) -> int:
        return sum(1 for r in self.rows if not r.approved_kit)

    @property
    def blocked_artists(self) -> int:
        """Artists, not songs — a consent gap blocks the whole roster entry at once, and
        counting songs would overstate how many decisions a person has to make."""
        return len({r.artist.id for r in self.rows if r.artist and not r.consent_ok})

    @property
    def ready(self) -> int:
        return sum(1 for r in self.rows if r.ready)


class CatalogueService:
    def __init__(
        self, *, artists: ArtistService, songs: SongService, kits: KitService
    ) -> None:
        self._artists = artists
        self._songs = songs
        self._kits = kits

    def gaps(self, principal: Principal, *, artist_id: str | None = None) -> Catalogue:
        """Three collection reads, then joins in memory.

        Three reads rather than one per song: a per-song lookup would make this O(songs)
        round trips against the repository, which is the shape that turns a 200-song
        catalogue into a page that times out.
        """
        artists = {a.id: a for a in self._artists.list(principal)}
        songs = self._songs.list(principal)
        kits = self._kits.list(principal)

        if artist_id:
            songs = [s for s in songs if s.artist_id == artist_id]

        by_song: dict[str, list[Kit]] = {}
        for kit in kits:
            by_song.setdefault(kit.song_id, []).append(kit)

        rows = [self._row(song, artists.get(song.artist_id), by_song.get(song.id, [])) for song in songs]
        # Blocked songs first, then by title: the point of the screen is the work queue,
        # so the rows needing attention must not be below the fold.
        rows.sort(key=lambda r: (r.ready, r.song.title.lower()))
        return Catalogue(rows=rows)

    @staticmethod
    def _row(song: Song, artist: Artist | None, kits: list[Kit]) -> SongGaps:
        return SongGaps(
            song=song,
            artist=artist,
            measured=song.bpm is not None and bool(song.bpm_method),
            # Either shape counts: the primary window, or a marked loopable section. A
            # song whose hooks are all in the sections list is not missing a hook.
            has_hook=song.has_hook,
            has_master=bool(song.master_key),
            # No artist record is treated as blocked rather than fine. A song whose
            # artist cannot be read is not a song anyone should generate from.
            consent_ok=bool(artist and not artist.consent.blocks_generation),
            approved_kit=any(
                k.approval in (ApprovalState.APPROVED, ApprovalState.PUBLISHED) for k in kits
            ),
            kit_count=len(kits),
        )
