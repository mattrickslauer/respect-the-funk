-- =============================================================================
-- 018_contact_route.sql — how you actually reach a counterparty
-- =============================================================================
--
-- The system has been able to say *who* a counterparty is since migration 009, and
-- *where they are* since 005 gave us `presence`. It has never been able to say **how to
-- reach them**, and that is the gap that stops a shortlist from becoming a campaign.
--
-- `presence` is not that column and must not be bent into it. A presence row is a
-- surface on a platform — a Deezer profile, a Spotify page — which answers "where can we
-- read about them" and "whose account is this". A contact route answers a different
-- question with different consequences: send to the wrong presence and you have looked
-- at the wrong page; send to the wrong route and you have mailed a stranger.
--
--
-- ## Why a table and not a column on `party`
--
-- A station has a general enquiries address, a music director's address, a submission
-- form, and a postal address for physical service. Those are four routes to one party,
-- with four different provenances and four different chances of working. A column
-- would force a choice at write time about which one is "the" contact, and that choice
-- is exactly what the outreach stage needs to make later, per campaign, with the
-- channel in hand.
--
--
-- ## The state machine, and why `opted_out` is in it
--
--   unverified — we have it, nobody has confirmed it resolves to a person
--   verified   — a human checked it, or a send was accepted and not bounced
--   bounced    — a send hard-failed. Do not retry this route.
--   invalid    — malformed, or confirmed wrong. Not the same as bounced.
--   opted_out  — **they asked us to stop.**
--
-- `opted_out` is a terminal state that no discovery stage may overwrite, which is why
-- the uniqueness constraint below is on `(tenant_id, party_id, channel, value)` and the
-- loaders use `ON CONFLICT DO NOTHING` rather than `DO UPDATE`. A harvester that
-- re-finds a public address must not be able to resurrect a route somebody asked us to
-- stop using. This is a legal requirement in every jurisdiction we would send into and
-- it is cheaper to make it structurally impossible than to remember it at each call
-- site. `SCOPE-RESET §2a`'s provenance discipline says a fact must carry how it was
-- known; this says a route must carry whether we are still allowed to use it.
--
--
-- ## Provenance, same three classes as everywhere else
--
-- `measured` is a route the source published — the FCC register's own contact field, a
-- station's published submissions address. `inferred` is a guess, above all a pattern
-- guess like `music@<domain>`, and a guessed address is the single fastest way to earn
-- a spam complaint, so it is labelled and the sender is expected to refuse it.
-- `asserted` is a human typing one in.


CREATE TABLE IF NOT EXISTS contact_route (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id      UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,

    -- email | phone | form | postal | social. The channel decides which sender
    -- adapter can use it, and there is no default: an adapter that cannot say what
    -- kind of route it found has not found one.
    channel       STRING NOT NULL,
    value         STRING NOT NULL,

    -- Who the route reaches, when the source says. 'music_director', 'general',
    -- 'submissions', 'program_director'. Free text on purpose — the taxonomy differs
    -- per source and inventing a closed set here would force adapters to lie.
    addressee     STRING NOT NULL DEFAULT '',

    provenance    STRING NOT NULL,
    state         STRING NOT NULL DEFAULT 'unverified',
    confidence    FLOAT,
    source        STRING NOT NULL DEFAULT '',

    -- The evidence the route was read from, so a route can be argued with later on the
    -- same terms as a fact. Same reasoning as `party_document` for embeddings: a claim
    -- whose basis has been discarded cannot be checked.
    document_id   UUID REFERENCES party_document(id) ON DELETE SET NULL,

    written_by    STRING NOT NULL DEFAULT '',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT route_channel_known
        CHECK (channel IN ('email', 'phone', 'form', 'postal', 'social')),
    CONSTRAINT route_provenance_known
        CHECK (provenance IN ('measured', 'inferred', 'asserted')),
    CONSTRAINT route_state_known
        CHECK (state IN ('unverified', 'verified', 'bounced', 'invalid', 'opted_out')),
    CONSTRAINT route_value_present
        CHECK (value != '')
);

-- One row per distinct route to a party. Re-harvesting is the normal case, so the
-- loaders rely on this to make a second discovery a no-op rather than a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS route_one_per_value
    ON contact_route (tenant_id, party_id, channel, value);

-- The outreach question: "who can we actually reach on this channel right now?"
-- `tenant_id` leads, as it does on every index in this schema.
CREATE INDEX IF NOT EXISTS route_reachable
    ON contact_route (tenant_id, channel, state)
    STORING (party_id, addressee, provenance);


-- ---------------------------------------------------------------- the new stage
--
-- `index_stations` is the pipeline stage that turns the FCC's public licence register
-- into counterparties. One lead per jurisdiction, so the work is naturally bounded and
-- a failed state retries without re-fetching the other fifty.
--
-- `requires_human` is false because nothing here is a guess about identity: a licence
-- record is a public fact with a canonical `facility_id`, which is precisely the
-- distinction `suggestion` exists for. The Deezer name-match harvest that produced
-- thirteen junk parties in August had no such identifier and correctly belonged behind
-- a human. This does not.
--
-- `adapters` names the source so `/fleet` can show where the rows came from, and
-- `writes` names the tables so the same view can say what the stage touches.

INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('index_stations', 'lead', 'pending', ARRAY['tenant'], ARRAY['fcc_fm_query'],
        ARRAY['party', 'party_role', 'party_identifier', 'party_fact',
              'party_document', 'contact_route', 'lead'],
        2, 0, 300, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    lease_seconds = excluded.lease_seconds,
    enabled = excluded.enabled,
    updated_at = now();
