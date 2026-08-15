"""The vocabulary the platform is willing to say out loud.

`ArtistType` is the supported set, and it is a type rather than a list of strings
so that the select, the server-side validation and anything downstream that
branches on kind all read from one definition. Adding `mariachi` is one line here
and appears in the form immediately.

**This does not become a database ENUM.** The column stays `STRING`
(`schema/002_artist_type.sql`), for two reasons that survived the change from a
free-text field to a closed one:

  * an ENUM makes adding a value an `ALTER TYPE` — a migration to coordinate with
    a deploy, for what is really a copy change;
  * a row written before a value was retired still has to load. A STRING column
    holds it; an ENUM the value was removed from cannot.

So the closed set is enforced where writes happen, in `routes`, and the storage
stays permissive enough to hold history. `unrecognised()` is what makes the second
reason real rather than theoretical: an artist carrying a type this build has never
heard of stays editable, and keeps its value unless somebody deliberately changes
it.
"""

from __future__ import annotations

import re
from enum import Enum


class ArtistType(str, Enum):
    """A kind of act. The value is what is stored; the label is what is shown."""

    def __new__(cls, value: str, label: str, group: str) -> "ArtistType":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label      # type: ignore[attr-defined]
        obj.group = group      # type: ignore[attr-defined]
        return obj

    # --- more than one person ------------------------------------------------
    BAND       = ("band",       "Band",              "Groups")
    DUO        = ("duo",        "Duo",               "Groups")
    TRIO       = ("trio",       "Trio",              "Groups")
    COLLECTIVE = ("collective", "Collective",        "Groups")
    ORCHESTRA  = ("orchestra",  "Orchestra",         "Groups")
    ENSEMBLE   = ("ensemble",   "Ensemble",          "Groups")
    CHOIR      = ("choir",      "Choir",             "Groups")

    # --- one person ----------------------------------------------------------
    SOLO       = ("solo",       "Solo artist",       "Individuals")
    SINGER     = ("singer",     "Singer",            "Individuals")
    SONGWRITER = ("songwriter", "Singer-songwriter", "Individuals")
    RAPPER     = ("rapper",     "Rapper",            "Individuals")
    DJ         = ("dj",         "DJ",                "Individuals")
    PRODUCER   = ("producer",   "Producer",          "Individuals")
    COMPOSER   = ("composer",   "Composer",          "Individuals")

    @classmethod
    def values(cls) -> set[str]:
        return {t.value for t in cls}

    @classmethod
    def parse(cls, raw: str) -> "ArtistType | None":
        """The stored value back into a member, or None if this build has never
        heard of it. Callers decide whether that is a rejection or a legacy row."""
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return None

    @classmethod
    def grouped(cls) -> list[tuple[str, list["ArtistType"]]]:
        """Ordered `(group, members)` for rendering `<optgroup>`s. Fourteen options
        in one flat list is a scrolling contest; two named groups is a choice."""
        groups: list[tuple[str, list[ArtistType]]] = []
        for member in cls:
            if groups and groups[-1][0] == member.group:  # type: ignore[attr-defined]
                groups[-1][1].append(member)
            else:
                groups.append((member.group, [member]))   # type: ignore[attr-defined]
        return groups


DEFAULT_TYPE = ArtistType.BAND


class Platform(str, Enum):
    """A surface the label can look at for an artist.

    Closed for the same reason `ArtistType` is: an adapter branches on this value, so
    a typo in a text field would be an artist nobody ever researches, failing silently.
    The column stays STRING (`004_research.sql`) so a retired surface still loads.
    """

    def __new__(cls, value: str, label: str) -> "Platform":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label      # type: ignore[attr-defined]
        return obj

    SPOTIFY     = ("spotify",     "Spotify")
    APPLE_MUSIC = ("apple_music", "Apple Music")
    DEEZER      = ("deezer",      "Deezer")
    YOUTUBE     = ("youtube",     "YouTube")
    INSTAGRAM   = ("instagram",   "Instagram")
    TIKTOK      = ("tiktok",      "TikTok")
    SOUNDCLOUD  = ("soundcloud",  "SoundCloud")
    BANDCAMP    = ("bandcamp",    "Bandcamp")
    MUSICBRAINZ = ("musicbrainz", "MusicBrainz")
    ACOUSTID    = ("acoustid",    "AcoustID")
    PRESS       = ("press",       "Press / web")

    @classmethod
    def parse(cls, raw: str) -> "Platform | None":
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return None


