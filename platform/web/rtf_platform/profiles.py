"""What a counterparty's embedding is computed from — one composer, one set of rules.

Three modules were composing profile text independently (`fcc`, `radiobrowser`, and
`agents.enrich_genre`'s append), and the result was measurably bad in a way that only
shows up when you read the vectors' source text side by side:

    ZCBN is a noncommercial educational radio station broadcasting on 92.3 FM from
    Tortola, VI, licensed to -. It appears to be a other station, inferred from the
    licensee's name rather than confirmed. Stations in the reserved 88.1–91.9 MHz band
    are noncommercial and educational by licence condition, and college and community
    stations in this band typically programme local and independent music, accept
    submissions from unsigned and independent artists, and are run by student or
    volunteer music directors.

Everything after the second sentence is **identical across all 8,568 FCC stations**. The
Radio Browser text carried its own invariant tail — "Tags are contributed by the Radio
Browser community and are asserted rather than measured" — across another 5,576. Roughly
half of every station's embedded text was a string every other station also had.

## The three rules this module exists to enforce

**1. No text that every row shares.** A sentence present in all 14,000 documents
contributes nothing to distinguishing them and actively dilutes what does: cosine
similarity over a short document is dominated by its bulk, so fifty words of shared
boilerplate drown four words of genre. Provenance, licence conditions and methodology
notes are *columns* — `party_fact.provenance` already carries them, losslessly and
queryably. They do not belong in a vector.

**2. The text describes programming, not identity.** The station's *name is deliberately
excluded*. This is the fix for the failure that started this file: shortlisting the artist
"Hallow Youth" returned "Halloween Radio", "Sleepy Hollow Radio" and "HEXX 9 Radio" —
lexical neighbours of the artist's name, none of them with a single genre tag between
them. A name is an identifier, and `party.name` already holds it for display; putting it
in the embedding means an artist called Deep House gets shortlisted to a station called
Deep House Radio for reasons that have nothing to do with what either of them plays.

The cost is real and worth naming: a station whose name carries its only genre signal
("Deep House Radio", untagged) loses that signal. Rule 3 is why that is the right trade.

**3. A station with no known programming says so, in words that match nothing.** It does
not get filler, and it does not get a guess from its licensee's name. It gets one short
sentence that is semantically distant from every genre query, so it sinks rather than
floating on noise. `enrich_genre` writes nothing when Wikipedia has no format for the same
reason — see `SCOPE-RESET §2a` rule 1.
"""

from __future__ import annotations

from typing import Any

#: What a station with no documented programming gets. Short, and deliberately using
#: vocabulary that no genre query will be near — it should rank last, not plausibly.
UNKNOWN = "Programming not documented."


def compose(*, role: str, genres: list[str] | None = None, market: str = "",
            station_kind: str = "", licensee: str = "", frequency: str = "",
            language: str = "") -> str:
    """The canonical embedded description of a counterparty.

    Genre leads and is stated twice — once as prose and once as a bare list. That is not
    redundancy for its own sake: a sentence embeds as a sentence, and a short list of
    domain terms gives the vector a blunt, high-signal handle that survives being averaged
    with the rest. Everything else is one clause and only when it is known.

    **`role` is required and deliberately has no default.** It used to default to
    `"radio station"`, which was harmless for exactly as long as radio stations were the
    only counterparty. The moment `podcastindex` landed, that default became a way to
    describe eighty-five thousand podcasts as radio stations — not at ingest, where
    `profile_text` passes the right value, but on the next `--recompose-profiles`, which
    rebuilds from facts and never had a role to pass. Silent, wrong, and invisible until
    somebody read the source text of the vectors side by side, which is the precise
    failure this module was written to end.

    A default that is right for the current callers and wrong for the next one is the
    shape of bug this codebase refuses on principle — `013_lease_token.sql` makes the
    same argument about worker names. So the parameter moved to the front of the
    signature and lost its default: a caller that has not decided what kind of thing it
    is describing now fails to call this at all, which is the only version of this
    problem that cannot ship.
    """
    tags = [g.strip().lower() for g in (genres or []) if g and g.strip()]

    if not tags:
        body = UNKNOWN
    else:
        listed = ", ".join(tags)
        body = f"Plays {listed}. Genres: {listed}."

    where = market.strip()
    if where:
        body += f" Broadcasts to {where}."
    if station_kind and station_kind not in {"other", ""}:
        body += f" A {station_kind.replace('_', ' ')} {role}."
    elif role:
        body += f" A {role}."
    if language.strip():
        body += f" Language: {language.strip()}."
    if frequency.strip():
        body += f" On {frequency.strip()}."
    # The licensee is the last clause and only when it is a real name. It is weak signal
    # — a holding company says nothing — but for a college licence ("University of Rhode
    # Island") it carries the one thing a music director cares about: who runs the place.
    holder = licensee.strip()
    if holder and holder not in {"-", "—"}:
        body += f" Licensed to {holder}."

    return body


def from_facts(name: str, facts: dict[str, str]) -> str:
    """Compose from a party's `party_fact` rows, keyed by dimension.

    `name` is accepted and **not used in the returned text** — it is in the signature so
    that a caller reading this line is confronted with rule 2 rather than discovering it
    by grepping. See the module docstring.

    The `role` dimension is read from the facts and is **required**, because this
    function is the rebuild path: `ingest.recompose_profiles` calls it for every
    counterparty in the tenant, and whatever it returns replaces the text those vectors
    were built from. A source module gets to know what it is describing — `podcastindex`
    passes `role="podcast"` directly. A rebuild from facts does not, and until this
    dimension existed it silently assumed radio.

    Raising here rather than guessing is the whole point. The alternatives were both
    worse: a default relabels every podcast as a radio station, and inferring the role
    from which other dimensions happen to be present (`frequency_mhz` implies radio,
    `show_kind` implies podcast) is a guess wearing a measurement's clothes, which is the
    one thing `SCOPE-RESET §2a` rule 1 forbids outright. A party whose role nobody
    recorded is a party whose profile nobody can rebuild, and saying so is cheap next to
    rebuilding it wrongly.
    """
    role = facts.get("role", "").strip()
    if not role:
        raise ValueError(
            f"{name!r} has no `role` fact, so there is no way to say what kind of "
            "counterparty it is without guessing. Its profile cannot be rebuilt from "
            "facts until one is written — see migration 026, which backfills the "
            "sources that predate this dimension.")

    raw = facts.get("genre", "")
    genres = [g.strip() for g in raw.split(",") if g.strip()] if raw else []
    return compose(
        role=role,
        genres=genres,
        market=facts.get("market", ""),
        station_kind=facts.get("station_kind", ""),
        licensee=facts.get("licensee", ""),
        frequency=facts.get("frequency_mhz", ""),
        language=facts.get("language", ""),
    )
