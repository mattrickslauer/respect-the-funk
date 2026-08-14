-- =============================================================================
-- 030_role_from_source_evidence.sql — the tail 026 could not reach, reached the
--                                     same way and not a different one
-- =============================================================================
--
-- `026_role_backfill.sql` wrote a `role` dimension for every counterparty whose *source*
-- answered what kind of thing it was, and refused to write one for the rest. Its central
-- refusal is correct and is not being reversed here:
--
--     "The tempting backfill is a name match — 'KEWU 89.5 FM' is obviously a radio
--      station, and 122 of these rows read exactly like that. It is also precisely the
--      inference this codebase spent `profiles.py` rule 2 forbidding."
--
-- Nothing below reads a name. What it reads is a *different column carrying the same
-- evidence 026 already accepted*, and the argument of this file is that 026's own test —
-- "does the source say so" — was applied through one table when it holds in three.
--
--
-- ## The measurement, on the live cluster on 2026-08-13
--
-- 026 keys entirely on `party_fact.source`. Neither it nor this file has been applied, so
-- these are the counts as the cluster stands:
--
--     counterparties                                                    14,170
--     reachable by 026 (an fcc_fm_query / radio_browser / wikipedia fact) 12,519
--     left with no role                                                   1,651
--
-- Of those 1,651:
--
--     carry a `radiobrowser_uuid` identifier, provenance `measured`       1,625
--     carry a `party_document` with platform 'radio_browser'              1,556
--     carry a `party_document` with platform 'deezer'                        25
--     carry nothing at all                                                    1
--
-- The 1,625 are not parties whose source was silent. They are Radio Browser stations
-- **whose entry carried no tags**, so `_write_index_streams` had no `genre` fact to write
-- and therefore wrote no `party_fact` row at all — and 026, looking only at `party_fact`,
-- concluded there was no source. The evidence was on the row the whole time, one table
-- over: `party_identifier(kind = 'radiobrowser_uuid', provenance = 'measured')`, which
-- `_write_index_streams` writes for every station it touches.
--
--
-- ## Why an identifier is the same class of evidence as a fact, and a name is not
--
-- 026's `measured` arm rests on this: *"A Radio Browser row is a directory entry whose
-- crawler checks the stream responds. Neither is somebody's opinion: both are
-- observations that the thing transmits."* A `radiobrowser_uuid` **is** that directory
-- entry — it is the entry's own primary key, transcribed, and `_write_index_streams`
-- writes it with `provenance = 'measured'` for exactly this reason. A station has a
-- Radio Browser UUID if and only if Radio Browser has an entry for it. The tag on that
-- entry is a separate claim of a weaker class, and 026 was careful to say so; its absence
-- says nothing about whether the entry exists.
--
-- The same holds for `fcc_facility_id`, included below for completeness rather than for
-- rows: every party with one already has four `fcc_fm_query` facts and is reached by 026.
-- Naming it anyway means this file states a rule about evidence rather than a patch for a
-- population, and a future source that writes an identifier and no facts is covered by
-- the rule instead of needing a 034.
--
-- The distance from a name match is the distance that matters. `Halloween Radio` is a
-- string we read and classified. A `radiobrowser_uuid` is a foreign key into somebody
-- else's register, which either resolves or does not. One is an inference about the
-- world; the other is a citation.
--
--
-- ## The 25 Deezer curators, and why they are `asserted`
--
-- These are the residue of the August playlist harvest — the one that produced the
-- Amandas, whose eighteen worst rows were deleted on 2026-08-10. They hold no identifier
-- at all, only a `party_role` of `curator` and a `party_document` on platform `deezer`
-- carrying their playlist titles.
--
-- The evidence that they curate playlists is real: `DeezerSource.discover_counterparties`
-- reads the `creator` off a playlist resource, which is Deezer's own record of who made
-- it, not a name match. The evidence that they are worth *pitching* is not, and that is
-- the whole reason those eighteen rows were deleted. Those are different claims, and a
-- `role` fact only makes the first one. `asserted` is the class that says a human's
-- record is behind it rather than a measurement of ours, and it keeps these rows visibly
-- weaker than a licence register in any query that reads provenance — which is the
-- outcome that would have been wanted in August.
--
-- Their role is `playlist curator`, not `radio station`. That distinction is the entire
-- point of the dimension: `profiles.compose` used to default `role` to "radio station",
-- and `023_podcast_source.sql` ended the default because the moment a second kind of
-- counterparty existed the default became a way of describing it wrongly. Writing
-- `radio station` here would reintroduce that bug by hand.
--
--
-- ## What this deliberately does not do
--
-- **It does not classify the one party with no evidence of any kind.** That row is
-- `self-test-delivery`, `ingest.send_self_test`'s fixture, whose name says what it is —
-- *"Delivery self-test (not a station)"*. It is not a station, it must not be described
-- as one, and it has no source to ask. It keeps no role, `profiles.from_facts` keeps
-- refusing it by name, and that is the correct output.
--
-- **It does not retire anything.** Retirement was considered for the ~1,100 untagged
-- internet streams that `031`'s refresh cannot make rankable, and rejected: a station
-- with no documented programming is still a real broadcaster, `profiles.py` rule 3
-- already gives it text that sinks rather than floats, and `party.status` is read by
-- queries that mean "does this counterparty exist" rather than "is it worth ranking".
-- Deleting information to improve a coverage percentage is the trade `028` refused when
-- it declined to backfill an index nobody queried.
--
-- **It does not merge the duplicates.** 225 groups of counterparties share both a name
-- and a homepage — 305 redundant rows, 2.2% of the index, mostly one internet station
-- listed once per bitrate. That is real index pollution and it is a `party_relation`
-- question with its own evidence rule, not a `role` one, and folding it in here would put
-- two unrelated judgements behind one apply.
--
--
-- ## Ordering and idempotency
--
-- `WHERE NOT EXISTS (... dimension = 'role')` rather than `ON CONFLICT`, for 026's stated
-- reason: there is no unique index on `(party_id, dimension)` to conflict against, and
-- this file must not depend on the shape of a constraint it does not own. Re-running
-- writes nothing.
--
-- **Order-independent with respect to 026.** Both files guard on the same predicate, so
-- either can be applied first and the result is identical: a party reachable both ways
-- gets one row from whichever ran first, never two. Within this file the measured
-- statement runs before the asserted one and the second excludes what the first wrote,
-- which is 026's ordering discipline repeated because it is still right.
--
-- Applied after 026, this covers 1,650 of the 1,651. Applied without it, it covers those
-- same rows and leaves 026's 12,519 alone; the two together bring `role` coverage to
-- 14,169 of 14,170, which is what `ingest.recompose_profiles` and
-- `streams.refresh_stream` both need before they can rebuild a party's profile at all.

