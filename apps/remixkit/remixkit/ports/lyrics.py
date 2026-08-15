"""The lyric transcription port.

`ports.audio` answers "where are the hooks" from the master. This answers "what is being
sung in them", and it is the same shape for the same reasons — services ask for lines,
adapters produce them, and no service imports a speech-to-text SDK.

Two things carry over from `ports.audio` deliberately, and one is new.

**`method` is required on the result.** A transcriber must describe how it heard, in one
line, and that line is stored on the song and rendered next to the words. Same rule as the
BPM: a claim without a stated method is a claim nobody can check.

**`available` / `unavailable_reason` are part of the port.** Transcription needs a
credentialed provider, which a laptop does not have. So the container always has *a*
transcriber and one of them refuses with the missing piece named — see
`adapters/lyrics_unavailable.py` for why there is no mock on this port either.

**Confidence is required per line, and it is the new part.** A section boundary is a
measurement of energy; a lyric line is a *model's guess at words*, and the two do not
deserve the same presentation. A word the model was unsure of and a word it was certain
of look identical once they are both text on a screen, so the number that separates them
travels with every line and the console renders it. This is what makes a transcript
editable rather than authoritative: the low-confidence lines are the ones to read first.

Timings are milliseconds from the start of the master, on the same clock as
`SongSection.start_ms` — so a lyric line and the section containing it can be compared
without either side converting anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class TranscribedLine:
    """One line of the transcript, with the window it was heard in.

    `confidence` is 0–1 and is `None` when the provider reported nothing to derive it
    from — never a default of 1.0. A fabricated certainty is worse here than a missing
    one, because the whole use of this field is deciding which lines to check by ear.
    """

    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class Transcription:
    """Everything one pass over a master heard.

    `language` is what the provider detected (or was told), and `language_confidence` is
    how sure it was — both optional, both `None` for "not reported". A transcriber that
    returns no lines and no warnings has said the master is instrumental, which is a real
    answer and must not be confused with a failure.
    """

    method: str
    lines: list[TranscribedLine] = field(default_factory=list)
    language: str | None = None
    language_confidence: float | None = None
    duration_ms: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class Transcriber(Protocol):
    name: str
    available: bool
    unavailable_reason: str

    def transcribe(
        self, data: bytes, *, filename: str = "", language: str | None = None
    ) -> Transcription:
        """Hear a master.

        `language`, when given, is an ISO-639 code a person supplied because they know
        what the song is in — providers detect it well on a sung vocal and badly on a
        heavily processed one, and a wrong detection is a whole transcript of plausible
        words in the wrong language.

        Raises `TranscriptionUnavailable` if this transcriber cannot run at all.
        """
