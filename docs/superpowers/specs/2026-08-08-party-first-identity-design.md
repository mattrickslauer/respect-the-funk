# Party-first identity, and the industry's four layers

*Design spec. 2026-08-08. Supersedes `2026-08-08-catalogue-presence-design.md`, which
was track-first and used the word "work" incorrectly. Follows
`2026-08-07-agent-contract-design.md` and `2026-08-07-forager-agent-design.md`.*

## 1. The decision

**A Party is the root entity.** An artist on our roster, a songwriter, a publisher, a
label, a distributor, a UGC creator, a radio programmer, a playlist curator and a
journalist are all Parties. They differ by the **role** they play relative to us, not
by their type in the schema.

**The catalogue has four layers, and they are the industry's, not ours:**

| Layer | Industry term | Identifier | Issuer |
|---|---|---|---|
| Who | **Party** | **ISNI** (ISO 27729), **IPI/CAE**, DPID | ISNI-IA, CISAC, DDEX |
| The song as written | **Work** | **ISWC** (ISO 15707) | CISAC |
| The master | **Recording** | **ISRC** (ISO 3901) | IFPI |
| The product | **Release** | **GTIN** (UPC/EAN), **GRid** | GS1, IFPI |

One Work has many Recordings. One Release contains many Recordings. Parties are
credited against all three.

**The previous spec called a track "the work". That was wrong.** A Work is the
composition; a Recording is the master. A cover shares the original's ISWC and gets
its own ISRC. Using the industry's word for the wrong thing would have confused
everyone we hire and broken the moment publishing or sync entered.

## 2. Why party-first — the collapse it buys

We had arrived at the same shape twice from different directions and not noticed:

- **`artist` + `artist_profile`** — a name, and where it exists on each platform.
- **The counterparty index** — creators, programmers, curators and press, each a name,
  with handles on platforms and a state of relationship.

Those are one table. A UGC creator is a Party with a presence on TikTok; Amanda Kurt
is a Party with a presence on Spotify. The difference is `party_role`, and roles are
**many per party** — which matters immediately, because at an independent label the
artist is very often also the writer and sometimes also the label.

What that collapse gives us for free:

- **One identifier system.** ISNI and IPI are Party identifiers. So is a Spotify artist
  id, a MusicBrainz MBID, a TikTok handle. One table, one resolution path.
- **One presence system.** The probe machinery in §7 that finds an artist on Deezer is
  the same machinery that finds a curator on YouTube.
- **One conversation of record.** Threads attach to a Party, so "every conversation
  goes through our system" needs no second concept.
- **Credits become real.** A featured artist, a producer and a co-writer are Parties
  credited on a Recording, which is what the industry means and what royalty
  conversations require.

**And it dissolves the collab hack.** The superseded spec keyed presence on ISRC rather
than on the track row, because `track.artist_id` was `NOT NULL` and a two-artist
recording was therefore two rows. With Recording no longer owned by an artist and
credits held in `party_credit`, a collaboration is *one* Recording with two
`main_artist` credits. Presence keys on `recording_id` again, with a real foreign key.
The workaround was a symptom of the wrong root entity.

## 3. Identifiers: canonical and raw

Every identifier arrives in more than one spelling, and this is the classic way an
identity system quietly fails:

| Identifier | Same value, different forms |
|---|---|
| ISRC | `QZ-ABC-25-00001` · `QZABC2500001` · lower case |
| GTIN | GTIN-12 `602557123456` · GTIN-13 `0602557123456` — leading zeros |
| ISWC | `T-123.456.789-C` · `T1234567891` |
| ISNI | `0000 0001 2103 2683` · `000000012103268X` |

Two sources hand us the same recording and a naive unique index files them as two.

**So every identifier column is stored twice: `value` canonical, `value_raw` as
received.** Canonical is what joins and what the unique index covers. Raw is evidence
of what the source actually said, which the provenance model in the contract spec
requires us to be able to produce. Normalisation rules live in `domain` beside the
closed vocabularies, not scattered at call sites.

Canonical forms: ISRC uppercase, 12 chars, no separators. GTIN zero-padded to 14.
ISWC uppercase `T` + 10 digits. ISNI 16 chars, no spaces.

