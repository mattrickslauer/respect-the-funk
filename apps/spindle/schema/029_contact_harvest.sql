-- =============================================================================
-- 029_contact_harvest.sql — the stage that makes a ranked station a reachable one
-- =============================================================================
--
-- `018_contact_route.sql` built the table and named the gap it closes: the system has
-- known *who* a counterparty is since 009 and *where they are* since 005, and could not
-- say **how to reach them** — *"the gap that stops a shortlist from becoming a campaign."*
--
-- Measured against the live cluster on 2026-08-13, that gap was still the whole picture:
--
--     party (party_class = 'counterparty')            14,170
--     contact_route rows                                   1
--
-- The one row is the delivery self-test address a human typed into `ingest.send_self_test`.
-- Every other counterparty in the index is a row a shortlist can rank and nobody can
-- write to. `018` shipped the table with an `index_stations` contact-enrichment step named
-- as future work; this is that step, and `contacts.py` is the code.
--
--
-- ## What the stage reads, and why that is not the scraping Pillar 10 forbids
--
-- None of the four sources this index is built from carries a contact. `fcc.py`'s own
-- header says the Commission knows the licensee and not the music director; Radio Browser
-- publishes a `homepage`, which `index_streams` correctly writes as a `presence` because
-- *"a homepage is a surface you can read, a contact route is an endpoint you may send
-- to"*; `podcastindex.py` records that the index does not expose `<itunes:owner>`'s email
-- and refuses to manufacture one; Wikipedia's infobox has a website and no address.
--
-- What is left is the station's own site — the page a broadcaster publishes *in order to
-- be contacted*. `docs/research/10-creator-indexing.md` §5's rule is **no scraper**, and
-- its stated reason is not chiefly legal: it is that enforcement against a small operator
-- is technical, and a ban costs us the accounts the campaign runs on. That reasoning is
-- about *platforms* — a host with terms of service, an account to lose and an anti-bot
-- team. Fetching a station's contact page over the open web, once, with a truthful
-- User-Agent, after asking its `robots.txt`, has no account behind it and nothing to ban.
--
-- The distinction is only worth anything if it has an edge, so the stage has three:
-- it reads only hosts a counterparty already has a `presence` row for; it follows only
-- same-site links; and it stops at one hop, at most three pages past the homepage.
--
--
-- ## Why this migration must move `contact_country` forward from 024
--
-- `024_regional_by_row.sql` adds `contact_route.contact_country` and says, in terms, who
-- has to fill it: *"Every writer of `contact_route` must now supply `contact_country`. As
-- of this migration the writers are `ingest.send_self_test` and whatever the
-- `index_stations` contact-enrichment step grows into."* This is that step, and it cannot
-- write a row without the column.
--
-- 024 cannot simply be applied first. Its statements 6–9 add two cloud regions to
-- `defaultdb`, which needs a Standard cluster that has not been provisioned, is not
-- reversible once done, and is gated on a human and a budget. The residency *signal* has
-- no such dependency: a nullable `STRING` and a shape `CHECK` are ordinary DDL on the
-- Basic cluster we have.
--
-- So statements 1 and 2 below are 024's statements 1 and 3, lifted forward verbatim. Both
-- are written to be applied twice: `ADD COLUMN IF NOT EXISTS`, and `DROP CONSTRAINT IF
-- EXISTS` before `ADD CONSTRAINT`, which is exactly how 024 wrote them. Applying 024
-- afterwards re-runs both as no-ops and then does its own work — the backfill, the
-- `SET NOT NULL`, the regions — on a column that is already there and already populated
-- for every row this stage has written. Nothing in 024 needs editing and nothing about
-- its argument changes.
--
-- What is deliberately **not** lifted forward is 024's statement 5, `ALTER COLUMN
-- contact_country SET NOT NULL`. The column stays nullable here. Making it NOT NULL is
-- 024's gate, positioned where it belongs — immediately before the table becomes
-- `REGIONAL BY ROW`, at which point a row with no country genuinely cannot be placed. Its
-- job is to stop the multi-region conversion, not to stop this stage, and asserting it
-- early would break `ingest.send_self_test`, which is the one existing writer and passes
-- no country today. `contacts.py` does not rely on the constraint: `country_for` raises
-- before the insert when it cannot establish a country, and raising in Python and failing
-- in the database are the same refusal at two different distances from the operator.
--
--
-- ## Where the country comes from, and the branch that does not exist
--
-- `contacts.country_for` reads it off evidence already on the row and refuses otherwise:
--
--   * an `fcc_facility_id` identifier means `US`. 024 argues this and the argument holds:
--     a broadcast licence is issued under 47 U.S.C. to a licensee in the United States or
--     its territories, so for a party carrying a facility ID this is a measured fact about
--     the source rather than an inference about the party.
--   * a live `country_code` fact, which `streams.refresh_stream` writes from Radio
--     Browser's own `countrycode` field. `031_stream_refresh.sql` is that stage.
--
-- There is no third branch and no `ELSE`, for the reason 024 spends its longest section
-- on. The plausible third branches are all traps: the homepage's TLD (`.fm` is Micronesia
-- and is used by radio stations on four continents), and the FCC city of licence, whose
-- two-letter state code is the alpha-2 collision 024 names — ISO `AL` is Albania and FCC
-- `AL` is Alabama. A party whose country cannot be established is a party whose contact
-- details are not harvested, and it fails loudly saying which evidence is missing.
--
-- Measured on 2026-08-13, that gate is not theoretical: of 6,314 counterparties with a
-- `web` presence, **872** carry an FCC facility ID and can be harvested today, and 5,442
-- cannot until `refresh_stream` has written them a country.
--
--
-- ## Why `requires_human` is false
--
-- The same test `018` applied to `index_stations`: is there an *identity* guess here? No.
-- The stage is told which party to read by a `presence` row somebody already wrote, and it
-- reads that party's own site. There is no name match, no candidate set and no "is this
-- the same organisation" question — which is what `suggestion` exists for, and what
-- produced thirteen junk parties from the Deezer harvest in August.
--
-- The judgement it *does* make is whether a string on a page is a contact, and that
-- judgement is constrained to the point of being mechanical: `contacts.plan` re-checks
-- every email and phone value against the bytes it was read from and raises
-- `NotPublished` if it is not there verbatim. There is no path through that module which
-- emits an address the station did not publish, so the `inferred` pattern-guess `018`
-- warns about — `music@<domain>` — is unrepresentable rather than merely discouraged.
--
-- `writes` names `contact_route` and `party_document` and nothing else. In particular it
-- does not name `party`: the stage does not touch `contact_state`, because 009's
-- `unusable` means "this counterparty cannot be reached" and "we did not find an address
-- on their website" is a weaker claim that would silently shrink every shortlist.
--
-- `batch_size` is 6 rather than `index_stations`' 2. A jurisdiction's FM Query response is
-- a 90-second fetch and hundreds of writes; a station homepage is a few tens of kilobytes
-- and at most four fetches, so more of them fit in a lease. `lease_seconds` is 300 for the
-- same arithmetic: four pages at a 20-second timeout is 80 seconds of worst case, and the
-- rest is headroom.
--
-- ## What was verified against the live cluster, and one thing learned by accident
--
-- Every statement below was executed against `respect-the-funk` on 2026-08-13 inside an
-- explicit transaction that was then rolled back, to prove the SQL parses and binds
-- before anybody applies it for real. The two `INSERT`s rolled back cleanly and
-- `agent_manifest` was unchanged afterwards.
--
-- **The three `ALTER TABLE`s did not.** CockroachDB runs a schema change as a job outside
-- the transaction's control, so `ADD COLUMN IF NOT EXISTS contact_country` and
-- `ADD CONSTRAINT route_country_shape` were still on the table after the `ROLLBACK`. They
-- were removed again immediately — `contact_route` holds one row and the column held no
-- data — and `contact_route` is back to its `018` shape. Recorded here rather than
-- quietly fixed, for two reasons: a rolled-back transaction is not a dry run for DDL on
-- this engine, which is worth knowing before somebody else tries the same check; and the
-- column having briefly existed and been dropped is a fact about this cluster's history
-- that a reader of `SHOW JOBS` would otherwise find unexplained.
--
-- Until this file is applied for real, `contacts._write_harvest_contacts` fails on the
-- missing column with an `UndefinedColumn` naming `contact_country`. No preflight check
-- was added for that, on `024`'s own reasoning about the same requirement: the missing
-- column is *"the most useful error the database can produce"*, and a Python guard would
-- restate it less precisely and once per lead.
--
--
-- `cadence_seconds` is deliberately absent, which makes these leads terminal rather than
-- polling. A station that publishes a new address next year is a re-harvest somebody asks
-- for with `--requeue harvest_contacts`, on the same argument `ingest.requeue` makes: a
-- silent re-fetch nobody asked for is not a feature, and re-reading personal data on a
-- timer least of all.

ALTER TABLE contact_route ADD COLUMN IF NOT EXISTS contact_country STRING;

ALTER TABLE contact_route DROP CONSTRAINT IF EXISTS route_country_shape;

ALTER TABLE contact_route ADD CONSTRAINT route_country_shape
    CHECK (contact_country IS NULL OR contact_country ~ '^[A-Z][A-Z]$');


-- The stage itself. `ON CONFLICT (kind) DO UPDATE` omits `enabled`, following
-- `027_enable_podcasts.sql`'s reasoning: re-applying this file must not switch a stage
-- back on that an operator turned off from the console.

INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('harvest_contacts', 'lead', 'pending', ARRAY['party'], ARRAY['station_website'],
        ARRAY['contact_route', 'party_document'],
        6, 0, 300, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    scope_kinds = excluded.scope_kinds,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    updated_at = now();
