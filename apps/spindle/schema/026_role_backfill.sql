-- =============================================================================
-- 026_role_backfill.sql — what kind of thing a counterparty is, said once
-- =============================================================================
--
-- `profiles.compose` used to default `role` to "radio station". That was true for as
-- long as radio stations were the only counterparty, and `023_podcast_source.sql` ended
-- it. The default is gone; the parameter is required; `profiles.from_facts` now reads it
-- from a `role` dimension and raises when it is absent rather than guessing.
--
-- Which leaves 14,173 counterparties written before the dimension existed, none of them
-- carrying it. This migration writes it for the ones whose source already answers the
-- question, and deliberately does not write it for the ones whose source does not.
--
--
-- ## Why the source answers it and the name does not
--
-- The tempting backfill is a name match — "KEWU 89.5 FM" is obviously a radio station,
-- and 122 of these rows read exactly like that. It is also precisely the inference this
-- codebase spent `profiles.py` rule 2 forbidding: shortlisting "Hallow Youth" returned
-- "Halloween Radio" because a name is a string, not a fact about what something is.
-- A backfill that reads names would be that same mistake with a different output column,
-- and it would be *more* dangerous here because the result is durable rather than a
-- ranking somebody can eyeball.
--
-- `party_fact.source` is the honest signal, because it records where we learned the row
-- rather than what it happens to be called. Measured against the live cluster on
-- 2026-08-13:
--
--     fcc_fm_query    8,568 parties      radio_browser   4,564 parties
--     wikipedia       6,267 parties      deezer              1 party
--
-- ## The three provenance classes, and why they differ here
--
-- **`measured` — a party with an `fcc_fm_query` or `radio_browser` fact.** An FCC FM
-- Query row is the United States government's licence register stating that this
-- callsign broadcasts. A Radio Browser row is a directory entry whose crawler checks the
-- stream responds. Neither is somebody's opinion: both are observations that the thing
-- transmits. Note this is a *different* judgement from the one `radiobrowser.py` makes
-- about tags — a community-typed genre is `asserted` because a human decided it, but
-- that the entry exists at all, with a stream that answers, is not a matter of opinion.
-- The role is not the tag.
--
-- **`asserted` — a party whose only facts came from `wikipedia`.** 122 rows. Wikipedia
-- has an article describing them as a station, and `enrich_genre` only writes when an
-- infobox names a format, so the article is about a broadcaster. That is a human writing
-- an encyclopaedia entry: better than a guess, weaker than a register, and `asserted` is
-- the class that says so.
--
-- **Nothing at all — a party with no facts of any kind.** 1,654 rows. We know a name and
-- nothing else. There is no evidence here to classify, and inventing one would put a
-- guess in the column the rebuild path trusts. They stay unwritten, `from_facts` refuses
-- them by name, and `ingest.recompose_profiles` counts and reports them rather than
-- aborting the batch or — far worse — describing them as radio stations, which is what
-- the default did silently until today.
--
-- Leaving 12% of the table unclassified is the correct outcome and not a shortfall. A
-- party with no facts has nothing to compose a profile from either; the text it would
-- have received was `UNKNOWN` plus an invented role, which is strictly worse than no
-- text and a name in a report.
--
--
-- ## Idempotency
--
-- `WHERE NOT EXISTS (... dimension = 'role')` rather than `ON CONFLICT`, because there is
-- no unique index on (party_id, dimension) to conflict against — `fact_one_live_per_dimension`
-- is partial and this migration must not depend on the shape of a constraint it does not
-- own. Re-running writes nothing. The two statements are ordered measured-then-asserted
-- and the second excludes anything the first wrote, so a party reachable both ways gets
-- the stronger class and never two rows.

INSERT INTO party_fact (tenant_id, party_id, dimension, value_text, provenance,
                        status, source, written_by)
SELECT p.tenant_id, p.id, 'role', 'radio station', 'measured', 'live',
       'backfill_026', 'migration'
  FROM party p
 WHERE p.party_class = 'counterparty'
   AND EXISTS (SELECT 1 FROM party_fact f
                WHERE f.party_id = p.id
                  AND f.source IN ('fcc_fm_query', 'radio_browser'))
   AND NOT EXISTS (SELECT 1 FROM party_fact r
                    WHERE r.party_id = p.id AND r.dimension = 'role');

INSERT INTO party_fact (tenant_id, party_id, dimension, value_text, provenance,
                        status, source, written_by)
SELECT p.tenant_id, p.id, 'role', 'radio station', 'asserted', 'live',
       'backfill_026', 'migration'
  FROM party p
 WHERE p.party_class = 'counterparty'
   AND EXISTS (SELECT 1 FROM party_fact f
                WHERE f.party_id = p.id AND f.source = 'wikipedia')
   AND NOT EXISTS (SELECT 1 FROM party_fact r
                    WHERE r.party_id = p.id AND r.dimension = 'role');