## 4. Schema — migration 005

### 4a. Party

```sql
CREATE TABLE IF NOT EXISTS party (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    slug       STRING NOT NULL,
    name       STRING NOT NULL,
    -- person | group | organisation. DDEX's party kinds. Distinct from the
    -- presentation type below: a duo is a `group` whose artist_type is `duo`.
    kind       STRING NOT NULL DEFAULT 'group',
    -- band|solo|dj|producer|… — meaningful only when the party performs. Empty for
    -- a publisher. This is today's `artist.type`, carried over unchanged.
    artist_type STRING NOT NULL DEFAULT '',
    status     STRING NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);

CREATE TABLE IF NOT EXISTS party_identifier (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id   UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    kind       STRING NOT NULL,        -- isni|ipi|dpid|mbid|spotify_artist|…
    value      STRING NOT NULL,        -- canonical, §3
    value_raw  STRING NOT NULL DEFAULT '',
    provenance STRING NOT NULL DEFAULT 'measured',
    source_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- An identifier identifies exactly one party. This constraint IS the dedup
    -- rule: two sources arriving with the same ISNI resolve to the same row or
    -- the write fails loudly, which is the correct direction.
    UNIQUE (tenant_id, kind, value)
);

CREATE TABLE IF NOT EXISTS party_role (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id   UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    -- roster_artist|writer|publisher|label|distributor|producer|performer|
    -- creator|programmer|curator|press
    role       STRING NOT NULL,
    started_on DATE,
    ended_on   DATE,
    UNIQUE (tenant_id, party_id, role)
);

CREATE TABLE IF NOT EXISTS party_relation (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    from_party_id UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    to_party_id   UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    relation   STRING NOT NULL,   -- member_of|signed_to|manages|publishes|booked_by
    started_on DATE,
    ended_on   DATE,
    UNIQUE (tenant_id, from_party_id, to_party_id, relation)
);

CREATE INDEX party_by_role ON party_role (tenant_id, role) STORING (party_id);
```

`party_by_role` is what makes "the roster" a query rather than a table.

### 4b. Work, Recording, Release

```sql
CREATE TABLE IF NOT EXISTS work (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    title      STRING NOT NULL,
    iswc       STRING NOT NULL DEFAULT '',   -- canonical
    iswc_raw   STRING NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX work_by_iswc ON work (tenant_id, iswc) WHERE iswc <> '';

CREATE TABLE IF NOT EXISTS recording (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    slug        STRING NOT NULL,
    title       STRING NOT NULL,
    isrc        STRING NOT NULL DEFAULT '',  -- canonical
    isrc_raw    STRING NOT NULL DEFAULT '',
    duration_ms INT,
    released_on DATE,
    status      STRING NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);
CREATE UNIQUE INDEX recording_by_isrc ON recording (tenant_id, isrc) WHERE isrc <> '';

CREATE TABLE IF NOT EXISTS release (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    title        STRING NOT NULL,
    gtin         STRING NOT NULL DEFAULT '',  -- canonical GTIN-14
    gtin_raw     STRING NOT NULL DEFAULT '',
    grid         STRING NOT NULL DEFAULT '',
    release_type STRING NOT NULL DEFAULT 'single',  -- single|ep|album|compilation
    released_on  DATE,
    label_party_id UUID REFERENCES party(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX release_by_gtin ON release (tenant_id, gtin) WHERE gtin <> '';

CREATE TABLE IF NOT EXISTS recording_work (
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    recording_id UUID NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
    work_id      UUID NOT NULL REFERENCES work(id) ON DELETE CASCADE,
    provenance   STRING NOT NULL DEFAULT 'inferred',
    PRIMARY KEY (tenant_id, recording_id, work_id)
);

CREATE TABLE IF NOT EXISTS release_recording (
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    release_id   UUID NOT NULL REFERENCES release(id) ON DELETE CASCADE,
    recording_id UUID NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
    disc_no      INT NOT NULL DEFAULT 1,
    sequence     INT NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, release_id, recording_id)
);

CREATE TABLE IF NOT EXISTS party_credit (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id     UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    subject_kind STRING NOT NULL,      -- work | recording | release
    subject_id   UUID NOT NULL,
    -- main_artist|featured|writer|composer|producer|performer|remixer|publisher
    role         STRING NOT NULL,
    split_percent DECIMAL,             -- NULL until somebody actually knows
    provenance   STRING NOT NULL DEFAULT 'asserted',
    UNIQUE (tenant_id, party_id, subject_kind, subject_id, role)
);
CREATE INDEX credit_by_subject ON party_credit (tenant_id, subject_kind, subject_id);
```

