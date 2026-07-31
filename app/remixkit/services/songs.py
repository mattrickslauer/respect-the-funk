"""Songs — measured once, reused forever.

The one opinion this service holds: **a number is only accepted with the method that
produced it.** FORMAT-SPEC requires the provenance of a measurement, not just the number,
and PRODUCT.md names catalogue onboarding — not compute — as the real bottleneck. A field
that quietly accepts an unsourced 128 is how that bottleneck gets hidden instead of
measured.

That rule started on the BPM and now covers the arrangement. A song has as many hooks as
it has, each a `SongSection` with its own window and its own `Provenance` — `MANUAL` for a
window somebody drew, `MEASURED` plus a method line for one the analyser produced. The two
kinds live in one list because a person must be able to correct the analyser, and they are
distinguished by their source because "the analyser found this" and "I dragged it here"
are different claims that a screen must not render identically.
"""

from __future__ import annotations

import logging

from remixkit.auth.provider import Principal
from remixkit.domain.models import (
    AnalysisStatus,
    ApprovalState,
    HookWindow,
    LyricLine,
    Provenance,
    SectionRole,
    Song,
    SongAnalysis,
    SongBar,
    SongLyrics,
    SongSection,
    slugify,
    utcnow,
)
from remixkit.ports.audio import AudioAnalysis
from remixkit.ports.lyrics import Transcription
from remixkit.ports.repository import DocumentRepository
from remixkit.ports.storage import Storage
from remixkit.services.errors import Conflict, NotFound

log = logging.getLogger(__name__)

COLLECTION = "songs"

