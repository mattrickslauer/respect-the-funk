# Catalogue presence — one recording, every platform

*Design spec. 2026-08-08. Follows `2026-08-07-agent-contract-design.md` and
`2026-08-07-forager-agent-design.md`.*

## 1. The decision

**A track is the work.** `track.isrc` is its identity when present, and a new
`track_presence` table records where that recording exists on each platform. There is
no separate `work` entity above `track`: `track.isrc` already exists, one new table
carries the whole idea, and nothing in the console moves.

**An ISRC probes every platform, regardless of where the ISRC came from.** It does not
matter whether it arrived from Spotify, from MusicBrainz, from a distributor CSV or
from an operator typing it in. If we hold an ISRC, every source that can accept one
gets asked about it.

That second sentence is the requirement that shapes the mechanism, and §6 is about
taking it literally rather than approximately.

## 2. What Spotify actually gives us

This has to be said before anything downstream depends on it being otherwise.

**There are no stream counts in the Spotify Web API.** The only traction signal is
`popularity`, a 0–100 index that is relative, recency-weighted, and explicitly not
convertible into plays. And since **27 November 2024**, `audio-features`,
`audio-analysis`, `recommendations`, `related-artists` and featured-playlists return
403 for *new* applications. Ours is a new application. `related-artists` would have
been the obvious discovery edge; it is not available and nothing here may assume it.

What we do get, free:

| Object | Useful fields |
|---|---|
| Artist | `followers.total` (absolute), `popularity` (0–100), `genres`, images |
| Album | name, `release_date`, `total_tracks`, `album_type`, `label`, images |
| Track | name, `duration_ms`, `explicit`, `popularity`, **`external_ids.isrc`** |
| Top tracks | per market — a regional traction proxy |

Real stream counts come from **Spotify for Artists** (no public API; needs a label or
artist grant) or from **the distributor's own reports**. Both are ingest paths — a
file or a feed we are given — not fetch paths. They are out of scope here and should
be designed as an import, not as a scrape.

This costs the feedback loop less than it sounds. `2026-08-07-agent-contract-design.md`
§9 already placed stream counts at rung 4, *report only, never train*. The change is
that the chart gets an honest label, not that a signal disappears.

## 3. ISRC is the identity of a recording

An ISRC is assigned once, per recording, by whoever distributes it, and it travels to
every DSP. That makes it the join key that turns "the same song in different places"
from a fuzzy-matching problem into a lookup.

Matching carries its provenance class, exactly as §2a of the contract spec requires:

| Matched on | Class | Meaning |
|---|---|---|
| ISRC equal | `measured` | the platforms agree on the identifier |
| title + duration ±2s + artist | `inferred` | we think these are the same recording |
| an operator said so | `asserted` | a human took responsibility for the link |

A presence discovered by fuzzy match is **never** written as `measured`, and the
coverage grid renders the two differently (§10). A match we cannot defend is a match
we draw differently, not one we round up.

### 3a. Why presence keys on ISRC and not on `track_id`

`track` is artist-scoped — `artist_id` is `NOT NULL`. A recording featuring two artists
on the same roster is therefore two `track` rows. If presence hung off `track_id` we
would fetch it twice, store it twice, and have two answers to one question.

So `track_presence` is keyed by `(tenant_id, isrc, platform)`. A collaboration becomes
two roster rows pointing at one recording with one set of presences, and both artists'
pages show the same truth because it *is* the same truth. This is why the collab case
needs no special handling anywhere else.

A track with no ISRC has no presence rows, and its empty state says so plainly: we
cannot place a recording we cannot name.

## 4. Schema delta — migration 005

### 4a. `track_presence`

```sql
CREATE TABLE IF NOT EXISTS track_presence (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    -- The recording, not our roster row. See §3a.
    isrc         STRING NOT NULL,
    platform     STRING NOT NULL,

    external_id  STRING NOT NULL DEFAULT '',   -- the platform's id for this recording
    url          STRING NOT NULL DEFAULT '',
    title        STRING NOT NULL DEFAULT '',   -- as that platform spells it
    album_title  STRING NOT NULL DEFAULT '',   -- release context differs per platform
    album_id     STRING NOT NULL DEFAULT '',
    duration_ms  INT,
    released_on  DATE,
    markets      STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],

    -- measured | inferred | asserted — how we decided this is the same recording.
    match_basis  STRING NOT NULL DEFAULT 'measured',
    confidence   FLOAT NOT NULL DEFAULT 1.0,

    -- present: found it. absent: asked, it is not there. unknown: never asked.
    state        STRING NOT NULL DEFAULT 'present',

    source_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    document_id    UUID REFERENCES artist_document(id) ON DELETE SET NULL,

    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT presence_match_basis CHECK (
        match_basis IN ('measured', 'inferred', 'asserted')),
    CONSTRAINT presence_state CHECK (state IN ('present', 'absent', 'unknown')),
    UNIQUE (tenant_id, isrc, platform)
);

CREATE INDEX presence_by_platform ON track_presence (tenant_id, platform, state);
```