`party_credit` is authoritative for "whose recording is this". There is no
`recording.primary_party_id`: a denormalised owner column is exactly what forced the
collab workaround last time.

`split_percent` is nullable and stays NULL until someone knows. A default of 100 or an
even split would be a fabricated number in a column people will eventually trust.

### 4c. Presence — one table for all three subjects

```sql
CREATE TABLE IF NOT EXISTS presence (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    subject_kind STRING NOT NULL,      -- party | recording | release
    subject_id   UUID NOT NULL,
    platform     STRING NOT NULL,

    external_id  STRING NOT NULL DEFAULT '',
    url          STRING NOT NULL DEFAULT '',
    handle       STRING NOT NULL DEFAULT '',
    title_seen   STRING NOT NULL DEFAULT '',  -- how that platform spells it
    context_id   STRING NOT NULL DEFAULT '',  -- the release a recording sits in there
    markets      STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],

    -- owned|unowned|absent. Only meaningful for a party: whether they hold the
    -- account. An artist with no TikTok is still worth searching TikTok.
    mode         STRING NOT NULL DEFAULT '',
    -- present: found. absent: asked, not there. unknown: never asked.
    state        STRING NOT NULL DEFAULT 'present',
    match_basis  STRING NOT NULL DEFAULT 'measured',  -- measured|inferred|asserted
    confidence   FLOAT NOT NULL DEFAULT 1.0,
    enabled      BOOL NOT NULL DEFAULT true,

    source_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    document_id    UUID REFERENCES artist_document(id) ON DELETE SET NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT presence_state CHECK (state IN ('present', 'absent', 'unknown')),
    CONSTRAINT presence_match CHECK (match_basis IN ('measured','inferred','asserted')),
    CONSTRAINT presence_subject CHECK (subject_kind IN ('party','recording','release')),
    UNIQUE (tenant_id, subject_kind, subject_id, platform)
);
CREATE INDEX presence_by_platform ON presence (tenant_id, platform, state);
```

This one table replaces `artist_profile` and the per-entity presence tables of the
superseded spec, and it is where the counterparty index will live.

**Polymorphic, like `fact_basis`.** The cost is no foreign key on `subject_id`; the
benefit is that one probe, one reconciler and one grid serve parties, recordings and
releases. The precedent is already set in `004` and the tradeoff is the same one.

**`absent` is a stored answer, not a missing row.** "We asked Deezer and it is not
there" is a distribution defect worth money. "We never asked" is nothing. A schema that
cannot tell them apart turns the coverage grid from a finding into a guess.

### 4d. Source manifest, suggestions, metric unit

`source_manifest` and `suggestion` carry over from the superseded spec unchanged in
intent, with `accepts`/`emits` now naming identifier kinds from the closed vocabulary
of §3, and `suggestion.kind` extended to `party | identifier | presence | credit`.

```sql
CREATE TABLE IF NOT EXISTS source_manifest (
    platform        STRING PRIMARY KEY,
    adapter         STRING NOT NULL,
    accepts         STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],  -- isrc|iswc|gtin|
                                                                  -- isni|ipi|mbid|…
    emits           STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],
    yields          STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],  -- identity|catalogue|
                                                                  -- metrics|contact
    subject_kinds   STRING[] NOT NULL DEFAULT ARRAY['party','recording'],
    auth            STRING NOT NULL DEFAULT 'none',
    cost_class      STRING NOT NULL DEFAULT 'free',   -- free|quota|dollar
    rate_per_sec    DECIMAL NOT NULL DEFAULT 1,
    quota_per_day   BIGINT,
    cadence_seconds INT,
    enabled         BOOL NOT NULL DEFAULT false,
    disabled_reason STRING NOT NULL DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT source_cost_class CHECK (cost_class IN ('free','quota','dollar'))
);

ALTER TABLE artist_metric ADD COLUMN IF NOT EXISTS unit STRING NOT NULL DEFAULT 'count';
```

