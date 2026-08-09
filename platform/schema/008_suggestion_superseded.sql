-- 008 — a suggestion can be closed without anyone having judged it.
--
-- `suggestion.state` allowed pending, accepted and rejected. Accepting one candidate has
-- to close its siblings — an artist has one page on a service, so once the Deezer match
-- for Hallow Youth is confirmed the other two Deezer candidates are answered — and none
-- of those three states says what happened to them.
--
-- Writing `rejected` would be a lie of exactly the kind this schema is careful about
-- everywhere else. "A person looked at this and said no" and "this was closed because a
-- sibling was accepted" are different facts, and the first one is evidence about the
-- candidate while the second is only evidence about its sibling. Collapsing them would
-- make a future re-search unable to tell a dismissed match from a merely displaced one.
--
-- `superseded` is not a new idea here — `party_fact.status` already carries it for the
-- same shape of event, a row that is no longer current without being wrong. Reusing the
-- word keeps one vocabulary across the schema rather than inventing a second one.
--
-- Non-destructive: it widens a CHECK. Every existing row still satisfies it.

ALTER TABLE suggestion DROP CONSTRAINT suggestion_state;

ALTER TABLE suggestion ADD CONSTRAINT suggestion_state
    CHECK (state IN ('pending', 'accepted', 'rejected', 'superseded'));
