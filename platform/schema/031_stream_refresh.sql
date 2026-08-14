-- =============================================================================
-- 031_stream_refresh.sql — reading the rest of a payload we already fetched once
-- =============================================================================
--
-- 1,651 counterparties on the live cluster hold the same embedded document, measured on
-- 2026-08-13:
--
--     SELECT body, count(*) FROM party_document
--      WHERE platform = 'profile_v2' GROUP BY 1 ORDER BY 2 DESC LIMIT 1;
--       ->  'Programming not documented. A radio station.'   1651
--
-- 14,170 counterparties, 10,841 distinct profile documents. Those 1,651 rows embed to one
-- vector and therefore sit at the same cosine distance from every query the shortlist will
-- ever run. They are indexed and they are not *rankable* — no query can prefer one over
-- another, because there is nothing in any of them to prefer. `profiles.py` rule 1 argues
-- that text every row shares dilutes the text that does not; this is its limit case,
-- where the shared text is the whole document.
--
--
-- ## What the source already said and nobody stored
--
-- 1,625 of the 1,651 are Radio Browser stations. Its station payload carries
-- `countrycode`, `state`, `language` and `homepage` next to `tags`, and
-- `agents._write_index_streams` stores the tags as a `genre` fact and the homepage as a
-- `presence` and lets the other three go. They reached `radiobrowser.profile_text` as
-- prose and never became `party_fact` rows, so `profiles.from_facts` — which is the
-- rebuild path every recomposition goes through — cannot see them.
--
-- `streams.refresh_stream` re-reads those entries by UUID and writes what was dropped.
-- Sampled against the live API on 2026-08-13, forty of the untagged parties:
--
--     countrycode  40/40      state     13/40
--     homepage     39/40      language   5/40
--     tags          0/40
--
-- The last line is the one to be honest about, and it is why this migration does not
-- claim to fix the tie. **Radio Browser does not know what these stations play and
-- re-reading it does not change that.** Expect `market` on about a third and `language`
-- on about an eighth; expect roughly eleven hundred rows to keep the identical document.
--
-- That residue is not a shortfall being hidden. There is no source this project can
-- lawfully reach that says what an untagged internet stream plays: Wikipedia's infoboxes
-- are keyed on call signs these stations do not have, and the only remaining input is the
-- station's *name*, which is the inference `026_role_backfill.sql` and `profiles.py`
-- rule 2 both refuse — "The Hairband Channel" is a string, not a fact about programming.
-- A stage that reports 1,100 rows it could not describe is worth more than one that
-- describes them wrongly.
--
--
-- ## The reason this is worth applying anyway: it unblocks the contact harvest
--
-- `029_contact_harvest.sql` cannot write a `contact_route` for a party whose country it
-- cannot establish — `024_regional_by_row.sql` is what makes that non-negotiable, and
-- `contacts.country_for` raises rather than defaulting. Two kinds of evidence satisfy it,
-- and one of them is a `country_code` fact, which is what this stage writes for
-- essentially every row it touches.
--
-- Measured on 2026-08-13: 6,314 counterparties have a `web` presence and are candidates
-- for the contact harvest. 872 of them carry an FCC facility ID and can be harvested
-- today. **The remaining 5,442 are blocked on this stage**, and that — rather than the
-- profile text — is the number that justifies the migration.
--
--
-- ## Provenance, and why it is `asserted` rather than `measured`
--
-- `radiobrowser.py` set the rule and this follows it: the module derives nothing, it
-- copies a string somebody typed into a community database, and `asserted` is the honest
-- class for that. 026 calls a Radio Browser entry's *existence* measured — a crawler
-- checked the stream responds — and that is a different claim from its `state` field
-- being accurate. The country in particular is a proxy for where a person is, and `024`'s
-- position on a proxy is that it is stated rather than hidden.
--
-- One consequence worth naming: `contacts.country_for` will therefore domicile a person's
-- contact details on the strength of an `asserted` fact. That is weaker than a licence
-- register and it is the strongest evidence that exists for an internet-only station.
-- `024` reached the same conclusion about country of establishment generally — *"a proxy
-- that is stated is a different thing from a guess that is hidden"* — and the alternative
-- is not a better country, it is 5,442 stations that stay unreachable.
--
--
-- ## Shape of the work
--
-- Bucketed by the first hex character of the station UUID: sixteen leads, deterministic,
-- naturally even because a UUID's first nibble is random, and independent so two workers
-- never contend. That is `enrich_genre`'s call-sign-prefix pattern with the identifier
-- this stage actually has, and it inherits the property that matters: the selection is
-- "has no `country_code` fact yet" rather than a cursor, so a killed run loses nothing and
-- the next one continues.
--
-- `REFRESH_CAP` is 200 stations per run and `UUIDS_PER_CALL` is 50, so a bucket costs four
-- HTTP calls and 200 small writes and is finished by its next run if it holds more. The
-- multi-UUID form of `byuuid` was verified against the live API on 2026-08-13 rather than
-- read off the documentation, because a query parameter the service ignores would return
-- the whole index and look like success.
--
-- `requires_human` is false for `index_stations`' reason: there is no identity guess. The
-- stage is handed a UUID we already hold and asks the register that issued it for the same
-- row back. `writes` names `party_fact` and `party_document` and `lead`, which is what it
-- touches — facts, a recomposed `profile_v2`, and the `embed_party` lead it returns to the
-- frontier when, and only when, the composed text actually changed.
--
-- No `cadence_seconds`, so these leads are terminal. Radio Browser entries do change, and
-- re-reading them is `--requeue refresh_stream`: an explicit hand-crank, on
-- `ingest.requeue`'s argument that a silent re-fetch nobody asked for is not a feature.

INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('refresh_stream', 'lead', 'pending', ARRAY['tenant'], ARRAY['radio_browser'],
        ARRAY['party_fact', 'party_document', 'lead'],
        2, 0, 300, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    scope_kinds = excluded.scope_kinds,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    updated_at = now();