`enabled` defaults to **false** — fail-closed, like the spend gate.

### 4e. What happens to the `artist_*` tables

`artist`, `artist_profile` and `track` are replaced. `artist_fact`, `artist_document`,
`artist_chunk`, `artist_metric`, `artist_budget` and `lead` keep their shape but their
`artist_id` becomes `party_id` and their `track_id` becomes `recording_id`.

**Do it in one cut, now.** There are three artist rows, zero facts, zero documents,
zero leads and no runtime. The migration is three `INSERT … SELECT`s. Every week of
delay adds rows, adapters and screens that have to be moved instead.

The console survives it because the payoff of `demo.View`/`Section` is exactly this:
`repo.py`, `research.py` and `routes.py` change; **no template changes**, because the
templates were written against a data contract rather than against the schema.

## 5. Roles vocabulary

`domain` gains closed sets beside `ArtistType` and `Platform`, for the same reason:
code branches on these, so a typo must be a rejection rather than a silent orphan.

| Vocabulary | Values |
|---|---|
| `PartyKind` | person, group, organisation |
| `PartyRole` | roster_artist, writer, publisher, label, distributor, producer, performer, creator, programmer, curator, press |
| `CreditRole` | main_artist, featured, writer, composer, producer, performer, remixer, publisher |
| `IdentifierKind` | isni, ipi, dpid, iswc, isrc, gtin, grid, mbid, acoustid, spotify_artist, spotify_track, deezer_artist, deezer_track, youtube_channel, … |
| `SubjectKind` | party, recording, release |

Storage stays `STRING` throughout, for the reason `002_artist_type.sql` already gives:
a value retired from the enum must still load from a row written before it was retired.

## 6. DDEX — the shape, and the way streams actually arrive

DDEX is the industry's data-exchange family, and **its standards are free to
implement**: an Evaluation Licence is automatic, a free Implementation Licence is
required before production, and membership is not required. (Charter membership runs
$29,378/yr as of January 2026 and buys us nothing we need.)

- **ERN** — Electronic Release Notification, the message a label or distributor sends
  a DSP. Its structure is Release → Resource(SoundRecording) → Party carrying exactly
  the identifiers in §1. §4b is deliberately shaped so an ERN maps onto it directly
  rather than through a translator.
- **DSR** — Digital Sales Reporting, how DSPs report usage back to rights owners.
  **This is where real stream counts come from**: per ISRC, per territory, per DSP,
  per period. The superseded spec wrote streams off as unavailable because the Spotify
  Web API has none. That was true and incomplete — they are available, from the
  distributor, in a standard format, as an *ingest*. Rung 4 of the feedback ladder is
  reachable after all, and by the route the industry already uses.
- **RIN** (session metadata) and **MEAD** (marketing metadata) are later, if ever.

DSR ingest is a file-and-parse path with no discovery in it. It does not belong in the
frontier and should not pretend to: an upload lands a document, the parser writes
`artist_metric` rows with `unit = 'count'` and `provenance = 'measured'`, and the
basis edge points at the file. That is the single highest-value integration on this
list and it costs nothing but a parser.

## 7. The probe, generalised

The superseded spec's reconciler was ISRC-shaped. Party-first makes it
identifier-shaped, and the requirement stands unchanged: **if we hold an identifier,
every source that accepts it gets asked, regardless of how we acquired it.**

Which is still why this is a **set-based reconciler and not a write-path trigger**. If
enqueueing lived in the writer, every writer would have to remember — the importer,
each adapter, the operator's form, a manual `UPDATE`, next month's DSR file. One that
forgets is an entity silently never probed, and nothing announces it. A reconciler
asks what is true of the database, not how it got that way:

