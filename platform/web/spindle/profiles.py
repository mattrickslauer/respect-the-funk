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


## The prose slot, and why admitting free text is not a fourth rule

Every field above is structured: a genre list, a market, a frequency. `podcastindex`
arrived with something none of the earlier sources had — a paragraph the publisher wrote
about what the show plays. It is the richest signal any adapter in this codebase produces.
A Radio Browser station offers four community tags; a feed offers sixty words of somebody
describing their own programming in sentences. Refusing that because the three rules
happened to be written about structured fields would be refusing the best available input
for a reason about this file's history rather than about the vectors.

So `compose` takes `prose`, and `podcastindex.profile_text` passes the blurb into it
rather than appending after it. That module's docstring used to name the append as a gap
being flagged rather than hidden; this is the change that closes it, and the reason it
belongs here and not there is the whole argument of this file — the fourth module to
compose its own text would have drifted exactly like the first three.

What follows is the case that free text can be admitted without weakening any of the
three rules, and the places where admitting it costs something real.

**Against rule 1 — no text that every row shares.** Publisher prose is precisely where
boilerplate lives. "Subscribe at patreon.com/…", "Follow us @theshow", "Advertising
enquiries to sales@…" — not rare, and close enough to identical across tens of thousands
of feeds to be the same invariant tail this module was written to delete, arriving by a
different door. They are also *structurally* recognisable: essentially every one of them
carries a URL, an email address or an @handle. So the slot drops **the entire sentence**
containing any of those, rather than stripping the link and keeping the wreckage —
"Subscribe at for bonus episodes" is worse than nothing, being boilerplate and
ungrammatical at once.

The cost is a sentence like "Nightly blues from Chicago, archive at bluesradio.com", where
a clause of real programming signal leaves with the link. That is a genuine loss and it is
accepted, because the alternative is an enumerated list of promotional phrases to strip,
and such a list is a guess about a corpus nobody here can inspect, goes stale in silence,
and fails in the direction this codebase keeps calling the worst — it reports success. A
structural test for a link needs no maintenance when marketing copy changes.

**Against rule 2 — programming, not identity.** A blurb opening "Join host Mary Anne Hobbs
for three hours of…" puts a person's name in the vector, which is the failure rule 2 was
written to kill. Rule 2 enforces itself on structured fields by never composing a name in.
Prose arrives with the names already inside it, so the slot cannot decline to add them; it
has to *subtract* them.

That is what `names` is for: the identity strings the caller already knows — the show's
title, the host it is about to write as a fact — every occurrence of which is removed from
the prose. The mirror of rule 2 rather than an exception to it. A name is still never
added, and now it is actively taken out.

Two limits, both deliberate.

*Only multi-token names are excised.* "Mary Anne Hobbs" and "Halloween Radio" go;
"Bandsplain" stays. This is not squeamishness about single words. Plenty of shows are
called "Jazz", "Motown" or "Dubstep", and excising a one-word name would delete the genre
word from every sentence that used it — making the best signal in the document the thing
most likely to be destroyed. The restriction also sits where the value is: a lexical
collision with an artist name almost always involves more than one word, which is what
produced "Hallow Youth" → "Halloween Radio". What it costs is a distinctive one-word name
surviving inside a paragraph, where it is one token among sixty rather than the whole
document, which is the condition under which rule 2's failure actually happened.

*None of this is named-entity recognition and it must not be read as such.* A blurb naming
a guest the caller has never heard of keeps that name. A regex over capitalised bigrams
would catch it, and would also delete "Blue Note", "New Orleans" and "Deep House" — worse
than what it fixes. The slot removes exactly the names it was told and guesses at nothing.

**Against rule 3 — silence rather than filler.** A show with no categories but a real
paragraph about what it plays *does* have documented programming, and printing
"Programming not documented." in front of that paragraph would be a false sentence and an
invariant one, which is both rules at once. So surviving prose **replaces** `UNKNOWN` and
takes the leading position, because the leading position belongs to whatever states the
programming.

The converse is the part that matters. Prose that cleans down to nothing — a description
that was only a link, or only the show's own name — contributes nothing at all, and a row
with no genres and no surviving prose still gets `UNKNOWN`. Nothing is invented to fill
the space it leaves.

**And the length, which is the trade-off with no clean answer.** `PROSE_CHARS` is 400 and
no value is obviously right.

Uncapped, one verbose publisher dominates its own vector. Show notes run to thousands of
words of episode listings, guest bios and back-catalogue links; rule 1's argument — that
cosine similarity over a short document is dominated by its bulk — applies *within* a
document as forcefully as across a corpus, and a 3,000-character description reduces the
genre sentence in its own text to noise.

Capped, a show whose sixth sentence is the one reading "specialising in Ethiopian jazz
reissues" loses it. That is the loss, and it is the one chosen, because publishers put
what the show *is* in the first two sentences and the merchandise later, and because the
damage is bounded: what a cap costs is the tail of a long description, never the whole of
a short one. 400 characters is roughly four sentences. Against a structured body running
60–160 characters, prose at the cap is about three-quarters of the document — the most it
may ever be — and a typical blurb sits nearer half.