**`absent` is a stored answer, not a missing row.** "We asked Deezer and this recording
is not there" and "we have never asked Deezer" are different facts, and only one of
them is a distribution problem worth showing someone. A schema that cannot tell them
apart turns the coverage grid from a finding into a guess.

### 4b. `source_manifest`

The direct analogue of `agent_manifest`: a source is a row plus a function.

```sql
CREATE TABLE IF NOT EXISTS source_manifest (
    platform        STRING PRIMARY KEY,
    adapter         STRING NOT NULL,

    -- The capability graph, declared. See §7.
    accepts         STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],  -- isrc|upc|mbid|…
    emits           STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],
    yields          STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],  -- identity|catalogue|
                                                                  -- metrics|contact
    auth            STRING NOT NULL DEFAULT 'none',   -- none | key | oauth
    -- free: unmetered. quota: free but rationed. dollar: costs money.
    cost_class      STRING NOT NULL DEFAULT 'free',
    rate_per_sec    DECIMAL NOT NULL DEFAULT 1,
    quota_per_day   BIGINT,                            -- NULL = unmetered
    cadence_seconds INT,                               -- re-check interval

    enabled         BOOL NOT NULL DEFAULT false,
    disabled_reason STRING NOT NULL DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT source_cost_class CHECK (cost_class IN ('free', 'quota', 'dollar'))
);
```

`enabled` defaults to **false**. A source that nobody has deliberately turned on does
not get called — the same fail-closed direction as the spend gate.

### 4c. `suggestion`

```sql
CREATE TABLE IF NOT EXISTS suggestion (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id    UUID REFERENCES artist(id) ON DELETE CASCADE,

    kind         STRING NOT NULL,      -- profile | identifier | presence
    payload      JSONB NOT NULL,       -- the row we would write, if accepted
    confidence   FLOAT NOT NULL DEFAULT 0.5,
    rationale    STRING NOT NULL DEFAULT '',

    source_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    state        STRING NOT NULL DEFAULT 'pending',  -- pending|accepted|rejected
    decided_by   STRING NOT NULL DEFAULT '',
    decided_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, artist_id, kind, (payload->>'platform'))
);
```

Accepting a suggestion writes the real row and records the operator in `decided_by`.
The discovery stays `inferred`; the acceptance is what makes it `asserted`. Rejections
are kept, because a suggestion re-proposed every cycle after being refused is the
fastest way to make an operator stop reading the queue.

### 4d. `artist_metric` gains a unit

```sql
ALTER TABLE artist_metric ADD COLUMN IF NOT EXISTS unit STRING NOT NULL DEFAULT 'count';
```

See §8 — this is not cosmetic.

### 4e. Backfill note

`004`'s `artist_metric` has no rows yet, so the `count` default is free. `track.isrc`
already exists and already defaults to `''`, so no data migration is needed; §6's
reconciler picks up whatever is there the first time it runs.

## 5. What each source can give us today

| Source | Cost | Auth | Yields | Note |
|---|---|---|---|---|
| MusicBrainz | free | none (UA required) | identity, catalogue | 1 req/s. The identity hub — MBID carries URL-rels to most DSPs. |
| Spotify | free | oauth (client creds) | identity, catalogue, metrics | No streams. No audio-features/related-artists for new apps. |
| Deezer | free | **none** | identity, catalogue, metrics | Public catalog endpoints need no key. ISRC lookup supported. |
| YouTube Data | quota | key | identity, metrics | Daily unit budget, not dollars. |
| **Apple Music** | **dollar** | oauth | identity, catalogue | **$99/yr Apple Developer Program.** Suggestable via MusicBrainz URL-rels; **not fetchable** under the current no-spend rule. |

Apple Music is the case that proves the design: we can *suggest* the surface for free
from MusicBrainz, show it in the grid as `unknown`, and never call it. It sits in the
manifest with `enabled = false` and `cost_class = 'dollar'` until somebody decides to
pay, at which point it becomes one row update.

