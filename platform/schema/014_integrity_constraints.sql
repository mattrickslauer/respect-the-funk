-- =============================================================================
-- 014_integrity_constraints.sql — two closed sets that were closed only in Python
-- =============================================================================
--
-- Both halves of this migration follow the same shape: a rule already written down
-- somewhere as prose — a migration comment, a docstring — that the database itself did
-- nothing to enforce. `docs/superpowers/plans/2026-08-09-root-causes.md` Task D names
-- both.
--
--
-- ## (a) A row cannot supersede itself
--
-- `party_fact.supersedes_id` and `lesson.supersedes_id` both point at a row of the same
-- table, and nothing before this migration stopped one from pointing at itself. A
-- self-reference is not a hypothetical: `lessons.heads()` computes
-- `replaced = {row.supersedes_id for row in rows if row.supersedes_id}` and then drops
-- every row whose `id` is in `replaced` — so a lesson that supersedes itself has its own
-- id land in its own `replaced` set and is filtered out of every retrieval that returns
-- it, silently, with no error anywhere. `agents.py`'s `party_fact` analogue
-- (`_write_map_source`, as of this same change) reads the same way: a self-pointing row
-- would be simultaneously the newest reading and the thing that reading superseded.
--
-- A `CHECK (supersedes_id IS NULL OR supersedes_id != id)` is a complete guard against
-- exactly that one-row cycle, and nothing more. It is **not** a guard against a longer
-- cycle — A supersedes B, B supersedes A — because a two-row (or longer) cycle never
-- makes either row's `supersedes_id` equal its own `id`; each comparison the CHECK can
-- see is between two different rows, so it is satisfied by construction on every
-- statement in a longer cycle. Catching that requires either a recursive check at write
-- time or an application-level walk of the chain looking for a repeated id, and this
-- migration does neither. Do not read the presence of this CHECK as "supersession cycles
-- are closed" — only the one-row case is.
--
ALTER TABLE party_fact ADD CONSTRAINT fact_no_self_supersede
    CHECK (supersedes_id IS NULL OR supersedes_id != id);

ALTER TABLE lesson ADD CONSTRAINT lesson_no_self_supersede
    CHECK (supersedes_id IS NULL OR supersedes_id != id);


-- ## (b) `presence.mode` gets the CHECK its sibling columns already have
--
-- `presence_state`, `presence_match` and `presence_subject` (migration 005) each pin
-- their column to a closed set. `mode` never got one — `harvested.Presence.parse`
-- validates it against `domain.ProfileMode` before a suggestion is accepted, but
-- `agents._write_find_counterparties` wrote straight SQL with a literal `'observed'`
-- that never went through that parser and was never one of `ProfileMode`'s three values
-- (`owned`, `unowned`, `absent`) or anything the design spec
-- (`docs/superpowers/specs/2026-08-08-party-first-identity-design.md` §4c) ever
-- described. Illegal data has been landing in a live column with nothing to catch it.
--
-- Queried on the live cluster before writing this constraint, 2026-08-09:
--
--     SELECT mode, count(*) FROM presence GROUP BY mode;
--       observed | 18
--       owned    |  3
--
-- 18 of 21 rows already violate the set this CHECK closes. A standard, validated
-- `ADD CONSTRAINT` would refuse to run against them, and rewriting those 18 rows to make
-- it pass would be exactly the silent-coercion move `NO FALLBACKS` forbids — nobody
-- asked for that, and it is not this migration's call to make unattended on a shared,
-- live cluster. So, matching the precedent migration `009` already set for this same
-- situation (`party_embedding_has_a_model ... NOT VALID`): the constraint is added
-- `NOT VALID`, which enforces it on every write from this point forward and does not
-- touch, check, or block on the 18 rows already there. They stay exactly as they are —
-- readable, unflagged by `VALIDATE CONSTRAINT`, a cleanup for whoever owns that decision
-- — until someone deliberately backfills or retires them.
--
-- The write that produced all 18 (`agents._write_find_counterparties`) is fixed in the
-- same commit as this migration to write `'owned'` instead: the party discovered is the
-- account's own party, in exactly the sense an artist's own Spotify page is `'owned'`,
-- not a fourth mode this system decided it needed.
--
ALTER TABLE presence ADD CONSTRAINT presence_mode_known
    CHECK (mode IN ('owned', 'unowned', 'absent')) NOT VALID;