class ProfileMode(str, Enum):
    """Whether the artist holds the account on a platform — not whether we look there.

    `absent` is a real answer, not a missing one: an act with no TikTok account still
    has TikTok content made about them, and that content is the point. Recording the
    mode is what lets one code path serve a one-account act and a five-account act.
    """

    def __new__(cls, value: str, label: str, hint: str) -> "ProfileMode":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label    # type: ignore[attr-defined]
        obj.hint = hint      # type: ignore[attr-defined]
        return obj

    OWNED   = ("owned",   "Owned",   "the artist runs this account")
    UNOWNED = ("unowned", "Unowned", "someone else's account about them")
    ABSENT  = ("absent",  "Absent",  "no account — still worth searching")

    @classmethod
    def parse(cls, raw: str) -> "ProfileMode | None":
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return None


#: The statuses an artist row may carry. Kept here rather than inline in the form so the
#: select and the validation cannot drift apart.
ARTIST_STATUSES: tuple[str, ...] = ("active", "paused", "archived")

#: The same for a recording. A separate tuple rather than a reuse of `ARTIST_STATUSES`
#: even though the members are identical today: a paused artist stops the agents, an
#: archived recording is a catalogue statement, and collapsing them would make the next
#: divergence a schema migration instead of an edit here.
RECORDING_STATUSES: tuple[str, ...] = ("active", "paused", "archived")

#: The credit roles the console offers. `party_credit.role` is an open `STRING` — a
#: contract can credit anyone as anything — so this is the set an operator can pick from
#: rather than the set the column allows. `main_artist` and `featured` lead because
#: `research.tracks` and `assets.queue_analysis` both key on exactly those two: they are
#: what decides whose record it is, and therefore whose budget an analysis is spent from.
CREDIT_ROLES: tuple[str, ...] = (
    "main_artist", "featured", "producer", "writer", "remixer", "engineer",
)


# ------------------------------------------------------------------ identifiers

# Every identifier in this industry arrives in more than one spelling, and a naive
# unique index files the same recording as two rows. So each is stored twice: the
# canonical form joins, and the raw form is kept because the provenance model has to
# be able to show what a source actually said.
#
# These return "" for anything that is not a well-formed identifier rather than
# guessing at a repair. A blank canonical value means "we hold no identifier", which
# is a state the schema already models — a wrong one is not.

def canonical_isrc(raw: str | None) -> str:
    """`QZ-ABC-25-00001` and `qzabc2500001` are one recording. ISO 3901: two letters
    of country, three of registrant, two digits of year, five of designation."""
    if not raw:
        return ""
    stripped = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return stripped if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", stripped) else ""


def canonical_gtin(raw: str | None) -> str:
    """UPC-12, EAN-13 and GTIN-14 are the same number with different leading zeros.
    Padding to 14 is what makes `602557123456` and `0602557123456` compare equal."""
    if not raw:
        return ""
    digits = re.sub(r"[^0-9]", "", raw)
    return digits.zfill(14) if 8 <= len(digits) <= 14 else ""


def canonical_iswc(raw: str | None) -> str:
    """`T-123.456.789-C` and `T1234567891` are one work. ISO 15707."""
    if not raw:
        return ""
    stripped = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return stripped if re.fullmatch(r"T[0-9]{10}", stripped) else ""


def canonical_isni(raw: str | None) -> str:
    """16 characters, no spaces. The final one may be an X check digit."""
    if not raw:
        return ""
    stripped = re.sub(r"[^0-9Xx]", "", raw).upper()
    return stripped if re.fullmatch(r"[0-9]{15}[0-9X]", stripped) else ""


def unrecognised(raw: str | None) -> str | None:
    """A stored type this build does not define, or None if it is known/empty.

    Retiring a value from the enum must not make existing artists uneditable, and
    must not silently rewrite them to the default. The form renders this as its own
    selected option so the value survives an unrelated edit.
    """
    if not raw:
        return None
    return None if ArtistType.parse(raw) else raw