## 6. The probe — a reconciler, not a trigger

The requirement is "if we have the ISRC, fetch and suggest all platforms **regardless
of how we acquired it**". Taken literally, that rules out enqueueing from the write
path, because every write path would have to remember — the importer, the Spotify
adapter, the operator's form, a manual `UPDATE`, next month's distributor feed. One
that forgets is a track that is silently never probed, and nothing announces it.

**So the mechanism is set-based.** A reconciler asks a question that is true or false
about the database as it stands, not about how it got that way:

```sql
-- Every (isrc, platform) that should have been asked and has not been.
SELECT DISTINCT t.tenant_id, t.isrc, s.platform, s.adapter, t.artist_id, t.id
  FROM track t
  CROSS JOIN source_manifest s
  LEFT JOIN track_presence p
         ON p.tenant_id = t.tenant_id AND p.isrc = t.isrc AND p.platform = s.platform
 WHERE t.isrc <> ''
   AND s.enabled
   AND 'isrc' = ANY (s.accepts)
   AND (p.id IS NULL
        OR (s.cadence_seconds IS NOT NULL
            AND p.checked_at < now() - (s.cadence_seconds || ' seconds')::INTERVAL))
```

Each result becomes a `lead` — the table already carries `platform`, `adapter`,
`target`, `cadence_seconds` and a `target_hash` unique key, so the fanout rides the
existing frontier, the existing claim query, the existing budget and the existing
Queue view. **No new worker, no new queue, no new screen.**

`target` is `isrc:QZABC2500001`, and `target_hash` over `(tenant_id, target, platform)`
makes re-running the reconciler idempotent. Running it twice enqueues nothing twice;
that is the property that lets it run on a timer without coordination.

The write path may *also* enqueue directly, and probably should — but only as a
latency optimisation. Correctness lives in the reconciler. If the two ever disagree,
the reconciler is right.

**Scope.** The lead is `scope_kind = 'track'`, carrying both `artist_id` and
`track_id`, which satisfies `lead_scope_shape` and keeps per-artist fairness and
per-artist budgets working unchanged. Where one ISRC belongs to two roster tracks
(§3a), the probe attaches to whichever track the reconciler saw first; the presence
row it produces serves both, and the `target_hash` prevents the second from queueing.

## 7. The capability graph — why this is dynamic

Nothing in §6 names a platform. It asks `source_manifest` which sources accept an
`isrc`, and fans out to those. Turning Deezer on is an `UPDATE` and the next
reconciler pass includes it. Turning it off stops the probes. **No deploy, no code
change, no new column.**

Generalised beyond ISRC: each source declares what it `accepts` and what it `emits`,
so the manifests form a graph and resolution is a traversal. From any anchor the
planner asks *what edges can I take from what I now know* — never a hardcoded
Spotify → MusicBrainz → Deezer sequence:

```
spotify_artist_id --spotify--> isrc ------deezer------> deezer_track_id
                                 |
                                 +---musicbrainz--> mbid + url-rels
                                                          |
                                                  apple / bandcamp / discogs
                                                  (suggested, never fetched)
```

Adding a source adds edges, and paths that did not exist light up on their own. Each
hop is a `lead` with a `parent_lead_id`, so the attention trail already answers *why
did we look at Deezer* — "ISRC from a Spotify album, which came from the profile you
added". That trail is the compliance artifact and the debugging tool at once.

**Depth is bounded.** `lead.depth` and the forager's existing `depth < 8` cap apply
unchanged; a source that emits an identifier another source accepts cannot loop
forever.

## 8. Units do not mix

Spotify `popularity` is a 0–100 index. Deezer `nb_fan` is a count. YouTube `viewCount`
is a count of a different thing. Summing them produces a number with no referent.

`artist_metric` gains `unit`, and the rule is: **never aggregate across `(platform,
metric, unit)`.** A chart may show three series; it may not show their total. This is
the same class of rule as measured/inferred/asserted — mixing is a schema error, not a
display preference — and it belongs in the read API rather than in a comment, so the
function that returns a series refuses to return a cross-unit sum at all.

## 9. Quota is a second currency

`spend.py` is denominated in dollars and defaults to deny, which is right and stays.
But every source in §5 except Apple Music is **free and rationed**. A fleet of free
agents does not overspend; it gets rate-limited, then IP-banned, and the bill for that
arrives as an outage rather than as a charge. The gate cannot currently see it.