# What a browser may upload as a master, and the extension each one lands under. A closed
# list rather than "anything audio/*": the key's suffix is what tells the analyser's
# decoder what it is holding, and an unrecognised type would be stored as `.bin` and then
# fail to decode long after the upload appeared to succeed.
MASTER_TYPES: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/mp4": "m4a",
    "audio/aac": "m4a",
}


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

    # ---------------------------------------------------------------- sections
    def add_section(
        self,
        principal: Principal,
        song_id: str,
        *,
        start_ms: int,
        end_ms: int,
        role: SectionRole | str = SectionRole.HOOK,
        label: str = "",
        notes: str | None = None,
        make_primary: bool = False,
    ) -> Song:
        """Mark a stretch of the song by hand.

        The window is checked here rather than only in the model so the refusal is the
        product's sentence and not a pydantic traceback — an end before a start is the
        single most likely mistake on this form.
        """
        _check_window(start_ms, end_ms)
        song = self.get(principal, song_id)
        section = SongSection(
            role=_role(role),
            label=label.strip(),
            start_ms=start_ms,
            end_ms=end_ms,
            source=Provenance.MANUAL,
            notes=(notes or "").strip() or None,
        )
        song.sections.append(section)
        if make_primary or (section.is_hook_material and song.primary_section_id is None):
            _make_primary(song, section)
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def update_section(
        self,
        principal: Principal,
        song_id: str,
        section_id: str,
        *,
        role: SectionRole | str | None = None,
        label: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        notes: str | None = None,
    ) -> Song:
        """Correct a section — and record that a person did.

        Moving a measured window makes it no longer the analyser's claim, so the source
        flips to `MANUAL` and the old method line is kept as history. The alternative is a
        window somebody dragged still reading "measured", which is the precise failure the
        provenance rule exists to prevent.
        """
        song = self.get(principal, song_id)
        section = song.section(section_id)
        if section is None:
            raise NotFound(f"No section {section_id!r} on {song.title!r}")

        moved = False
        if start_ms is not None and start_ms != section.start_ms:
            section.start_ms, moved = start_ms, True
        if end_ms is not None and end_ms != section.end_ms:
            section.end_ms, moved = end_ms, True
        _check_window(section.start_ms, section.end_ms)

        if role is not None:
            section.role = _role(role)
        if label is not None:
            section.label = label.strip()
        if notes is not None:
            section.notes = notes.strip() or None
        if moved:
            _became_manual(section)

        if song.primary_section_id == section.id:
            song.hook = section.window
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def remove_section(self, principal: Principal, song_id: str, section_id: str) -> Song:
        song = self.get(principal, song_id)
        if song.section(section_id) is None:
            raise NotFound(f"No section {section_id!r} on {song.title!r}")
        song.sections = [s for s in song.sections if s.id != section_id]
        if song.primary_section_id == section_id:
            # The window itself stays. It is what every existing kit was cut to, and
            # zeroing it would change what the next kit renders as a side effect of
            # tidying up a list.
            song.primary_section_id = None
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def set_primary_section(self, principal: Principal, song_id: str, section_id: str) -> Song:
        """Choose which hook a kit cuts to when its brief names none."""
        song = self.get(principal, song_id)
        section = song.section(section_id)
        if section is None:
            raise NotFound(f"No section {section_id!r} on {song.title!r}")
        if section.duration_ms <= 0:
            raise Conflict("That section has no duration — it cannot be the hook.")
        _make_primary(song, section)
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def set_hook(self, principal: Principal, song_id: str, *, start_ms: int, end_ms: int) -> Song:
        """The primary hook window — Pillar 13's one free lever.

        Kept as its own call because it is the oldest surface in the console and the API
        contract other things already use. It now writes through to the sections list: the
        primary section moves with it, or one is created if the song has none, so the
        window on the song and the list of sections can never say different things.
        """
        if end_ms <= start_ms:
            raise Conflict("The hook window must end after it starts.")
        song = self.get(principal, song_id)
        section = song.primary_section
        if section is None:
            section = SongSection(
                role=SectionRole.HOOK,
                label="Hook",
                start_ms=start_ms,
                end_ms=end_ms,
                source=Provenance.MANUAL,
            )
            song.sections.append(section)
        else:
            moved = (section.start_ms, section.end_ms) != (start_ms, end_ms)
            section.start_ms, section.end_ms = start_ms, end_ms
            if moved:
                _became_manual(section)
        _make_primary(song, section)
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    # ---------------------------------------------------------------- the master
    def master_upload_url(self, principal: Principal, song_id: str, *, content_type: str) -> dict:
        """Presigned PUT — the master goes browser→bucket, never through this process.

        §2b rule 2. It is also the difference between a 128 MB WAV being a storage
        event and it being a request that has to fit in a Lambda body limit.

        The key is deliberately *not* recorded here. An upload that is presigned and then
        abandoned would leave the song claiming a master that does not exist, which reads
        as "ready to analyse" on every screen that asks. `register_master` runs after the
        bytes land, and checks that they did.
        """
        song = self.get(principal, song_id)
        suffix = MASTER_TYPES.get(content_type.split(";")[0].strip().lower())
        if suffix is None:
            raise Conflict(
                f"{content_type} is not an audio master. Upload a WAV, MP3, FLAC or M4A."
            )
        key = f"{self._prefix}/masters/{principal.tenant_id}/{song.id}.{suffix}"
        url = self._storage.presign_put(key, content_type=content_type)
        return {"url": url, "key": key, "method": "PUT", "content_type": content_type}

    def register_master(
        self, principal: Principal, song_id: str, *, key: str, content_type: str | None = None
    ) -> Song:
        """Record an uploaded master, after checking the bytes are actually there.

        The key is checked against the one this song would have been presigned rather than
        trusted from the request: it arrives from a browser, and a caller who could name
        any key could point a song at another tenant's object and have the console presign
        a download of it.
        """
        song = self.get(principal, song_id)
        expected = f"{self._prefix}/masters/{principal.tenant_id}/{song.id}."
        if not key.startswith(expected):
            raise Conflict("That object key does not belong to this song.")
        if not self._storage.exists(key):
            raise Conflict("No object at that key — the upload did not complete.")
        song.master_key = key
        song.master_content_type = content_type or song.master_content_type
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    # ---------------------------------------------------------------- analysis
    def mark_analysis(
        self,
        principal: Principal,
        song_id: str,
        status: AnalysisStatus,
        *,
        analyzer: str | None = None,
        error: str | None = None,
    ) -> Song:
        """Move the analysis through its states, so a screen can say which one it is in."""
        song = self.get(principal, song_id)
        current = song.analysis or SongAnalysis()
        current.status = status
        current.error = error
        if analyzer:
            current.analyzer = analyzer
        if status is AnalysisStatus.QUEUED:
            current.requested_at = utcnow()
            current.completed_at = None
        elif status in (AnalysisStatus.DONE, AnalysisStatus.FAILED):
            current.completed_at = utcnow()
        song.analysis = current
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def apply_analysis(
        self,
        principal: Principal,
        song_id: str,
        analysis: AudioAnalysis,
        *,
        analyzer: str,
        source_key: str | None = None,
        source_sha256: str | None = None,
    ) -> Song:
        """Write what the analyser found onto the song.

        Three rules, each of them a decision about whose claim wins:

        1. **Hand-drawn sections survive.** Only `MEASURED` sections are replaced. Somebody
           who marked a hook by ear does not lose it because a colleague re-ran the
           analyser.
        2. **A human tempo is not overwritten.** If the song already carries a BPM whose
           method is not this analyser's, the measured value is recorded on the analysis
           and the human number stays — with a warning when the two disagree. Silently
           replacing it makes the console's own history unreadable.
        3. **The primary hook is not chosen here.** Analysis proposes; a person disposes.
           `services.recommendations` ranks the candidates with their basis and the console
           applies one on a click. An analyser that quietly repointed the hook would change
           what every future kit renders without anybody deciding to.
        """
        song = self.get(principal, song_id)

        kept = [s for s in song.sections if s.source is not Provenance.MEASURED]
        fresh = [
            SongSection(
                role=item.role,
                label=item.label,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                source=Provenance.MEASURED,
                method=analysis.method,
                energy_low_band=item.energy_low_band,
                energy_rms_db=item.energy_rms_db,
                beats=item.beats,
                bar_start=item.bar_start,
                bar_end=item.bar_end,
                band_mix=list(item.band_mix),
                brightness_hz=item.brightness_hz,
                onset_density=item.onset_density,
                tonal_mid=item.tonal_mid,
                segment_type=item.segment_type,
                repeat_of=item.repeat_of,
                evidence=list(item.evidence),
                entry=item.entry or None,
            )
            for item in analysis.sections
            if item.end_ms > item.start_ms
        ]
        song.sections = kept + fresh

        warnings = list(analysis.warnings)
        recorded_by_hand = song.bpm is not None and not (song.bpm_method or "").startswith(analyzer)
        if analysis.bpm is not None:
            if not recorded_by_hand:
                song.bpm = analysis.bpm
                song.bpm_method = f"{analyzer}: {analysis.method}"
            # Half a BPM, not a percentage. At 125 BPM that is ~2ms per beat, which is
            # ~46ms across a 24-beat carousel — already past the 25ms `measure_beat.py`
            # calls off the grid, and a fixed offset never washes out.
            elif abs(song.bpm - analysis.bpm) > 0.5:
                warnings.append(
                    f"Measured {analysis.bpm:.2f} BPM; the song records {song.bpm:.2f} "
                    f"({song.bpm_method}). The recorded value was kept — change it on the "
                    "measurement form if the analyser is right."
                )
        if analysis.drop_ms is not None and song.drop_ms is None:
            song.drop_ms = analysis.drop_ms

        # A primary section that pointed at a measured window now replaced is re-pointed at
        # whichever fresh section covers the same moment, so "the hook" survives a re-run.
        if song.primary_section_id and song.section(song.primary_section_id) is None:
            song.primary_section_id = _best_overlap(fresh, song.hook)

        song.analysis = SongAnalysis(
            status=AnalysisStatus.DONE,
            analyzer=analyzer,
            method=analysis.method,
            source_key=source_key,
            source_sha256=source_sha256,
            requested_at=(song.analysis.requested_at if song.analysis else utcnow()),
            completed_at=utcnow(),
            duration_ms=analysis.duration_ms,
            bpm=analysis.bpm,
            beat_ms=analysis.beat_ms,
            downbeat_ms=analysis.downbeat_ms,
            drop_ms=analysis.drop_ms,
            riser_beats=analysis.riser_beats,
            plateau_beats=analysis.plateau_beats,
            bar_phase_confidence=analysis.bar_phase_confidence,
            warnings=warnings,
            bars=[SongBar(**vars(bar)) for bar in analysis.bars],
            sheet=analysis.sheet or None,
        )
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    # ---------------------------------------------------------------- lyrics
    def mark_transcription(
        self,
        principal: Principal,
        song_id: str,
        status: AnalysisStatus,
        *,
        transcriber: str | None = None,
        error: str | None = None,
    ) -> Song:
        """Move the transcription through its states, so a screen can say which one it is in.

        The mirror of `mark_analysis`, down to preserving the lines already there: a
        re-transcription that is queued and then fails must leave the previous transcript
        readable, because the alternative is a person losing a lyric they had corrected to
        a provider outage they had nothing to do with.
        """
        song = self.get(principal, song_id)
        current = song.lyrics or SongLyrics()
        current.status = status
        current.error = error
        if transcriber:
            current.transcriber = transcriber
        if status is AnalysisStatus.QUEUED:
            current.requested_at = utcnow()
            current.completed_at = None
        elif status in (AnalysisStatus.DONE, AnalysisStatus.FAILED):
            current.completed_at = utcnow()
        song.lyrics = current
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def apply_transcript(
        self,
        principal: Principal,
        song_id: str,
        transcription: Transcription,
        *,
        transcriber: str,
        source_key: str | None = None,
        source_sha256: str | None = None,
    ) -> Song:
        """Write what the transcriber heard onto the song.

        The one place this deliberately differs from `apply_analysis`: **hand-edited lines
        do not survive.** Sections are independent claims about separate stretches of a
        song, so a measured list and a hand-drawn one merge cleanly. A lyric is one
        continuous reading of the same vocal, and interleaving a corrected line from the
        old transcript with fresh lines from the new one produces a lyric that is neither —
        duplicated phrases, overlapping windows, and no way to tell which pass said what.

        So a re-run replaces the transcript, `services.transcription.request` refuses to
        start one that would discard corrections unless the caller says to anyway, and the
        count that was discarded is recorded here in `warnings` rather than only in a log
        line the person who lost the work will never read.
        """
        song = self.get(principal, song_id)
        previous = song.lyrics
        warnings = list(transcription.warnings)
        discarded = len(previous.edited_lines) if previous else 0
        if discarded:
            warnings.append(
                f"This transcript replaced {discarded} hand-corrected line(s) from the "
                "previous run. Corrections are not carried across a re-transcription."
            )

        song.lyrics = SongLyrics(
            status=AnalysisStatus.DONE,
            transcriber=transcriber,
            method=transcription.method,
            source_key=source_key,
            source_sha256=source_sha256,
            requested_at=(previous.requested_at if previous else utcnow()),
            completed_at=utcnow(),
            language=transcription.language,
            language_confidence=transcription.language_confidence,
            warnings=warnings,
            lines=[
                LyricLine(
                    text=line.text,
                    start_ms=line.start_ms,
                    end_ms=line.end_ms,
                    source=Provenance.MEASURED,
                    method=transcription.method,
                    confidence=line.confidence,
                )
                for line in transcription.lines
                if line.text.strip()
            ],
        )
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def add_lyric_line(
        self, principal: Principal, song_id: str, *, text: str, start_ms: int, end_ms: int
    ) -> Song:
        """Type a line the transcriber missed — or write the lyric with no transcriber at all.

        Works on a song that has never been transcribed, which is the point: a label that
        already has the lyric sheet should not have to run a provider to get it into the
        console, and a deployment with no credentials is not a deployment that cannot hold
        a lyric.
        """
        text = text.strip()
        if not text:
            raise Conflict("A lyric line needs words.")
        _check_lyric_window(start_ms, end_ms)
        song = self.get(principal, song_id)
        lyrics = song.lyrics or SongLyrics(status=AnalysisStatus.DONE, method=None)
        # A song whose lyric is entirely hand-written has nothing queued and nothing
        # running — saying `done` is what makes the panel read as a lyric rather than as a
        # job that never finished.
        if lyrics.status is AnalysisStatus.QUEUED and not lyrics.transcriber:
            lyrics.status = AnalysisStatus.DONE
        lyrics.lines.append(
            LyricLine(text=text, start_ms=start_ms, end_ms=end_ms, source=Provenance.MANUAL)
        )
        song.lyrics = lyrics
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def update_lyric_line(
        self,
        principal: Principal,
        song_id: str,
        line_id: str,
        *,
        text: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> Song:
        """Correct a line — and record that a person did.

        Same rule as `update_section`, and it bites harder here. A transcribed line that
        somebody rewrote is no longer the provider's claim, so the source flips to `MANUAL`
        and the confidence goes with it: a 0.42 next to words a person is certain of would
        be a machine's doubt about a sentence it never produced.
        """
        song = self.get(principal, song_id)
        lyrics = song.lyrics
        line = lyrics.line(line_id) if lyrics else None
        if lyrics is None or line is None:
            raise NotFound(f"No lyric line {line_id!r} on {song.title!r}")

        changed = False
        if text is not None and text.strip() != line.text:
            if not text.strip():
                raise Conflict("A lyric line needs words. Remove the line to delete it.")
            line.text, changed = text.strip(), True
        if start_ms is not None and start_ms != line.start_ms:
            line.start_ms, changed = start_ms, True
        if end_ms is not None and end_ms != line.end_ms:
            line.end_ms, changed = end_ms, True
        _check_lyric_window(line.start_ms, line.end_ms)

        if changed:
            _line_became_manual(line)
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def remove_lyric_line(self, principal: Principal, song_id: str, line_id: str) -> Song:
        song = self.get(principal, song_id)
        lyrics = song.lyrics
        if lyrics is None or lyrics.line(line_id) is None:
            raise NotFound(f"No lyric line {line_id!r} on {song.title!r}")
        lyrics.lines = [line for line in lyrics.lines if line.id != line_id]
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def set_lyrics_text(self, principal: Principal, song_id: str, text: str) -> Song:
        """Rewrite the whole lyric as a block of text — the way a person actually fixes one.

        Correcting a transcript line by line is right for one bad word and wrong for a
        chorus the model heard as gibberish, which is the common case. So this takes the
        lyric as text and re-pairs it with the windows already known:

        * the Nth new line inherits the Nth old line's window, because fixing words does
          not move when they were sung;
        * a line whose text is unchanged keeps its provenance and its confidence — nobody
          edited it, and marking it `manual` would erase a real measurement by association;
        * a line with no counterpart gets a zero window, which every screen renders as
          "no timing" rather than as a line at the start of the song.

        When the line *count* changes the pairing is a guess after the first insertion, and
        that is recorded as a warning rather than left for somebody to discover in a kit.
        """
        song = self.get(principal, song_id)
        previous = song.lyrics.ordered_lines if song.lyrics else []
        fresh = [line.strip() for line in text.splitlines()]
        fresh = [line for line in fresh if line]

        lines: list[LyricLine] = []
        for index, body in enumerate(fresh):
            old = previous[index] if index < len(previous) else None
            if old is not None and old.text == body:
                lines.append(old)
                continue
            lines.append(
                LyricLine(
                    text=body,
                    start_ms=old.start_ms if old else 0,
                    end_ms=old.end_ms if old else 0,
                    source=Provenance.MANUAL,
                    method=(
                        f"hand-edited (was: {old.method})"
                        if old and old.method
                        else "hand-written"
                    ),
                )
            )

        lyrics = song.lyrics or SongLyrics(status=AnalysisStatus.DONE)
        if lyrics.status is AnalysisStatus.QUEUED and not lyrics.transcriber:
            lyrics.status = AnalysisStatus.DONE
        if previous and len(fresh) != len(previous):
            lyrics.warnings = [
                w for w in lyrics.warnings if not w.startswith("The line count changed")
            ] + [
                f"The line count changed from {len(previous)} to {len(fresh)}. Windows are "
                "paired by position, so timings after the first inserted or deleted line "
                "may no longer match the audio."
            ]
        lyrics.lines = lines
        song.lyrics = lyrics
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def update(self, principal: Principal, song_id: str, *, title: str | None = None) -> Song:
        """Rename. Deliberately narrow: everything else about a song is *measured*.

        BPM, the drop, the sections and the lyric all carry a `method` recording where
        they came from, and a free-text edit form over them is exactly how a measured
        number turns into an unsourced one. Those have their own methods, each of which
        rewrites provenance honestly. A title is the one field with no measurement behind
        it, so it is the one field this edits.
        """
        song = self.get(principal, song_id)
        if title is not None:
            title = title.strip()
            if not title:
                raise Conflict("A song needs a title.")
            song.title = title
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song

    def delete(self, principal: Principal, song_id: str) -> None:
        """Remove a song and the master in the bucket that belongs to it.

        The master is the largest object this product stores — a whole uploaded WAV — so
        leaving it behind on delete is the most expensive orphan available. Callers that
        need to refuse when kits still point here do that check first; this is the
        unconditional removal.
        """
        song = self.get(principal, song_id)
        if song.master_key:
            try:
                self._storage.delete(song.master_key)
            except Exception as exc:
                log.warning("song %s: could not delete master %s (%s)", song_id, song.master_key, exc)
        self._repo.delete(principal.tenant_id, COLLECTION, song_id)
        log.info("song %s deleted", song_id)

    def set_approval(self, principal: Principal, song_id: str, state: ApprovalState) -> Song:
        song = self.get(principal, song_id)
        song.approval = state
        song.touch()
        self._repo.put(principal.tenant_id, COLLECTION, song.id, song)
        return song


# ---------------------------------------------------------------- helpers
def _role(value: SectionRole | str) -> SectionRole:
    try:
        return value if isinstance(value, SectionRole) else SectionRole(value)
    except ValueError as exc:
        allowed = ", ".join(r.value for r in SectionRole)
        raise Conflict(f"{value!r} is not a section role. One of: {allowed}.") from exc


def _check_window(start_ms: int, end_ms: int) -> None:
    if start_ms < 0:
        raise Conflict("A section cannot start before the song does.")
    if end_ms <= start_ms:
        raise Conflict("A section must end after it starts.")


def _check_lyric_window(start_ms: int, end_ms: int) -> None:
    """A lyric window, which unlike a section's is allowed to be unknown.

    `0–0` means "these are the words, nobody has said when" — the state a lyric typed off
    a sheet is in, and a legitimate one: the words are still correct and still usable. What
    is refused is a window that is *stated and impossible*, because that is a typo rather
    than an absence, and every screen renders the two differently.
    """
    if start_ms == 0 and end_ms == 0:
        return
    if start_ms < 0:
        raise Conflict("A lyric line cannot start before the song does.")
    if end_ms <= start_ms:
        raise Conflict(
            "A lyric line must end after it starts. Leave both at 0 if the timing is unknown."
        )


def _became_manual(section: SongSection) -> None:
    """A measured window somebody moved is a manual one, and says what it used to be.

    The features go with it — all of them. `services.recommendations` ranks on measured
    energy, and a figure that no longer describes the window it is attached to would let a
    section a person dragged rank as though the analyser still vouched for it. The same
    applies to the texture: `evidence` reading "top energy tier" under a window that has
    been moved somewhere quiet is worse than no evidence at all, because it is a sentence
    asserting a measurement of a stretch of audio nobody measured.

    `bar_start`/`bar_end` go too. A dragged window is at whatever millisecond somebody
    typed, and a bar number implies it is on the grid.
    """
    if section.source is not Provenance.MEASURED:
        return
    section.source = Provenance.MANUAL
    section.method = f"hand-edited (was: {section.method})" if section.method else "hand-edited"
    section.energy_low_band = None
    section.energy_rms_db = None
    section.beats = None
    section.bar_start = None
    section.bar_end = None
    section.band_mix = []
    section.brightness_hz = None
    section.onset_density = None
    section.tonal_mid = None
    section.segment_type = None
    section.repeat_of = None
    section.evidence = []
    section.entry = None


def _line_became_manual(line: LyricLine) -> None:
    """A transcribed line somebody changed is a manual one, and says what it used to be.

    The confidence goes with it, for the same reason `_became_manual` drops a section's
    measured energies: the number described the provider's certainty about words it chose,
    and leaving it attached to a sentence a person wrote makes a human decision look like a
    machine's — with a doubt attached that nobody expressed.
    """
    if line.source is not Provenance.MEASURED:
        return
    line.source = Provenance.MANUAL
    line.method = f"hand-edited (was: {line.method})" if line.method else "hand-edited"
    line.confidence = None


def _make_primary(song: Song, section: SongSection) -> None:
    song.primary_section_id = section.id
    song.hook = HookWindow(start_ms=section.start_ms, end_ms=section.end_ms)


def _best_overlap(sections: list[SongSection], window: HookWindow) -> str | None:
    """Which of these sections covers the old hook window best. None if none of them do."""
    if window.duration_ms <= 0:
        return None
    best, best_overlap = None, 0
    for section in sections:
        overlap = min(section.end_ms, window.end_ms) - max(section.start_ms, window.start_ms)
        if overlap > best_overlap:
            best, best_overlap = section.id, overlap
    return best
