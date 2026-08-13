-- =============================================================================
-- 021_sender.sql — the Sender stops being declared and starts being real
-- =============================================================================
--
-- `agent_manifest` has carried a `sender` row since migration 010, with
-- `enabled = false`, and `platform/README.md` has said the honest thing next to it ever
-- since: *"no Sender claims the outbox, so nothing is sent."* `rtf_platform/sender.py`
-- now claims it.
--
-- **`requires_human` stays true, and that is not a formality.** The send is the one
-- irreversible act in the product. `/approvals` writes the `message` and the `outbox`
-- row in one transaction carrying `approved_by`, so a row can only exist here because a
-- person put it here. Turning this flag off would not make the fleet faster; it would
-- make it capable of mailing a stranger with nobody's name against the decision.
--
-- `batch_size` 5 and `max_attempts` 3, both lower than any other agent's, for reasons
-- `sender.py` argues at length: a large batch widens the window in which a crash strands
-- rows in `claimed`, and every attempt risks a real delivery.
--
-- Note what this migration does **not** do: it does not set `enabled = true`. Enabling
-- outbound mail is an operator's decision made against a configured SES identity, not a
-- side effect of applying a schema file. `UPDATE agent_manifest SET enabled = true
-- WHERE kind = 'sender'` is the deliberate act, and `010`'s seeding comment already
-- established that `enabled` belongs to the operator rather than to a deploy.

UPDATE agent_manifest
   SET work_table = 'outbox',
       claim_state = 'pending',
       adapters = ARRAY['ses'],
       writes = ARRAY['outbox', 'message', 'thread', 'contact_route', 'agent_run'],
       batch_size = 5,
       max_attempts = 3,
       lease_seconds = 300,
       backoff_seconds = ARRAY[60, 900, 3600],
       requires_human = true,
       updated_at = now()
 WHERE kind = 'sender';