```sql
-- Every (subject, platform) that should have been asked and has not been.
WITH subject_identifier AS (
    SELECT tenant_id, 'party' AS subject_kind, party_id AS subject_id, kind, value
      FROM party_identifier
    UNION ALL
    SELECT tenant_id, 'recording', id, 'isrc', isrc FROM recording WHERE isrc <> ''
    UNION ALL
    SELECT tenant_id, 'release',   id, 'gtin', gtin FROM release   WHERE gtin <> ''
)
SELECT DISTINCT si.tenant_id, si.subject_kind, si.subject_id, si.kind, si.value,
                s.platform, s.adapter
  FROM subject_identifier si
  JOIN source_manifest s
    ON s.enabled
   AND si.kind = ANY (s.accepts)
   AND si.subject_kind = ANY (s.subject_kinds)
  LEFT JOIN presence p
    ON p.tenant_id = si.tenant_id AND p.subject_kind = si.subject_kind
   AND p.subject_id = si.subject_id AND p.platform = s.platform
 WHERE p.id IS NULL
    OR (s.cadence_seconds IS NOT NULL
        AND p.checked_at < now() - (s.cadence_seconds || ' seconds')::INTERVAL)
```

Each row becomes a `lead`. The table already carries `platform`, `adapter`, `target`,
`cadence_seconds` and a `target_hash` unique key, so this rides the existing frontier,
claim query, budget and Queue view. **No new worker, no new queue, no new screen.**

`target` is `isrc:QZABC2500001` or `isni:000000012103268X`; `target_hash` over
`(tenant_id, target, platform)` makes the reconciler idempotent, which is what lets it
run on a timer with no coordination. A write path may also enqueue directly, but only
as a latency optimisation — correctness lives in the reconciler, and if the two ever
disagree the reconciler is right.

**Nothing above names a platform.** Enabling Deezer is an `UPDATE`.

## 8. The capability graph

Because each source declares `accepts` and `emits`, the manifests form a graph and
resolution is a traversal. From any anchor the planner asks *what edges can I take
from what I now know*, never a hardcoded sequence:

```
spotify_artist --spotify--> isrc ------deezer------> deezer_track
      ^                       |
      |                       +--musicbrainz--> mbid --> isni, iswc, url-rels
   isni/mbid                                              |
                                              apple / bandcamp / discogs
                                              (suggested, never fetched)
```

Adding a source adds edges, and paths that did not exist light up on their own. Each
hop is a `lead` with a `parent_lead_id`, so the attention trail answers *why did we
look at Deezer* — "ISRC from a Spotify release, from the profile you added". That
trail is the compliance artifact and the debugging tool at once. `lead.depth` and the
forager's `depth < 8` cap bound it.

## 9. Matching, and how a match is classified

| Matched on | Class |
|---|---|
| identifier equal (ISRC, ISWC, GTIN, ISNI) | `measured` |
| **AcoustID / Chromaprint** audio fingerprint | `measured` |
| title + duration ±2s + credited party | `inferred` |
| an operator said so | `asserted` |

**AcoustID replaces the hand-rolled heuristic** as the primary fallback when an
identifier is missing. It is free, open, and already linked to MusicBrainz, and it is
the industry-grade version of the job that "title plus duration" was doing badly.

A presence found by fuzzy match is **never** written as `measured`, and §11's grid
renders the two differently. A match we cannot defend is one we draw differently, not
one we round up.

## 10. Units, and quota as a second currency

**Units do not mix.** Spotify `popularity` is a 0–100 index, Deezer `nb_fan` is a
count, DSR streams are a count of something else again. `artist_metric.unit` exists so
the read API can refuse a cross-unit aggregate outright. A chart may show three
series; it may not show their total.

**Quota is a budget.** Every source in §12 except Apple Music is free but rationed. A
fleet of free agents does not overspend — it gets rate-limited, then banned, and that
bill arrives as an outage the dollar gate cannot see. So the gate gains
`rate_per_sec` and `quota_per_day` ceilings, **summed from `agent_run`** rather than
decremented from a counter, for the same `SERIALIZABLE` hot-row reason as money. A
refusal writes `agent_run.refused_json` so "why did nothing happen last night" has an
answer in the Runs view. `COSTS.md` gains the rule: **free is rationed, and the ration
is a budget.**

## 11. How it is displayed

**The coverage matrix** stays the artifact worth building. Rows are Recordings,
columns come from `source_manifest`, cells are present / absent / inferred / unknown.
Columns are **data**, so enabling a source adds a column with no front-end change.

