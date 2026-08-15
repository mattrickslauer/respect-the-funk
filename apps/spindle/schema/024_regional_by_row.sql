-- =============================================================================
-- 024_regional_by_row.sql — an EU contact's row lives in the EU, and the database
--                           is what makes that true
-- =============================================================================
--
-- Migration 018 built `contact_route` and said the honest thing about what it holds:
-- *"send to the wrong presence and you have looked at the wrong page; send to the wrong
-- route and you have mailed a stranger."* What it did not say, because at the time the
-- table held nothing, is what the row **is**: an email address, a phone number, a postal
-- address and an `addressee` — "music_director", a named person's job — belonging to an
-- identifiable individual who never asked us to hold any of it.
--
-- That is personal data under Article 4(1) of the GDPR, under the UK GDPR, under the
-- Swiss FADP, and under most of what has been written since. It is the only table in
-- this schema that is. `party` holds organisations, `party_fact` holds claims about
-- them, `party_metric` holds counts. `contact_route` holds people.
--
-- The obligation that follows is not "encrypt it" — that is Article 32 and we do it by
-- using TLS to a managed cluster. It is Chapter V: a transfer of personal data to a
-- third country needs a legal basis, and "we put the whole table in Virginia because
-- that is where the cluster was" is not one. Since Schrems II the safe answer for an EEA
-- data subject is not to make the transfer at all.
--
--
-- ## Why this is the argument for CockroachDB and not for Postgres
--
-- The obligation is per-row, because it depends on where each individual is. Postgres
-- can meet it in exactly two ways and both are worse:
--
--   1. **One database per jurisdiction.** Three `contact_route` tables, three
--      connection strings, three connection pools, and application code that picks one
--      before it can read a row. Every query that spans regions — "who can we reach on
--      email right now", which is the outreach stage's only question — becomes a fan-out
--      and a merge in Python. Every foreign key from `contact_route` to `party` either
--      breaks or gets replicated. The uniqueness constraint that stops a harvester from
--      resurrecting an `opted_out` route stops being a constraint and becomes a
--      convention, enforced by whichever call site remembers.
--   2. **One database in one country.** Simple, and unlawful for anyone in the EEA.
--
-- `REGIONAL BY ROW` is the third answer: one logical table, one primary key, one unique
-- index, one foreign key graph, and a per-row home that the storage layer honours
-- because the zone configuration on the partition tells it to. The application does not
-- route. It cannot get the routing wrong, because it does not do the routing.
--
-- That is the claim. What follows is the part that makes it true rather than a slide.
--
--
-- ## Where the region comes from, and the hole this migration had to fill first
--
-- `contact_route` had no country. Neither does `party`. Neither does anything else in
-- this schema: `party_metric.territory` is a *sales* territory off a distributor
-- statement, which is where a stream was played and not where a person lives, and
-- bending it into a residency column would be exactly the mistake 018 refused when it
-- would not bend `presence` into a contact route.
--
-- So the first half of this file is a column that should have existed from 018:
-- `contact_country`, ISO-3166-1 alpha-2, the country of establishment of whoever this
-- route reaches. It is a proxy and it is worth naming as one — the GDPR attaches to
-- where the data subject is, and a music director can be on holiday — but country of
-- establishment is the best fact a licence register or a station directory will ever
-- give us, and a proxy that is stated is a different thing from a guess that is hidden.
--
-- **The alpha-2 trap.** `fcc.JURISDICTIONS` is fifty-six two-letter codes and they are
-- *US states*: `AL` is Alabama. ISO-3166-1 alpha-2 `AL` is Albania. Anything that copies
-- a state code into this column has silently moved a station in Tuscaloosa to the
-- Balkans. The `CHECK` below cannot catch that — both are two capital letters — but the
-- region map deliberately can, because Albania is not in any of the three lists and an
-- unmapped country is a hard failure rather than a default. That is the single clearest
-- example of why the map has no `ELSE`, and it is the reason the backfill in statement 4
-- writes `'US'` flat for every FCC-sourced row rather than transcribing the state.
--
--
-- ## The map, jurisdiction by jurisdiction
--
--   aws-eu-west-1 (Ireland)     the EU-27, plus Iceland/Liechtenstein/Norway because
--                               the EEA agreement extends the GDPR to them, plus GB
--                               (UK GDPR) and CH (revised FADP). Ireland is *inside*
--                               the EEA, so storing an EEA subject's row there is not a
--                               Chapter V transfer at all — a stronger position than an
--                               adequacy decision or a set of standard contractual
--                               clauses, both of which can be struck down and one of
--                               which already was.
--
--   aws-ap-southeast-1 (SG)     ASEAN, plus AU/NZ/JP/KR/TW/HK/IN. None of these
--                               requires storage in-country; all of them are badly
--                               served by a US-only footprint, and Singapore is the
--                               region a regulator in any of them would recognise as a
--                               good-faith answer.
--
--   aws-us-east-1 (Virginia)    the US, its territories, Canada and Latin America. This
--                               is also the primary region, which is where every other
--                               table in the database lives.
--
-- **Deliberately absent, and this is not an oversight:**
--
--   * **CN.** China's PIPL requires personal information collected in China to be stored
--     in China, with a security assessment before any export. Singapore does not satisfy
--     that. Mapping CN to ap-southeast-1 would look tidy and would be a violation, so CN
--     is unmapped and a Chinese contact cannot be written until somebody has done the
--     work. **RU** is unmapped for the same reason (Federal Law 152-FZ, Article 18(5)).
--   * **Every country in Africa and the Middle East, and a long tail elsewhere.** Not
--     because they do not matter — because nobody on this project has read their laws.
--     Nigeria's NDPA and South Africa's POPIA both have transfer rules; guessing at them
--     from a continent name is not a residency policy, it is a shrug with a map.
--
-- Adding a jurisdiction is a one-line change to the map and a reviewed migration. That
-- is the correct price. It should cost a human reading a statute, because that is the
-- work the column is standing in for.
--
--
-- ## Why there is no ELSE, argued properly, because this is the whole decision
--
-- The obvious shape for the map is `CASE ... ELSE 'aws-us-east-1' END`. It would make
-- this migration apply on the first run, it would make every existing writer keep
-- working, and it would be wrong in the one direction that matters.
--
-- An `ELSE` says: *any person whose country we failed to record, or recorded in a form
-- we do not recognise, is stored in the United States.* Consider what actually produces
-- an unmapped value. A new source adapter that does not know about this column. A
-- station directory that returns `"DE "` with a trailing space, or `de` in lower case,
-- or the string `"Germany"`. A US state code copied in by someone who saw two capital
-- letters and assumed. Every one of those is *most* likely to be a European contact,
-- because Europe is the only place we harvest where the law is strict — and every one of
-- them would be silently domiciled in Virginia by an `ELSE`, indistinguishable in the
-- table from a contact we correctly determined to be American.
--
-- The failure would be silent, it would be permanent (a row, once written, is where it
-- lives until somebody notices), and it would be undetectable by inspection, because a
-- US-homed row with `contact_country = 'US'` and a US-homed row that fell through an
-- `ELSE` look identical afterwards. That is precisely the class of bug MEMORY's
-- no-fallbacks rule exists to forbid: *a silent default hides the error that caused it.*
--
-- Without an `ELSE`, the expression yields NULL, `residency_region` is NOT NULL, and the
-- write fails with a constraint violation naming the column. The cost is an outage on
-- one insert. The alternative cost is a regulatory finding and a person's address in the
-- wrong country. Those are not close.
--
-- **The alternative that was considered and rejected** was to store the region as an
-- ordinary column written by the application, with a `CHECK` tying it to
-- `contact_country`. It is more maintainable — a `CHECK` can be replaced in place, and a
-- computed column's formula cannot (CockroachDB has no `ALTER COLUMN ... SET EXPRESSION`;
-- widening the map means adding a second computed column, cutting over, and removing the
-- first, which for the partitioning column of a `REGIONAL BY ROW` table means reverting
-- the locality first). It was rejected because it moves the decision into Python, and
-- "the application computes the residency region correctly" is a promise, where "the
-- database computes it from the country and refuses rows it cannot place" is a property.
-- The maintenance cost is a bounded, human-gated migration. The safety cost of the other
-- design is an unbounded application bug.
--
--
-- ## What this breaks, on purpose
--
-- **Every writer of `contact_route` must now supply `contact_country`.** Statement 5
-- makes it NOT NULL with no default. As of this migration the writers are
-- `ingest.send_self_test` (which inserts the delivery self-test route) and whatever the
-- `index_stations` contact-enrichment step grows into; `sender.py` only updates
-- `checked_at` and is unaffected. Those call sites live under `apps/spindle/web/spindle/`
-- and are not changed here — this migration is what makes their omission visible, and it
-- does so as a `NotNullViolation` naming `contact_country`, which is the most useful
-- error the database can produce. Applying this before those call sites pass a country
-- will stop the self-test. That is the intended order of events: the schema states the
-- requirement, the code meets it.
--
-- **Cross-region reads appear on the insert path.** `route_one_per_value` is unique on
-- `(tenant_id, party_id, channel, value)` and does not lead with the region, so
-- CockroachDB enforces it with a distributed uniqueness check — the guarantee holds, and
-- it costs a round trip to the other regions on every insert. Same for the foreign keys
-- to `tenant`, `party` and `party_document`, which stay in the primary region: writing an
-- EU-homed route to a US-homed party validates across the Atlantic. This is real latency
-- and it is the honest price of the constraint, not a defect. The alternative — dropping
-- the uniqueness so it stops costing anything — is the one that would let a harvester
-- resurrect an `opted_out` route, which 018 made structurally impossible and this
-- migration is not entitled to undo.
--
-- **Storage grows by ~1.67x.** `SURVIVE REGION FAILURE` raises the replication factor
-- from three to five, spread 2+2+1 across the three regions. That is what surviving the
-- loss of a region means and it is billed.
--
--
-- ## Reversibility
--
-- The migration is reversible. The provisioning it depends on is not.
--
-- Statements 10–12 revert cleanly: set the locality back to `REGIONAL BY TABLE IN
-- PRIMARY REGION`, remove the computed column, remove `contact_country`. Statement 9
-- reverts with `SURVIVE ZONE FAILURE`. Statements 7–8 revert with `ALTER DATABASE
-- defaultdb DROP REGION` — but only after the survival goal is back to zone, because a
-- region cannot be removed from a database that is relying on three of them.
--
-- The exact reversal script is in `docs/runbooks/multiregion.md` §Reversal and is
-- deliberately **not** quoted here. `apply.py` greps the raw file text — comments
-- included — for a destructive keyword and refuses to run any migration that contains
-- one without an entry in its `DESTRUCTIVE` table. Pasting the reversal into this
-- comment block would make this file unappliable, which is the guard behaving correctly
-- and is worth knowing before somebody helpfully adds it.
--
-- What does not revert is the cluster. CockroachDB Cloud does not support removing a
-- region from a Basic or Standard cluster once it has been added — verified from the
-- Cloud documentation on 2026-08-13, not from the cluster, because verifying it against
-- the cluster would mean doing it. The way back to a single-region cluster is backup,
-- new cluster, restore. This is why the runbook provisions a throwaway cluster rather
-- than converting `respect-the-funk`.
--
--
-- ## Re-runnability, and how to resume a partial apply
--
-- `apply.py` connects with `autocommit=True` and executes statements one at a time.
-- There is no transaction and there is no rollback: a failure at statement N leaves 1
-- through N-1 applied. Every migration in this repo lives with that, and this one is
-- written to be re-run — except for three statements that cannot be.
--
--   Statements 1–5, 10–12 are idempotent. `IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`
--     before `ADD CONSTRAINT`, a backfill filtered on `IS NULL`, and `SET NOT NULL` /
--     `SET LOCALITY` on something already in that state.
--   Statement 6 (`SET PRIMARY REGION`) is expected to be idempotent — setting the
--     primary region to the value it already has is a no-op. UNVERIFIED.
--   **Statements 7 and 8 (`ADD REGION`) are not re-runnable.** CockroachDB does not
--     accept `IF NOT EXISTS` on `ADD REGION`, and this file cannot wrap them in a
--     conditional: PL/pgSQL bodies are dollar-quoted and full of semicolons, and
--     `apply.py`'s statement splitter is a `str.split(';')` that its own docstring
--     admits would shred them. Re-running after these have succeeded fails with
--     "region already added to database" — a loud, accurate, harmless error. §Resume in
--     the runbook says to skip them.
--   Statement 9 (`SURVIVE REGION FAILURE`) is idempotent.
--
--
-- ## What is UNVERIFIED
--
-- Everything below has been written against the CockroachDB documentation and against
-- `terraform providers schema` for cockroachdb/cockroach v1.22.0. **None of it has been
-- executed**, because executing it means provisioning a Standard cluster and this
-- repository's budget is $10 in total. The repo's standard is that a claim is checked
-- against the running cluster; where that was impossible, the claim is marked rather
-- than asserted. Specifically unverified:
--
--   * that `crdb_internal_region` literals in a computed column expression are accepted
--     in the cast form used below;
--   * that `SET LOCALITY REGIONAL BY ROW AS <col>` accepts a `STORED` computed column
--     (the documented migration pattern says yes; it has not been run here);
--   * that `SHOW RANGES FROM TABLE ... WITH DETAILS` returns replica localities on a
--     Standard cluster at all — see the runbook, this is the likeliest thing to fail on
--     the day, and the runbook carries a metadata-only proof that does not depend on it;
--   * the current contents of `contact_route` on `respect-the-funk`. There was no
--     `DATABASE_URL` available in this environment, so the backfill in statement 4 is
--     scoped by source rather than sized by a count. Statement 5 is what finds out.
-- =============================================================================