**The cut lands on a sentence boundary and nothing marks it.** Whole sentences are taken
until the next would not fit, because these models embed sentences and a clause severed
mid-phrase embeds as a fragment. No ellipsis is appended: a "…" on every truncated row is
a token thousands of documents would share, which is rule 1 being smuggled back in by the
mechanism meant to serve it.
"""

from __future__ import annotations

import re
from typing import Any

#: What a station with no documented programming gets. Short, and deliberately using
#: vocabulary that no genre query will be near — it should rank last, not plausibly.
UNKNOWN = "Programming not documented."

#: How much source-supplied prose reaches the vector, in characters. See the module
#: docstring for the arithmetic behind the number and for what a cap costs.
PROSE_CHARS = 400

#: Sentence boundaries, as far as a regex can tell. It splits after `.`, `!` or `?`
#: followed by whitespace, which means "feat. Roy Ayers" and "U.K. garage" split into two
#: pieces. That is tolerable and not worth a parser: the pieces are rejoined with a single
#: space, so a mis-split changes nothing about the text unless it lands exactly on the
#: truncation boundary, where the cost is one clause.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

#: Runs of whitespace, including the newlines a publisher's CMS leaves behind.
_SPACE = re.compile(r"\s+")

#: The structural marks of a call to action: a link, an address, a handle. A sentence
#: carrying any of these is dropped whole — see rule 1 in the module docstring. The bare
#: domain arm needs a suffix list, which is the one part of this that can go stale; it is
#: allowed to, because a suffix nobody listed costs one noisy sentence in one document
#: rather than a wrong answer, and because the alternative pattern (any dotted word) also
#: matches "R.E.M." and every abbreviation a music writer uses.
_CONTACT = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|[\w.+-]+@[\w-]+\.[\w.-]+"
    r"|(?<![\w/])@[A-Za-z0-9_]{2,}"
    r"|\b[\w-]{2,}\.(?:com|net|org|co|uk|fm|io|me|tv|app|link|shop|xyz)\b(?:/\S*)?",
    re.IGNORECASE,
)


def _excise(sentence: str, names: list[str]) -> str:
    """Remove every multi-token identity string in `names` from one sentence.

    Case-insensitive and literal — `re.escape`, never a pattern the caller supplies.
    Single-token names are skipped here rather than filtered by the caller so that the
    reason travels with the code that would otherwise delete the word "jazz" from a show
    called Jazz; see rule 2 in the module docstring.

    What is left can read a little oddly — "The Blues Show is a weekly hour of Chicago
    blues" becomes "is a weekly hour of Chicago blues". That is the intended trade. An
    embedding degrades gracefully on a missing subject and not at all on a missing name,
    whereas the name is the thing that makes an unrelated artist a nearest neighbour.
    """
    for raw in names:
        name = " ".join(str(raw or "").split())
        if " " not in name:
            continue
        sentence = re.sub(re.escape(name), " ", sentence, flags=re.IGNORECASE)
    # Excising "with Jo Presenter." leaves "with ." The stranded space is cosmetic to a
    # reader and not to a tokeniser, which would see a lone punctuation token that every
    # excised row shares — small, but rule 1 does not have a size exemption.
    sentence = re.sub(r"\s+([.,;:!?])", r"\1", _SPACE.sub(" ", sentence))
    return sentence.strip()


def _prose(text: str, names: list[str]) -> str:
    """Source-supplied free text, made fit to embed — or the empty string.

    Four passes, in this order and for the reasons the module docstring argues at length:
    collapse whitespace, drop any sentence carrying a link or a handle, subtract the
    identity strings, and take whole sentences up to `PROSE_CHARS`.

    Order matters in one place worth naming. Sentences are dropped *before* names are
    excised, so a sentence that is nothing but "Follow Mary Anne Hobbs @maryannehobbs"
    goes as a unit rather than being reduced to a stub that then has to be recognised as
    empty. The empty check afterwards is the second net, not the first.

    Returning `""` is a real answer and not a failure: it means the publisher wrote
    nothing this module is willing to embed. The caller composes as though no prose had
    been offered, which for a row with no genres means `UNKNOWN` — a row that sinks,
    which is what rule 3 asks for and what a fabricated sentence would prevent.
    """
    surviving: list[str] = []
    for sentence in _SENTENCE.split(_SPACE.sub(" ", text).strip()):
        if not sentence or _CONTACT.search(sentence):
            continue
        sentence = _excise(sentence, names)
        # A sentence that was only a name is now only punctuation. It is not text.
        if not any(character.isalnum() for character in sentence):
            continue
        surviving.append(sentence)

    kept: list[str] = []
    used = 0
    for sentence in surviving:
        cost = len(sentence) + (1 if kept else 0)
        if used + cost > PROSE_CHARS:
            break
        kept.append(sentence)
        used += cost
    if kept:
        return " ".join(kept)

    if not surviving:
        return ""
    # Nothing fit, so the first surviving sentence is longer than the entire budget — a
    # publisher who wrote a paragraph with no full stop in it. Cutting at the last word
    # boundary inside the budget is the only option that neither discards a whole good
    # description nor embeds a severed word. A run of `PROSE_CHARS` with no space in it
    # is not prose and gets a hard cut; there is nothing better to do with a token that
    # long, and pretending otherwise would only hide it.
    head = surviving[0][:PROSE_CHARS]
    cut = head.rfind(" ")
    return head[:cut].rstrip() if cut > 0 else head


def compose(*, role: str, genres: list[str] | None = None, market: str = "",
            station_kind: str = "", licensee: str = "", frequency: str = "",
            language: str = "", prose: str = "",
            names: list[str] | None = None) -> str:
    """The canonical embedded description of a counterparty.

    Genre leads and is stated twice — once as a sentence and once as a bare list. That is
    not redundancy for its own sake: a sentence embeds as a sentence, and a short list of
    domain terms gives the vector a blunt, high-signal handle that survives being averaged
    with the rest. Everything else is one clause and only when it is known. ("Prose" is a
    term of art in this file from here on: it means the `prose` parameter, which is the
    publisher's own text, and never the composed sentences above.)

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

    **`prose` is the slot for text a source's publisher wrote about themselves**, and
    `names` is the identity strings to subtract from it. The module docstring argues both
    against the three rules; two operational notes belong here.

    *`prose` must arrive as plain text.* Decoding a source's wire format is the adapter's
    job and not this function's — `podcastindex.blurb` strips the HTML an RSS description
    carries, because only that module knows its field is HTML. The line is between
    decoding, which needs source-specific knowledge, and deciding what belongs in a
    vector, which must not be source-specific or this file has failed. Markup that
    reaches this slot is treated as words, and will look like it.

    *`names` has no default when `prose` is non-empty; passing prose without it raises.*
    `names=[]` is available and means "this source has no identity strings to subtract",
    but it has to be typed. This is the `role` argument again: a caller who has not
    thought about whether the paragraph they are handing over opens with a person's name
    should discover that at the call site, not in a shortlist six weeks later that keeps
    returning the show whose host shares a surname with the artist. Embedding a name by
    omission is the failure rule 2 exists for, and an omission is exactly how it would
    happen a second time.
    """
    tags = [g.strip().lower() for g in (genres or []) if g and g.strip()]

    if prose.strip() and names is None:
        raise ValueError(
            "compose() was given `prose` but no `names`. Free text arrives with the "
            "publisher's own name, the host's name, or both already in it, and rule 2 "
            "of this module is that a counterparty's embedded text describes its "
            "programming and not its identity. Pass the identity strings you know — "
            "typically the party's name and any host — so they can be taken out, or "
            "`names=[]` to state that this source has none.")
    said = _prose(prose, names or [])

    if tags:
        listed = ", ".join(tags)
        body = f"Plays {listed}. Genres: {listed}."
    elif said:
        # No tags, but the publisher described the programming in words. `UNKNOWN` would
        # be a false sentence here, and an invariant one; the prose takes the leading
        # position instead of following it, because the leading position belongs to
        # whatever says what this counterparty plays.
        body, said = said, ""
    else:
        body = UNKNOWN

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

    # Prose goes last, after every structured clause. Not because an embedding cares
    # about order but because a human diffing two of these documents does: the composed
    # part stays aligned line-for-line between rows, which is how the drift this module
    # was written about was found in the first place.
    if said:
        body += f" {said}"

    return body


def from_facts(name: str, facts: dict[str, str]) -> str:
    """Compose from a party's `party_fact` rows, keyed by dimension.

    `name` is **never added to the returned text**, and is now actively *subtracted* from
    it: it is passed to `compose` as one of the `names` to excise from the `description`
    fact, alongside any `host`. It was originally in the signature only so that a caller
    reading this line would be confronted with rule 2 rather than discovering it by
    grepping; it has since acquired the job of enforcing that rule against the one input
    that arrives with names already inside it. Both directions are the same rule.

    **The `description` dimension is the rebuild path's copy of the prose slot.** A
    source module holds the publisher's blurb in its hand at ingest — `podcastindex`
    passes it straight to `compose` — and this function has only what was written to
    `party_fact`. If the blurb is not stored there, then `--recompose-profiles` rebuilds
    every podcast without it, the vectors quietly lose the best signal that source has,
    and nothing anywhere fails. That is the identical shape of bug as the `role` default
    below, which is why the slot is wired here in the same change that added it rather
    than in a later one: a composer that can only be driven correctly on the write path
    is a composer with a silent rebuild.

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
        prose=facts.get("description", ""),
        names=[name, facts.get("host", "")],
    )
