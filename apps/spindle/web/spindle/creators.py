"""UGC creators: the paste box, and what a creator's vibe is made of.

`037_creator_scout.sql` carries the full argument and it is not repeated here. The
one-paragraph version, because it explains why this module has no HTTP client:

    Every platform gates arbitrary creator data. TikTok's Research API exposes exactly
    the `music_id` query we want and forbids commercial use; Instagram returns audience
    data only under the creator's own OAuth grant; the affordable vendor tier is
    scraped-and-estimated. Pillar 10 §4's verdict is *no scraper, ever*, reached on
    enforcement grounds, and §5c's recommendation is that a person opens the sound pages
    of adjacent artists and reads off who used them. **The operator is the adapter.**

So the entry point for an entire counterparty class is a textarea, and everything below
exists to make that boundary strict. `harvested.py`'s discipline applies unchanged: a
field that cannot be read raises, naming the line, rather than defaulting to the most
confident answer available.

The vibe is `sound_usage`. A creator who posts over 124bpm four-on-the-floor house has an
audience that has demonstrably sat still for 124bpm four-on-the-floor house, and
`analyse_recording` already measures those properties off the audio. `compose_profile`
renders those rows into the text that `embed_party` embeds — into the *same* space as
every other party, for the index-prefix reason `037`'s header sets out at length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg

from spindle import fleet, spend

#: The platforms `sound_usage_platform_known` admits. Kept here as well so the paste box
#: rejects a typo before a round trip rather than after one, and asserted against the
#: constraint by `test_creator_scout.py` so the two cannot drift apart silently.
PLATFORMS = ("tiktok", "instagram", "youtube")

#: What a line looks like, quoted verbatim into every error this module raises. A scout
#: pasting fifty rows should never have to open this file to recover the column order.
SHAPE = ("handle | platform | sound_ref | views | median | genre  "
         "(the first three are required; the rest may be left empty)")


class ScoutInvalid(ValueError):
    """A pasted line could not be read.

    Raised rather than skipped, and raised for the *whole paste* rather than per line.
    A partially-accepted paste is the worst outcome available here: the scout sees a
    success message, some creators are silently absent, and the missing ones are
    indistinguishable from creators who were never on the sound page. Failing the batch
    means they fix the line and paste again, which costs a minute and loses nothing.
    """


@dataclass(frozen=True)
class ScoutRow:
    """One creator-and-sound pair, as read off a sound page by a person."""

    handle: str
    platform: str
    sound_ref: str
    view_count: int | None = None
    creator_median_views: int | None = None
    asserted_genre: str = ""

    @property
    def performed_well(self) -> bool | None:
        """Did this post beat the creator's own baseline?

        Pillar 10 §7 calls this the shortlisting key — *"rank by 'used an adjacent
        artist's sound and beat their own median', not by demographics"* — because it is
        measured behaviour rather than an inferred audience.

        `None` is a real third answer and callers must render it as one. A creator whose
        numbers nobody wrote down has not underperformed, and collapsing that into
        `False` would bury a good creator under a missing field.
        """
        if self.view_count is None or self.creator_median_views is None:
            return None
        return self.view_count > self.creator_median_views


def _count(raw: str, *, field: str, line_no: int) -> int | None:
    """A view count, or `None` for a field nobody filled in.

    The distinction this function exists to preserve: **empty is not zero.** `|  |` means
    the scout did not read the number; `| lots |` means they wrote something this cannot
    read. The first is a legitimate absence and becomes NULL. The second is a mistake, and
    turning it into `0` would rank a creator as having flopped on a sound they may well
    have carried — a wrong answer that looks exactly like a right one forever after.
    """
    text = raw.strip().replace(",", "").replace("_", "")
    if not text:
        return None
    if not text.isdigit():
        raise ScoutInvalid(
            f"line {line_no}: {field} reads {raw.strip()!r}, which is not a whole number. "
            f"Leave it empty if you did not read it — empty means 'not read' and is fine. "
            f"Shape: {SHAPE}")
    return int(text)


def parse_scout_paste(text: str) -> list[ScoutRow]:
    """Read a scout's pasted sound-page list into rows.

    Blank lines and `#` comments are skipped so a scout can label a block with the sound
    page and the date it came off — which is the only provenance this data will ever
    carry, and worth encouraging.

    An empty paste is zero rows and not an error: submitting an empty box is a no-op the
    console reports plainly. A *malformed* paste raises. Those are different events and
    conflating them is how a lenient parser starts eating work.
    """
    rows: list[ScoutRow] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        fields = [f.strip() for f in line.split("|")]
        if len(fields) < 3:
            raise ScoutInvalid(
                f"line {line_no}: {line!r} has {len(fields)} field(s); a creator needs at "
                f"least three. Shape: {SHAPE}")

        handle = fields[0].lstrip("@").strip().lower()
        platform = fields[1].strip().lower()
        sound_ref = fields[2].strip()

        if not handle:
            raise ScoutInvalid(f"line {line_no}: no handle. Shape: {SHAPE}")
        if platform not in PLATFORMS:
            raise ScoutInvalid(
                f"line {line_no}: {platform!r} is not a platform this indexes; known: "
                f"{', '.join(PLATFORMS)}")
        if not sound_ref:
            #: Guarded because `sound_usage_once` is unique on `(tenant, creator,
            #: platform, sound_ref)`. An empty ref would collapse every unattributed post
            #: by one creator into a single row, losing all but the first.
            raise ScoutInvalid(
                f"line {line_no}: no sound for @{handle} — the sound is the evidence, and "
                f"a creator without one is a name. Shape: {SHAPE}")

        rows.append(ScoutRow(
            handle=handle,
            platform=platform,
            sound_ref=sound_ref,
            view_count=_count(fields[3], field="views", line_no=line_no)
            if len(fields) > 3 else None,
            creator_median_views=_count(fields[4], field="median", line_no=line_no)
            if len(fields) > 4 else None,
            asserted_genre=fields[5].strip() if len(fields) > 5 else "",
        ))
    return rows


def compose_profile(handle: str, usages: list[dict[str, Any]]) -> str:
    """Compose a creator's profile text from their `sound_usage` rows.

    `profile_party`'s reasoning applies here word for word and is the reason this reads
    flat: *"every clause here is a row somebody can go and check, and a fluent biography
    generated by a model would be an `inferred` fact wearing the costume of an `asserted`
    one."*

    **Measured and reported facts get separate clauses.** `SCOPE-RESET.md` §2a rule 1 and
    Pillar 10 §7's standing warning — *never render an estimated share next to a measured
    one* — are usually read as instructions about the interface. They apply here too,
    because this text lands in `party_document` where a human reads it, not only in the
    embedding. One clause holding "tech house (from the audio), disco house (somebody
    said so)" would launder the difference the moment anyone quoted it.

    Returns `""` for a creator with no sounds. `profile_party` refuses to embed a name
    alone — *"embedding it would put the artist somewhere arbitrary in the vector space
    rather than nowhere. Nowhere is better"* — and a creator with no usage is that case.
    """
    if not usages:
        return ""

    measured_genres: list[str] = []
    bpms: list[float] = []
    reported_genres: list[str] = []
    sounds: list[str] = []
    beat, compared = 0, 0

    for use in usages:
        ref = (use.get("sound_ref") or "").strip()
        if ref and ref not in sounds:
            sounds.append(ref)

        for genre in use.get("genres") or []:
            if genre and genre not in measured_genres:
                measured_genres.append(genre)
        if use.get("bpm"):
            bpms.append(float(use["bpm"]))

        reported = (use.get("asserted_genre") or "").strip()
        if reported and reported not in reported_genres:
            reported_genres.append(reported)

        views, median = use.get("view_count"), use.get("creator_median_views")
        if views is not None and median is not None:
            compared += 1
            if views > median:
                beat += 1

    parts = [f"{handle} is a UGC creator who posts over other people's music."]

    if measured_genres or bpms:
        clause = "Measured from the audio they post over: "
        bits = []
        if measured_genres:
            bits.append(", ".join(measured_genres))
        if bpms:
            bits.append(f"around {round(sum(bpms) / len(bpms))} BPM")
        parts.append(clause + "; ".join(bits) + ".")

    if reported_genres:
        #: Deliberately a separate sentence, and deliberately says who said it.
        parts.append("Reported by the scout who indexed them: "
                     + ", ".join(reported_genres) + ".")

    parts.append("Sounds posted over: " + "; ".join(sounds[:20]) + ".")

    if compared:
        parts.append(
            f"Performance: beat their own median view count on {beat} of {compared} "
            f"of those sounds.")

    return " ".join(parts)


def intake(conn: psycopg.Connection, tenant_id: str, rows: list[ScoutRow],
           *, actor: str) -> dict[str, int]:
    """Land a parsed paste: creators as suggestions, sounds as evidence, one lead each.

    **Nothing here becomes contactable.** A creator arrives as a `party` plus a `pending`
    `suggestion`, which `009`'s queue requires a human to accept before the party is
    treated as a real counterparty. That is the same gate an inferred party from any
    other source passes through, and it is why this function can be exposed to a text box
    without inventing a new compliance regime: `018`'s rules apply downstream unchanged.
    No `contact_route` is written here at any confidence — a creator becomes reachable
    only when somebody records a route for them, and `sender._prepare` refuses an
    `inferred` one outright rather than ranking it lower.

    Idempotent by construction, because a scout re-pasting an overlapping list is the
    normal case rather than the exceptional one: the party is keyed on
    `(tenant_id, slug)`, the evidence on `sound_usage_once`, and the lead on
    `(tenant_id, target_hash)`. Running the same paste twice changes nothing and reports
    zero new rows, which is a truthful answer rather than a silent one.

    The whole batch lands in one transaction. A paste that half-applied would leave
    creators with no evidence and evidence with no creators, and the scout would have no
    way to tell which half they were looking at.
    """
    landed = {"creators": 0, "sounds": 0, "suggestions": 0, "leads": 0}

    with conn.transaction():
        with conn.cursor() as cur:
            for row in rows:
                slug = f"{row.platform}-{row.handle}"

                #: `RETURNING` alone would give nothing back on a conflict, so the
                #: re-select below is what makes a re-paste resolve to the existing
                #: party rather than skipping its sounds.
                cur.execute(
                    """INSERT INTO party (tenant_id, slug, name, kind, party_class,
                                          contact_state)
                       VALUES (%s, %s, %s, 'creator', 'counterparty', 'contactable')
                       ON CONFLICT (tenant_id, slug) DO NOTHING
                       RETURNING id""",
                    (tenant_id, slug, row.handle),
                )
                got = cur.fetchone()
                if got is not None:
                    party_id, fresh = got["id"], True
                    landed["creators"] += 1
                else:
                    cur.execute(
                        "SELECT id FROM party WHERE tenant_id = %s AND slug = %s",
                        (tenant_id, slug))
                    party_id, fresh = cur.fetchone()["id"], False

                #: `party_role` carries no provenance of its own — the standing of a
                #: creator's role lives on the `suggestion` a human has to accept, and
                #: duplicating it here would create two places to disagree.
                cur.execute(
                    """INSERT INTO party_role (tenant_id, party_id, role)
                       VALUES (%s, %s, 'creator')
                       ON CONFLICT (tenant_id, party_id, role) DO NOTHING""",
                    (tenant_id, party_id),
                )

                cur.execute(
                    """INSERT INTO sound_usage (tenant_id, creator_party_id, platform,
                                                sound_ref, asserted_genre, view_count,
                                                creator_median_views, written_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (tenant_id, creator_party_id, platform, sound_ref)
                       DO NOTHING""",
                    (tenant_id, party_id, row.platform, row.sound_ref,
                     row.asserted_genre, row.view_count, row.creator_median_views,
                     f"manual_scout:{actor}"),
                )
                landed["sounds"] += cur.rowcount

                if fresh:
                    #: Only a newly-created creator needs accepting. Re-pasting to add a
                    #: second sound to somebody already accepted must not reopen the
                    #: decision — that would be the queue nagging about settled work.
                    cur.execute(
                        """INSERT INTO suggestion (tenant_id, party_id, kind, payload,
                                                   confidence, rationale, state)
                           VALUES (%s, %s, 'creator_scout', %s, 0.6, %s, 'pending')""",
                        (tenant_id, party_id,
                         psycopg.types.json.Json({
                             "handle": row.handle, "platform": row.platform,
                             "first_sound": row.sound_ref, "scout": actor}),
                         f"@{row.handle} posted over {row.sound_ref} on {row.platform}; "
                         f"read off the sound page by {actor}"),
                    )
                    landed["suggestions"] += 1

                #: One profiling lead per creator, not per sound. `target_hash` collapses
                #: the repeats; a creator who gains a fourth sound is re-profiled by
                #: `--requeue profile_creator`, the same explicit hand-crank
                #: `index_stations` documents, rather than by a silent re-run.
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                         party_id, target, target_hash, platform,
                                         reason, score)
                       VALUES (%s, 'party', 'profile_creator', 'auto', 'internal',
                               %s, %s, %s, %s, %s, 0.7)
                       ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                    (tenant_id, party_id, str(party_id),
                     f"profile_creator:{party_id}", row.platform,
                     "creator indexed from a sound page, not yet profiled or embedded"),
                )
                landed["leads"] += cur.rowcount

    return landed


def profile_creator(conn: psycopg.Connection, lead: dict[str, Any],
                    gate: spend.Gate) -> fleet.Outcome:
    """Compose one creator's profile from `sound_usage`, then queue embedding.

    Network-free by construction, which is why it carries the plain `fleet.Agent` shape
    and no `adapters` entry in `agent_manifest`: everything it reads is already in this
    database, put there by a person. `work_once` runs it inside the transaction that
    writes `agent_run` and completes the lead, and nothing here may block that.

    The join to `party_fact` is what makes a creator's vibe *measured* rather than
    asserted. A `sound_usage` row whose `recording_id` is set points at audio this system
    analysed, so the genre and BPM come off `analyse_recording`'s output. A row without
    one contributes only its `sound_ref` and whatever the scout typed — legitimate, and a
    different standing, which `compose_profile` keeps in a different sentence.
    """
    party_id = lead.get("party_id") or lead["target"]
    tenant_id = lead["tenant_id"]

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM party WHERE tenant_id = %s AND id = %s",
                    (tenant_id, party_id))
        party = cur.fetchone()
        if party is None:
            raise fleet.LeadFailed("the creator is gone", permanent=True)

        cur.execute(
            """SELECT u.sound_ref, u.asserted_genre, u.recording_id,
                      u.view_count, u.creator_median_views,
                      coalesce(array_agg(f.value_text) FILTER (
                          WHERE f.dimension = 'genre' AND f.status = 'live'), '{}')
                          AS genres,
                      max(CASE WHEN f.dimension = 'bpm'
                               THEN f.value_text::FLOAT8 END) AS bpm
                 FROM sound_usage u
                 LEFT JOIN party_fact f
                        ON f.tenant_id = u.tenant_id
                       AND f.recording_id = u.recording_id
                WHERE u.tenant_id = %s AND u.creator_party_id = %s
                GROUP BY u.id, u.sound_ref, u.asserted_genre, u.recording_id,
                         u.view_count, u.creator_median_views
                ORDER BY u.observed_at DESC""",
            (tenant_id, party_id),
        )
        usages = [dict(row) for row in cur.fetchall()]

    body = compose_profile(party["name"], usages)
    if not body:
        #: Not a permanent failure. A creator pasted ahead of their sounds is a normal
        #: ordering, and the lead should come back when the rows land — not be parked.
        raise fleet.LeadFailed(
            "no sound usage recorded for this creator yet, so there is nothing to "
            "profile — index a sound they posted over first", permanent=False)

    from spindle.agents import content_hash

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'internal', '', %s, %s, %s, 'text/plain', 'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"],
                 f"{party['name']} — composed creator profile", body, content_hash(body)),
            )

    return fleet.Outcome(
        summary=f"profiled {party['name']} from {len(usages)} sound(s)",
        documents=1,
        follow_on=[{
            "kind": "embed_party", "adapter": "internal", "party_id": str(party_id),
            "scope_kind": "party", "target": str(party_id),
            "target_hash": f"embed_party:{party_id}",
            "reason": "creator profile composed, not yet searchable", "score": 0.7,
        }],
    )