-- ------------------------------------------------- 1–5: the residency signal
--
-- Nullable first, backfilled, then made NOT NULL. Adding it NOT NULL in one step would
-- need a DEFAULT, and a default country is the same silent lie as a default region.

ALTER TABLE contact_route ADD COLUMN IF NOT EXISTS contact_country STRING;

-- Shape only. This cannot tell Alabama from Albania and does not pretend to; it catches
-- the lower-case, the trailing space and the full country name, which are the three
-- forms a source adapter actually produces.
ALTER TABLE contact_route DROP CONSTRAINT IF EXISTS route_country_shape;

ALTER TABLE contact_route ADD CONSTRAINT route_country_shape
    CHECK (contact_country IS NULL OR contact_country ~ '^[A-Z][A-Z]$');

-- The only backfill this migration is entitled to make.
--
-- An FCC licence is issued under 47 U.S.C. to a licensee in the United States or its
-- territories; there is no such thing as a non-US FCC broadcast licence. So for a route
-- read out of the FM Query register, `US` is a *measured* fact about the source, on the
-- same footing as the frequency — not an inference, and not a default. Every other row
-- is left NULL and statement 5 refuses to proceed until a human has said what it is.
UPDATE contact_route
   SET contact_country = 'US'
 WHERE contact_country IS NULL
   AND (source IN ('fcc', 'fcc_fm_query') OR written_by = 'index_stations');

