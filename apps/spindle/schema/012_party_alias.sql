-- 012 — a merge that can be undone.
--
-- The live index already holds `Amanda`, `Amanda` again, `Amanda Goncalves`,
-- `Amanda Gonçalves`, `Amanda Rocha da Silva` and `Petra Liina Amanda Suokorpi`. Five
-- or six rows that are probably two or three people. Deduplication is not hypothetical
-- here, and it becomes load-bearing the moment discovery adds rows faster than a human
-- reconciles them.
--
--
-- ## Why an alias flag and not a rewrite
--
-- The obvious merge repoints every reference from the duplicate to the survivor and
-- deletes the duplicate. It is irreversible in the way that matters: two curators
-- collapsed into one is a mistake you cannot undo, because you no longer know there
-- were two.
--
-- So nothing is rewritten. The duplicate keeps its row, its presence, its documents and
-- its facts; it gains `alias_of` pointing at the survivor and `party_class = 'alias'`.
-- Accepting a merge is two column writes. Reversing it is the same two backwards.
--
--
-- ## Why the flag costs nothing to read
--
-- `party_class` is already an equality prefix column on the `party_shortlist` vector
-- index, and R1 filters `party_class = 'counterparty'`. So an alias drops out of the
-- shortlist by construction — no new predicate, no index change, and none of the
-- acceleration `009` was written to preserve. This is the third use of the same trick
-- `009` argued for `contact_state`, and it is the reason that argument was worth making
-- once properly.
--
-- The cost lands on reads that want everything known about a person: they must union
-- over the alias chain. That is one join in `repo`, and it is the better trade.
--
--
-- ## The hole this opens in `thread`, closed by 012a
--
-- `thread` already exists on the cluster, with a partial unique index
-- `one_open_thread_per_counterparty` on `(tenant_id, counterparty_id)` enforcing
-- `PLATFORM-SPEC §3c`: one open conversation per person, across every channel.
--
-- An alias and the party it aliases are **two different `counterparty_id` values**. So
-- the moment this migration makes aliases possible, that index stops meaning what it
-- says — both rows can hold an open thread, and the label contacts one person twice
-- through the back door the index was built to lock.
--
-- `012a_thread_canonical.sql` closes it, in the same session as this file, by adding a
-- denormalised `thread.canonical_party_id` and moving the index onto that column. The
-- two migrations are separable only in the sense that they are separate files; shipping
-- this one without that one is a regression in a correctness guarantee that already
-- holds today.


ALTER TABLE party ADD COLUMN IF NOT EXISTS alias_of UUID REFERENCES party(id) ON DELETE SET NULL;

ALTER TABLE party DROP CONSTRAINT IF EXISTS party_class_known;
ALTER TABLE party ADD CONSTRAINT party_class_known
    CHECK (party_class IN ('roster', 'counterparty', 'alias'));

-- The two halves of the flag cannot drift apart: an alias without a target is
-- unresolvable, and a target without the class would still be shortlisted.
ALTER TABLE party ADD CONSTRAINT party_alias_is_classed
    CHECK ((party_class = 'alias') = (alias_of IS NOT NULL));

-- Resolving a canonical party to its aliases — for the union read, and for the console
-- showing what was merged into what.
CREATE INDEX IF NOT EXISTS party_aliases_of
    ON party (tenant_id, alias_of) STORING (name, party_class);
