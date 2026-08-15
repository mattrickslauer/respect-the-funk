-- =============================================================================
-- 023_podcast_source.sql — podcasts become a source, and a stage that will read it
-- =============================================================================
--
-- The counterparty index has been a radio index. `fcc.py` gives it which stations exist,
-- `radiobrowser.py` gives it what they play, `wikipedia.py` fills the genre gaps — three
-- adapters, one channel. This migration adds the second channel.
--
-- The case for podcasts is not that there are a lot of them. It is that a podcast names
-- the person who decides. A station's music director is a human the FCC register does not
-- record and the stream directories have never heard of, so every FM row in this database
-- is a counterparty we can describe and cannot yet address. A podcast feed carries
-- `<itunes:owner>` and a link to the show's own site, which is the difference between a
-- shortlist and a campaign — and `contact_route` (migration 018) is the table that has
-- been waiting for a source that can fill it.
--
--
-- ## Two rows, and they are not the same kind of registration
--
-- `source_manifest` says a source *exists* and whether it may be called. `agent_manifest`
-- says a pipeline stage exists and what it touches. This migration writes one of each,
-- and both land in a state that is honest about what has actually shipped.
--
--
-- ## Why `podcast_index` lands disabled, with `auth = 'key'`
--
-- 005's registry comment sets the rule: every row lands disabled, and turning one on is
-- an UPDATE rather than a deploy. It is the right posture here for a stronger reason than
-- tidiness — this source **cannot be called at all** without a key and secret, and
-- `spindle.podcastindex.credentials()` raises `NotConfigured` when either is absent
-- rather than returning empty strings. A row enabled before the credential exists would
-- produce a stage that fails every lead with a perfectly good error message nobody asked
-- for. `disabled_reason` therefore names the two environment variables, because the
-- console renders that string and it is the only place an operator will look.
--
-- `auth = 'key'` is load-bearing beyond documentation. `ingest.discover_sources` queues a
-- name search against every source where `enabled AND auth = 'none'`, so a keyed source
-- is structurally excluded from the keyless discovery sweep and cannot be swept into by a
-- later change that forgets this one exists.
--
-- `cost_class` is `free`: Podcast Index charges nothing and publishes no quota. It is not
-- `quota` — that class means a measured daily budget, and inventing a number for a limit
-- the service does not publish would be exactly the confident guess `SCOPE-RESET §2a`
-- rule 1 is about. `rate_per_sec` is 1, chosen conservatively for a non-commercial
-- service answering for free, and it is a courtesy rather than a measurement.
--
--
-- ## Why the `index_podcasts` stage row lands `enabled = false`
--
-- Migrations 017, 019 and 022 all exist because `agent_manifest` and `agents.REGISTRY`
-- drifted, and all three say the same thing: never ship one without the other. This
-- migration ships the manifest row while `agents.REGISTRY` does not yet carry
-- `index_podcasts`, and that is a deliberate exception which needs its argument stated
-- rather than assumed.
--
-- The workstream that adds this source owns the adapter (`podcastindex.py`), the seeding
-- stage (`ingest.index_podcasts`) and this migration. It does not own `agents.py`, which
-- is being changed concurrently by other work; adding the agent there is the next change,
-- not this one.
--
-- So the choice is between declaring the stage and not declaring it, and declaring it is
-- the option that makes the gap **visible**. `research.fleet` cross-references this table
-- against the running code and puts a kind with a manifest row and no registry entry in
-- its "declared, not running" branch — which is precisely and accurately what this is.
-- Omitting the row would leave the drift real and invisible instead, which is the failure
-- 017 was written about. `enabled = false` means the console does not present it as
-- working; the same change that adds the agent flips it to true and this comment stops
-- being true, which is the intended trigger for deleting it.
--
-- Nothing can claim these leads meanwhile: `fleet.work_once` dispatches on
-- `agents.REGISTRY` and never reads this table, so a lead of a kind the registry does not
-- know simply waits. And no such lead can exist yet either — `ingest.index_podcasts`
-- calls `podcastindex.credentials()` before it inserts anything, so an unconfigured
-- deployment cannot seed a frontier it has no way to work.
--
--
-- ## What the stage writes, and what it deliberately does not
--
-- `writes` lists `party`, `party_role`, `party_identifier`, `party_fact`,
-- `party_document`, `presence` and `lead` — the same seven `index_streams` touches, and
-- for the same reasons. `contact_route` is **not** in the list, and its absence is the
-- point: Podcast Index does not publish the owner's email address, and this stage does
-- not synthesise one from the show's domain. 018's own header names a pattern guess as
-- the fastest route to a spam complaint. The show's website lands in `presence`, which is
-- a surface you may read; the route to send to is read off that page later, by a stage
-- that can say where it got it.
--
-- `requires_human` is false, on the same test `index_stations` passes: there is no
-- identity guess here. A Podcast Index feed ID is a canonical key for "this show", so
-- existence and ownership are keyed facts rather than a name match. The one thing the
-- stage infers — `show_kind`, from the publisher's own category choice — is written with
-- `provenance = 'inferred'` and never promoted, which is what `suggestion` would
-- otherwise have been protecting against.
--
-- `batch_size` 2 and `lease_seconds` 600 are copied from `index_streams` rather than
-- guessed anew, because the shape of the work is the same: one HTTP call, then up to a
-- few hundred rows each touching six tables, on a CockroachDB BASIC cluster whose
-- request-unit budget is the real constraint. A lease that expires mid-block hands the
-- work to a second worker that redoes all of it, which is worse than a small batch.


INSERT INTO source_manifest
    (platform, adapter, accepts, emits, yields, subject_kinds,
     auth, cost_class, rate_per_sec, quota_per_day, cadence_seconds, disabled_reason)
VALUES
    ('podcast_index', 'podcast_index',
     ARRAY['podcast_guid', 'podcastindex_feed_id', 'itunes_id', 'feed_url'],
     ARRAY['podcastindex_feed_id', 'itunes_id', 'feed_url', 'url'],
     ARRAY['identity', 'catalogue'], ARRAY['party'],
     'key', 'free', 1, NULL, 604800,
     'needs PODCASTINDEX_API_KEY and PODCASTINDEX_API_SECRET — both are free and '
     'issued immediately at https://api.podcastindex.org/signup')
ON CONFLICT (platform) DO NOTHING;


INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('index_podcasts', 'lead', 'pending', ARRAY['tenant'], ARRAY['podcast_index'],
        ARRAY['party', 'party_role', 'party_identifier', 'party_fact',
              'party_document', 'presence', 'lead'],
        2, 0, 600, 4, ARRAY[60, 300, 1800, 7200], false, false, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    updated_at = now();
-- `enabled` is deliberately absent from the DO UPDATE list. Re-applying this migration
-- after the agent has landed and been switched on must not switch it back off; the
-- INSERT's `false` is a starting state, not a standing assertion. `019` and `022` both
-- carry `enabled = excluded.enabled` because for those the row and the code shipped
-- together and re-asserting true was correct. Here it would not be.