-- GATE 1. Fails loudly, by design, if any route has no country. `apply.py` prints the
-- statement number and the error; the runbook's §Resume has the query that lists which
-- rows and the one-line UPDATE that settles them once somebody has decided.
ALTER TABLE contact_route ALTER COLUMN contact_country SET NOT NULL;


-- --------------------------------------- 6–9: the database becomes multi-region
--
-- `defaultdb` is named literally because `ALTER DATABASE` takes a name and DDL cannot
-- call `current_database()`. `platform/README` records that this schema lives in
-- `defaultdb`; if that ever stops being true, these four statements are what has to
-- change, and they are the only place in the schema directory that names a database.
--
-- The region names here are the SQL spelling — `aws-us-east-1` — which is not the Cloud
-- API spelling — `us-east-1`. `SHOW REGIONS FROM CLUSTER` is authoritative. The short
-- form fails with "region not found", which reads like the region failed to provision.
--
-- NOT RE-RUNNABLE: statements 7 and 8. See the header.

ALTER DATABASE defaultdb SET PRIMARY REGION "aws-us-east-1";

ALTER DATABASE defaultdb ADD REGION "aws-eu-west-1";

ALTER DATABASE defaultdb ADD REGION "aws-ap-southeast-1";

-- Three regions is the minimum for this goal and the reason the Terraform module asserts
-- exactly three: the replication factor goes from three to five, spread 2+2+1, and two
-- regions cannot hold that. `SHOW SURVIVAL GOAL FROM DATABASE defaultdb` must return
-- `region` afterwards, and that query is the demo's proof that this line took effect.
ALTER DATABASE defaultdb SURVIVE REGION FAILURE;


