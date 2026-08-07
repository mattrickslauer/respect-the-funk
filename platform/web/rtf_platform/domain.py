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


def unrecognised(raw: str | None) -> str | None:
    """A stored type this build does not define, or None if it is known/empty.

    Retiring a value from the enum must not make existing artists uneditable, and
    must not silently rewrite them to the default. The form renders this as its own
    selected option so the value survives an unrelated edit.
    """
    if not raw:
        return None
    return None if ArtistType.parse(raw) else raw