So the gate gains a second ceiling, checked the same way and failing the same
direction:

- `source_manifest.rate_per_sec` — a floor on spacing between calls to one source.
- `source_manifest.quota_per_day` — a ceiling on calls per rolling 24h, **summed from
  `agent_run`**, never decremented from a counter. Same reasoning as the dollar
  budget: a counter row is a serialization point under `SERIALIZABLE`, and ten workers
  retrying against it is exactly the contention the budget was meant to avoid.
- A refusal writes `agent_run.refused_json` with the quota that stopped it, so "why
  did nothing happen last night" has an answer in the Runs view.

`COSTS.md` gains a rule: **free is rationed, and the ration is a budget.**

## 10. How it is displayed

### 10a. The coverage matrix — the thing worth building

Rows are recordings (ISRC), columns come from `source_manifest` where
`'catalogue' = ANY (yields)`. **The columns are data**, so enabling a source adds a
column with no front-end change.

```
RECORDING              ISRC            SPOTIFY  DEEZER  YOUTUBE  APPLE  MUSICBRAINZ
Hollow Ground          QZABC2500001    ●        ●       ●        ○      ●
Slow Return            QZABC2500002    ●        ✕       ●        ○      ●
Nightline (feat. …)    QZABC2500003    ●        ◐       —        ○      ✕

● present (measured)   ◐ present (inferred match)   ✕ absent — asked, not there
○ unknown — source not enabled      — never asked
```

**The gaps are the product.** "Slow Return is on Spotify and YouTube but not on
Deezer" is a distribution defect that costs real money every month it persists, and no
small label has a screen that shows it. That is the row that justifies the whole
integration.

Colour encodes provenance, not sentiment: a `measured` ISRC match and an `inferred`
title match must not look the same, because acting on the second one is a different
decision.

Selecting a cell fills the inspector with that platform's id, its own spelling of the
title, the album context it sits in, the URL, and the `fact_basis` chain down to the
document the answer came from — the same chain kind the pane already renders.

### 10b. Elsewhere

- **Artist inspector** gains a Presence strip: surfaces configured, surfaces
  suggested, surfaces missing. The suggestions are one click from accepted.
- **Approvals** is where suggested surfaces queue. It already exists and is already
  the "what needs me" screen.
- **Tracks** gains a presence count column — `4/6 platforms` — and the existing spark
  column finally carries a real series, labelled by unit per §8.

## 11. Order of work

1. **Migration 005** — `track_presence`, `source_manifest`, `suggestion`,
   `artist_metric.unit`. Seed manifest rows for musicbrainz, spotify, deezer, youtube,
   apple_music, all `enabled = false`.
2. **The reconciler** — §6's query behind one function, plus its idempotence test.
   This can be exercised with zero network calls and a hand-written `track` row, which
   is why it comes before any adapter.
3. **`musicbrainz` and `deezer` adapters** — both free, neither needs a credential, so
   they are the only two that can run under the current no-spend rule without asking
   anyone for anything.
4. **The coverage matrix** — reading real `track_presence` rows.
5. **`spotify` adapter** — needs a client id and secret from the user (§13).
6. **Quota ceilings in the gate** (§9). Before, not after, anything runs on a timer.

Steps 1–4 need nothing from anyone and cost nothing.

## 12. What this deliberately does not do

- **No stream counts.** §2. Any surface claiming to show streams would be showing
  `popularity` with a lie for a label.
- **No scraping of play counts.** Third-party scrapers exist; they violate the ToS we
  would be relying on, and pillar 10 already rules them out.
- **No Apple Music fetching** until somebody decides to spend $99.
- **No automatic acceptance of a suggestion.** An `inferred` platform match becomes an
  `asserted` fact only when a person says so.
- **No `work` entity above `track`.** Revisit only if a recording needs to exist with
  no roster artist attached at all.

## 13. Unverified / needs the user

- **Spotify client id + secret.** Free to register, but it is an account action nobody
  else can take. Until then the Spotify adapter cannot run at all.
- Whether Deezer's ISRC lookup covers every recording we care about, or only those in
  its own catalogue metadata. Assumed the latter; unverified.
- Whether MusicBrainz URL-relationships are populated densely enough for a small
  independent roster to be worth the lookup. Likely sparse for new artists — the
  suggestion path may return nothing for exactly the acts we care most about, and the
  grid should say "no links known" rather than imply absence.
- YouTube's daily unit cost per search vs per video lookup, which decides whether it
  belongs in the fanout at all.