-- ------------------------------------ 10–12: contact_route becomes REGIONAL BY ROW
--
-- Named `residency_region` rather than `crdb_region`. Two reasons: `crdb_region` is the
-- name CockroachDB gives the *implicit* partitioning column it adds when you do not
-- supply one, and reusing it for a user-defined column invites a collision that would
-- surface as a confusing error at `SET LOCALITY`; and `residency_region` says what the
-- value means — a legal home, not a placement hint.
--
-- `STORED`, not virtual: this becomes the leading column of the primary index and of
-- every secondary index on the table. It has to be materialised.
--
-- No `ELSE`. An unmapped country yields NULL and the NOT NULL on the next statement
-- rejects the row. The header argues this at length; the short version is that the
-- failure mode of a default is a European's address in Virginia, silently.

ALTER TABLE contact_route ADD COLUMN IF NOT EXISTS residency_region crdb_internal_region
    AS (
        CASE
            -- EU-27, plus the three EEA states, plus the UK and Switzerland. Ireland is
            -- inside the EEA, so this is not a Chapter V transfer.
            WHEN contact_country IN (
                'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR',
                'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL',
                'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE',
                'IS', 'LI', 'NO',
                'GB', 'CH'
            ) THEN 'aws-eu-west-1'::crdb_internal_region

            -- ASEAN, plus the East Asian and Oceanian jurisdictions we would plausibly
            -- pitch. CN and RU are absent deliberately — see the header.
            WHEN contact_country IN (
                'SG', 'MY', 'ID', 'TH', 'VN', 'PH', 'BN', 'KH', 'LA', 'MM',
                'AU', 'NZ',
                'JP', 'KR', 'TW', 'HK', 'IN'
            ) THEN 'aws-ap-southeast-1'::crdb_internal_region

            -- The US and its territories — PR, VI, GU, AS and MP hold FCC licences and
            -- are their own ISO codes — plus Canada and Latin America.
            WHEN contact_country IN (
                'US', 'PR', 'VI', 'GU', 'AS', 'MP', 'UM',
                'CA', 'MX',
                'AR', 'BO', 'BR', 'CL', 'CO', 'CR', 'CU', 'DO', 'EC', 'GT',
                'HN', 'JM', 'NI', 'PA', 'PE', 'PY', 'SV', 'TT', 'UY', 'VE'
            ) THEN 'aws-us-east-1'::crdb_internal_region
        END
    ) STORED;

-- GATE 2. Fails loudly if any row's country is not on any of the three lists — which is
-- the intended behaviour for CN, RU, an African jurisdiction nobody has researched, or a
-- US state code that got into the country column. The error names the column and the
-- runbook's §Resume has the query that shows which countries were rejected.
ALTER TABLE contact_route ALTER COLUMN residency_region SET NOT NULL;

-- The line the whole file exists for. From here the storage layer, not the application,
-- decides where a row's replicas live, and the zone configuration on each partition is
-- checkable in SQL — see the runbook's verification queries.
ALTER TABLE contact_route SET LOCALITY REGIONAL BY ROW AS residency_region;