INSERT INTO party_fact (tenant_id, party_id, dimension, value_text, provenance,
                        status, source, written_by)
SELECT p.tenant_id, p.id, 'role', 'radio station', 'measured', 'live',
       'backfill_030', 'migration'
  FROM party p
 WHERE p.party_class = 'counterparty'
   AND EXISTS (SELECT 1 FROM party_identifier i
                WHERE i.tenant_id = p.tenant_id AND i.party_id = p.id
                  AND i.kind IN ('radiobrowser_uuid', 'fcc_facility_id')
                  AND i.provenance = 'measured')
   AND NOT EXISTS (SELECT 1 FROM party_fact r
                    WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id
                      AND r.dimension = 'role');

INSERT INTO party_fact (tenant_id, party_id, dimension, value_text, provenance,
                        status, source, written_by)
SELECT p.tenant_id, p.id, 'role', 'playlist curator', 'asserted', 'live',
       'backfill_030', 'migration'
  FROM party p
 WHERE p.party_class = 'counterparty'
   AND EXISTS (SELECT 1 FROM party_role pr
                WHERE pr.tenant_id = p.tenant_id AND pr.party_id = p.id
                  AND pr.role = 'curator')
   AND EXISTS (SELECT 1 FROM party_document d
                WHERE d.tenant_id = p.tenant_id AND d.party_id = p.id
                  AND d.platform = 'deezer')
   AND NOT EXISTS (SELECT 1 FROM party_fact r
                    WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id
                      AND r.dimension = 'role');
