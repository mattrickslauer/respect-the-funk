-- =============================================================================
-- 032_feed_contact_harvest.sql — the one element in any of our sources whose only
--                                purpose is to say where to write
-- =============================================================================
--
-- `docs/research/14-counterparty-sources.md` surveyed every public source that could stock
-- the empty channels and ranked them by one question — *does it carry a route the
-- publisher declared* — and the ranking it produced is not the expected one. Podcast feeds
-- win, and not narrowly.
--
-- Measured 2026-08-13 against 45 music-category feeds:
--
--     feeds fetched                            45   (0 errors)
--     carrying <itunes:owner><itunes:email>     40   (89%)
--     of those, hosting-platform boilerplate     2
--
-- Two of forty. The rest are the show's own domain (`contact@switchedonpop.com`), a
-- broadcaster (`podcasts@npr.org`), or an independent presenter's personal address — and
-- `023_podcast_source.sql` already argued that the third group is the most valuable
-- counterparty this index can hold, because *"there is no programming department between
-- a pitch and the ear that hears it."*
--
--
-- ## Why this is `measured` without any stretching
--
-- `018_contact_route.sql` reserves `measured` for *"a route the source published — the FCC
-- register's own contact field, a station's published submissions address."* Apple
-- requires `<itunes:owner><itunes:email>` in a submitted feed so that the directory can
-- reach the owner. The element has no other purpose. There is no ambiguity about whose
-- address it is, and none about what it is for.
--
-- That is a materially stronger claim than anything a web page yields, and the difference
-- is the reason `029_contact_harvest.sql`'s website stage and this one are separate
-- stages rather than two branches of one. §11 of the research puts it precisely: *"the
-- difference between a publisher-declared route and a page-scraped one is not a difference
-- of degree."*
--
--
-- ## `podcastindex.py`'s header is correct and this does not contradict it
--
-- > **What is deliberately absent: an email address.** Podcast Index does not publish
-- > `<itunes:owner><itunes:email>`, and this module does not manufacture one from the
-- > show's domain.
--
-- Both halves are true, and both are about the *API*. The API returns the feed URL. The
-- feed is a public XML document on the publisher's own host, one GET away, and reading an
-- element out of it is not manufacturing anything. The module's refusal to synthesise
-- `info@<domain>` stands untouched — `contacts.py` enforces the same refusal structurally
-- for the website stage and this stage has nothing to synthesise from.
--
--
-- ## What was built, and the two things that were not
--
-- Built: `podcastindex.feed_by_id`, `podcastindex.fetch_feed`, `podcastindex.owner`, and
-- the `harvest_feed_contacts` stage in `contacts.py`, with the boilerplate-host filter
-- that drops the two-in-forty.
--
-- **Not built: storing `feed_url` at index time.** The natural home for this whole
-- capability is `_write_index_podcasts`, which holds the feed record with its `url` field
-- already in hand and could write the route in the same transaction as the party. That
-- function is in `agents.py`, which this workstream does not own, so the stage re-asks the
-- index for the URL by feed ID — one extra call per show. If `index_podcasts` later
-- stores the feed URL as an identifier or a `presence`, this stage should read it from
-- there and the call disappears; `_fetch_harvest_feed_contacts` is written so that change
-- is a deletion rather than a rewrite. The same applies to the research's suggestion of
-- adding `contact_route` to `index_podcasts`' `writes` array: that row belongs to `023`
-- and to whoever owns the stage, and asserting it from here would be claiming a stage
-- writes a table it does not.
--
-- **Not exercised.** As of 2026-08-13 the live cluster holds **zero** podcast
-- counterparties. `023` and `027` are both unapplied — `agent_manifest` has no
-- `index_podcasts` row — and `PODCASTINDEX_API_KEY` is not set in this environment. So
-- this stage is complete, covered by offline tests, and has never run against a real feed
-- from this repository. Applying this migration adds a manifest row for a stage whose work
-- table will be empty until `023`, `027`, a key and an `--index-podcasts` run all land.
-- That is stated here rather than left to be discovered from a zero in a report.
--
--
-- ## `contact_country` is written NULL, deliberately, and it blocks 024's gate
--
-- A feed declares an address and a name and says nothing about where its owner is
-- established. There is no `countrycode` as there is for a Radio Browser station and no
-- licence jurisdiction as there is for an FCC one, and `024_regional_by_row.sql`'s
-- objection is to *inventing* a country — its long argument is against `ELSE
-- 'aws-us-east-1'`, not against an honest NULL.
--
-- The consequence is real and is the point: 024's statement 5, `ALTER COLUMN
-- contact_country SET NOT NULL`, will **fail** while any feed-harvested route is
-- unresolved, naming the column, with the runbook's §Resume query listing exactly which
-- rows. That is `024`'s "GATE 1" doing its job. It is also precisely the human decision
-- `14-counterparty-sources.md` §10 item 3 raises — a gmail address on a one-person show is
-- an individual subscriber under PECR even when the show is a business — and a migration
-- that stops until somebody answers it is better than one that answers it by default.
--
-- `addressee` carries `<itunes:name>`, transcribed. §10 notes that this column is already
-- the field a corporate-subscriber-versus-named-individual policy would filter on, so the
-- distinction that decision turns on is stored the moment the route is.
--
--
-- ## Shape
--
-- One lead per show, party-scoped, because the unit of work is one feed and one feed is
-- two HTTP calls. `batch_size` 6 matches `harvest_contacts` for the same arithmetic.
-- `requires_human` is false: there is no identity guess — the stage is handed a party that
-- already carries a `podcastindex_feed_id` and asks that ID's own feed for its own owner.
--
-- No `cadence_seconds`. Re-reading a publisher's declared contact details on a timer is
-- the last thing this table should do; a re-harvest is `--requeue harvest_feed_contacts`.

INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('harvest_feed_contacts', 'lead', 'pending', ARRAY['party'],
        ARRAY['podcast_index', 'rss_feed'],
        ARRAY['contact_route', 'party_document'],
        6, 0, 300, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    scope_kinds = excluded.scope_kinds,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    updated_at = now();