```
RECORDING              ISRC            SPOTIFY  DEEZER  YOUTUBE  APPLE  MUSICBRAINZ
Hollow Ground          QZABC2500001    ●        ●       ●        ○      ●
Slow Return            QZABC2500002    ●        ✕       ●        ○      ●
Nightline (feat. …)    QZABC2500003    ●        ◐       —        ○      ✕

● measured   ◐ inferred match   ✕ absent — asked, not there
○ source not enabled            — never asked
```

**The gaps are the product.** "Slow Return is on Spotify and YouTube but not Deezer"
is a distribution defect costing money every month it persists, and no small label has
a screen that shows it.

Party-first adds three more, all cheap because they are the same table:

- **Parties** replaces Artists as a view, filtered by role. The roster is
  `role = 'roster_artist'`; Counterparties is the same screen with a different filter,
  which is most of that feature delivered by not building it.
- **Party inspector** gains Roles, Identifiers (canonical, with the raw on hover) and
  Credits alongside the presence editor already built.
- **Releases** — a release, its recordings, and its GTIN, which is what an operator
  actually thinks in when they talk about a launch.

## 12. What each source gives us, and what it costs

| Source | Cost | Auth | Yields |
|---|---|---|---|
| MusicBrainz | free | none (UA) | identity, catalogue — the hub: MBID → ISNI, ISWC, url-rels |
| AcoustID | free | key (free) | fingerprint → MBID |
| Deezer | free | **none** | identity, catalogue, metrics |
| Spotify | free | oauth | identity, catalogue, `popularity`/`followers` — **no streams** |
| YouTube Data | quota | key | identity, metrics |
| **DSR from distributor** | free | file | **real streams**, per ISRC per territory |
| **Apple Music** | **$99/yr** | oauth | suggestable via MusicBrainz, **not fetchable** |

## 13. Order of work

1. **Migration 005** — party layer, rights layer, presence, source_manifest,
   suggestion, metric unit. Carry the three artists over as parties with
   `role = 'roster_artist'`.
2. **`domain` vocabularies + identifier normalisation**, with unit tests. Pure
   functions, no network, no database.
3. **Repoint the console** — `repo`/`research`/`routes` to party. Templates untouched.
   The console must not go dark; this step is done when every view still renders.
4. **The reconciler** — §7 behind one function plus its idempotence test. Exercisable
   with a hand-written row and zero network calls.
5. **`musicbrainz` + `deezer` adapters** — free, no credential, the only two that can
   run today without asking anyone for anything.
6. **The coverage matrix.**
7. **DSR parser** — highest value per line of code on this list.
8. **Quota ceilings in the gate**, before anything runs on a timer.
9. `spotify` adapter — needs the credential in §15.

Steps 1–7 need nothing from anyone and cost nothing.

**Risk.** Step 3 is the one that can break a working console ten days before the
deadline. It is sequenced third precisely so it happens while the surface area is
small — three parties, no facts, no leads — rather than after adapters are writing.

## 14. What this deliberately does not do

- **No scraped play counts.** Third-party scrapers exist and violate the terms we
  would be relying on; pillar 10 rules them out. DSR is the legitimate route.
- **No royalty accounting.** `split_percent` exists and stays NULL. Modelling splits is
  not the same as computing money, and we are not doing the second.
- **No automatic acceptance of a suggestion.** An `inferred` match becomes `asserted`
  only when a person says so.
- **No Apple Music fetching** until somebody decides to spend $99.
- **No DDEX XML emission.** We borrow the shape and read DSR. Producing valid ERN is a
  different project with a conformance burden.

## 15. Unverified / needs the user

- **Spotify client id + secret.** Free to register; an account action nobody else can
  take.
- **Whether the distributor exposes DSR files, and in which profile.** This decides
  whether real streams are days away or unavailable. It is the single highest-value
  question on this list and it is a question for the label, not for research.
- Whether a free DDEX Implementation Licence is needed merely to *parse* DSR we
  receive, or only to transmit. Assumed needed before production either way; unverified.
- Whether MusicBrainz url-rels and ISNI coverage are dense enough for a small
  independent roster. Likely sparse for new artists — the grid must say "no links
  known" rather than imply absence.
- YouTube's daily unit cost per search vs per lookup, which decides whether it belongs
  in the fanout at all.
