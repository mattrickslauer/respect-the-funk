"""The parse boundary between what an adapter returns and what gets written.

`sources.py` hands back a `dict` per item — an identifier, a fact, a metric, a
recording, a release, a suggestion that a human may promote to a presence. Every
consumer used to call `.get(key, default)` on that dict at the point of use, and the
failure that produces is not "the read comes back empty" — it is a fact of *unknown*
provenance silently becoming the platform's *most confident* answer, because
`.get("provenance", "measured")` cannot tell the difference between an adapter that
said nothing and one that actually measured something. `measured` is the highest-trust
class this system has; inventing it is the worst possible guess, not a safe one.

`SCOPE-RESET §2a` states three provenance classes and the discipline that every fact
carries one. This module is where that discipline is enforced rather than hoped for.
Each item an adapter can emit gets a frozen dataclass and a `parse(raw, *, adapter)`
classmethod that:

  * requires every identity field (`kind`/`value`, `dimension`/`value_text`,
    `metric`/`value`, `platform`) and every trust-class label (`provenance`,
    `unit`, `release_type`, `mode`) — the fields a caller cannot safely guess on
    the adapter's behalf — raising `HarvestInvalid(adapter, field, raw)`, naming
    the adapter and the field, when one is absent. This is **not** the same claim
    as "every `NOT NULL` column". `recording.title` and `release.title` are `NOT
    NULL` in the schema, but a blank title is a real "the platform gave nothing"
    answer, not a missing label — `Recording`/`Release.parse` accept `""` there,
    and the write side skips a blank title, exactly as it always has;
  * never defaults a label. `provenance`, `unit`, `release_type` and `mode` are
    required on every item that carries one. An adapter that cannot say what it
    measured has produced an item this system cannot store, and `parse` raises
    rather than guess on the adapter's behalf;
  * validates `provenance` against the three values `fact_provenance`,
    `identifier_provenance` and `presence_match` — the schema's own `CHECK`
    constraints — allow. `mode` is validated too, against `domain.ProfileMode`'s
    three values, and as of migration `014` (`presence_mode_known`)
    `presence.mode` carries that same `CHECK` in the schema — added `NOT VALID`
    because 18 of 21 live rows already held the illegal `'observed'` value this
    module would have rejected, written by SQL that bypassed this parser
    entirely (`agents._write_find_counterparties`, fixed in the same change).
    `NOT VALID` means those 18 pre-existing rows are grandfathered, not that the
    constraint is inert: every write from `014` onward is checked by the
    database regardless of whether it goes through this module. So this is now
    a redundant, earlier gate for a `presence` row assembled through
    `Presence.parse` — not the only guard an illegal `mode` has to get past.
    `release_type` is required to be non-empty but is **not** validated against a
    fixed set of values — see `Release`'s own docstring for why.

Why a dataclass and not a validated dict: a validated dict is still a dict at the
next call site, and the next author will `.get()` it anyway. These are types — once
`parse` returns, the missing field is unrepresentable, not merely absent-and-checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: `SCOPE-RESET §2a`'s three provenance classes — the values `identifier_provenance`,
#: `fact_provenance` and `presence_match` accept in `platform/schema/005_party_first.sql`.
#: A fourth value here is a bug in the caller, not a new kind of fact.
PROVENANCE_CLASSES: tuple[str, ...] = ("measured", "inferred", "asserted")

#: `presence.mode`'s legal values — `domain.ProfileMode`'s three, duplicated rather
#: than imported so this module (which the statement importer will also depend on)
#: carries no dependency on the web layer.
PRESENCE_MODES: tuple[str, ...] = ("owned", "unowned", "absent")


class HarvestInvalid(ValueError):
    """An adapter's item is missing a field the database requires, or gave one an
    illegal value. Carries the adapter, the field and the raw dict, so fixing the
    adapter is a matter of reading the exception rather than reproducing it."""

    def __init__(self, adapter: str, field: str, raw: dict[str, Any], *,
                reason: str = "missing") -> None:
        self.adapter = adapter
        self.field = field
        self.raw = raw
        self.reason = reason
        super().__init__(f"{adapter}: {field!r} is {reason} in {raw!r}")


def _required(raw: dict[str, Any], field: str, *, adapter: str) -> Any:
    """A field with something in it, or `HarvestInvalid`. `None` and `""` are both
    "the adapter said nothing" — the two spellings a `dict.get(field)` returns for a
    key that was never set and one set to an empty string, and neither is a value
    worth writing as though it were one."""
    value = raw.get(field)
    if value is None or value == "":
        raise HarvestInvalid(adapter, field, raw)
    return value


def _provenance(raw: dict[str, Any], *, adapter: str) -> str:
    value = _required(raw, "provenance", adapter=adapter)
    if value not in PROVENANCE_CLASSES:
        raise HarvestInvalid(adapter, "provenance", raw,
                             reason=f"not one of {PROVENANCE_CLASSES}")
    return value


# --------------------------------------------------------------------- Identifier

@dataclass(frozen=True)
class Identifier:
    """One row of `party_identifier`. `value_raw` is the one field on *this* class
    allowed to fall back — to `value` itself, which is not a guess: an identifier
    with no distinct raw spelling to keep is honestly represented by repeating the
    canonical one. (`Release.gtin`/`release_date` and `Presence.handle`/
    `profile_url` fall back too, for the same reason — real, optional data with no
    trust class of its own, not a label being guessed.)"""

    kind: str
    value: str
    value_raw: str
    provenance: str

    @classmethod
    def parse(cls, raw: dict[str, Any], *, adapter: str) -> "Identifier":
        kind = _required(raw, "kind", adapter=adapter)
        value = _required(raw, "value", adapter=adapter)
        provenance = _provenance(raw, adapter=adapter)
        return cls(kind=kind, value=value,
                   value_raw=raw.get("value_raw") or value, provenance=provenance)


# --------------------------------------------------------------------------- Fact

@dataclass(frozen=True)
class Fact:
    """One row of `party_fact`. `confidence` is genuinely optional: the column is
    nullable, and a `measured` or `asserted` fact has no meaningful confidence to
    give in the first place — only an `inferred` one does."""

    dimension: str
    value_text: str
    provenance: str
    confidence: float | None

    @classmethod
    def parse(cls, raw: dict[str, Any], *, adapter: str) -> "Fact":
        dimension = _required(raw, "dimension", adapter=adapter)
        value_text = _required(raw, "value_text", adapter=adapter)
        provenance = _provenance(raw, adapter=adapter)
        return cls(dimension=dimension, value_text=value_text,
                   provenance=provenance, confidence=raw.get("confidence"))


# ------------------------------------------------------------------------- Metric

@dataclass(frozen=True)
class Metric:
    """One row of `party_metric`. `unit` is the dimension of `value` — a follower
    count and a 0-100 popularity score are both floats, and nothing but this field
    tells them apart, so it is required exactly like `provenance`."""

    metric: str
    value: float
    unit: str
    provenance: str

    @classmethod
    def parse(cls, raw: dict[str, Any], *, adapter: str) -> "Metric":
        metric = _required(raw, "metric", adapter=adapter)
        # `value` may legitimately be 0 — a fresh act with zero followers is a real
        # measurement, not a missing one — so this checks for absence, not falsiness.
        if raw.get("value") is None:
            raise HarvestInvalid(adapter, "value", raw)
        unit = _required(raw, "unit", adapter=adapter)
        provenance = _provenance(raw, adapter=adapter)
        return cls(metric=metric, value=raw["value"], unit=unit, provenance=provenance)


# ---------------------------------------------------------------------- Recording

@dataclass(frozen=True)
class Recording:
    """One row of `recording`. Unlike the three above, `recording` carries no
    provenance column: a title, an ISRC and a duration are catalogue facts an
    adapter either read off the platform or did not, not a label with a trust
    class of its own. A blank title is therefore a real, if useless, answer rather
    than a reason to raise — the write side skips it, matching the behaviour every
    adapter here already relies on for a track a platform returned with nothing in
    it."""

    title: str
    isrc: str
    duration_ms: int | None

    @classmethod
    def parse(cls, raw: dict[str, Any], *, adapter: str) -> "Recording":
        return cls(
            title=(raw.get("title") or "").strip(),
            isrc=(raw.get("isrc") or "").strip().upper(),
            duration_ms=raw.get("duration_ms"),
        )


# ------------------------------------------------------------------------ Release

@dataclass(frozen=True)
class Release:
    """One row of `release`. `release_type` is required for the same reason
    `provenance` is: a release the adapter could not classify is not honestly a
    single by default — it is a release this system cannot store yet.

    Unlike `provenance`/`mode`, `release_type` is **not** validated against a
    fixed set of values here. Real distributors disagree on their own vocabulary
    — Spotify's `album`/`single`/`compilation`/`appears_on` vs. Deezer's
    `album`/`single`/`ep`/`compile` — and enumerating one distributor's spelling
    as canonical would reject another's honest, real answer. The requirement is
    only that the adapter says *something*, not that it says one of a closed list."""

    title: str
    release_type: str
    gtin: str
    release_date: str

    @classmethod
    def parse(cls, raw: dict[str, Any], *, adapter: str) -> "Release":
        release_type = _required(raw, "release_type", adapter=adapter)
        return cls(
            title=(raw.get("title") or "").strip(),
            release_type=release_type,
            gtin=(raw.get("gtin") or "").strip(),
            release_date=(raw.get("release_date") or ""),
        )


# ----------------------------------------------------------------------- Presence

@dataclass(frozen=True)
class Presence:
    """What `repo.accept_suggestion` promotes a suggestion into. `mode` is `owned` /
    `unowned` / `absent` — `owned` is the strongest ownership claim in the system,
    so it is the suggestion's job to say which one accepting it means, the same as
    `provenance` is every adapter's job elsewhere in this module."""

    platform: str
    mode: str
    handle: str
    profile_url: str

    @classmethod
    def parse(cls, raw: dict[str, Any], *, adapter: str) -> "Presence":
        platform = _required(raw, "platform", adapter=adapter)
        mode = _required(raw, "mode", adapter=adapter)
        if mode not in PRESENCE_MODES:
            raise HarvestInvalid(adapter, "mode", raw,
                                 reason=f"not one of {PRESENCE_MODES}")
        return cls(platform=platform, mode=mode,
                   handle=raw.get("label") or raw.get("handle") or "",
                   profile_url=raw.get("url") or "")
