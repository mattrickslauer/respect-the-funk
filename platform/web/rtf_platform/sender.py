"""The Sender: the worker that drains `outbox` and actually mails a curator.

`platform/README.md` has said the same thing since migration 010 landed: *"the gate at
`/approvals` is live; nothing claims the outbox, so nothing sends."* This is the thing
that claims it.

It is deliberately **not** a `fleet.Agent`. Every other agent works the `lead` table, and
`agent_manifest` has always recorded that this one works `outbox` instead
(`work_table = 'outbox'`, `claim_state = 'pending'`). That is not an inconsistency to be
tidied away — the two tables have different semantics. A lead is *work that can be
retried*; an outbox row is *an irreversible act that must happen at most once*. Sharing a
claim loop between them would mean sharing a retry policy between them, and the retry
policy is the entire safety argument.

## The ordering that makes double-sending impossible

    1. claim   — UPDATE outbox → 'claimed', COMMITTED, in its own transaction
    2. read    — the message, the thread, and a contact route that is allowed to be used
    3. send    — the network call, with NO transaction open
    4. record  — outbox → 'sent', message.provider_message_id, thread advanced,
                 agent_run written, ALL IN ONE transaction

Step 1 commits before step 3 begins. A worker that dies between them leaves a row in
`claimed`, which **no code in this module will ever pick up again**. `mail.py`'s header
argues why that is the right direction to fail; the short version is that a lost pitch
costs nothing and a duplicate pitch costs the label a curator.

Step 4 is one transaction for the same reason `194b972` made an agent's memory write,
its run record and its lead's completion atomic: the send is not complete until the system
of record says it is, and there must be no state where the provider has the message and
the database does not know.

## What it refuses to do

  * **Send without a configured mailer.** `mail.load()` raises rather than returning a
    no-op, and this module lets that propagate before claiming anything.
  * **Send to a route that is `bounced` or `opted_out`.** Enforced in the SQL that
    selects the address, not in a branch afterwards. `opted_out` is the state migration
    018 made unoverwritable by any discovery stage; this is the other half of that
    promise.
  * **Send to a party with no contact route at all.** The row fails with a reason a
    human can act on, rather than inventing an address from a domain.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg

from rtf_platform import mail, settings as settings_mod, spend

#: Outbox rows claimed per pass. Small on purpose: each one is a network call to a
#: provider and an irreversible act, and a large batch lengthens the window in which a
#: crash strands rows in `claimed`.
BATCH = 5

#: Transient failures before the row is parked as `failed` for a human. Lower than the
#: fleet's four, because every attempt here risks a real delivery.
MAX_ATTEMPTS = 3

#: Seconds to wait after a transient refusal — SES throttling, a network blip. Two
#: entries, not three: `_record_failure` parks the row once `attempts` reaches
#: `MAX_ATTEMPTS`, so with a ceiling of 3 the only backoffs ever reached are the first
#: and second. A third entry would be dead configuration that reads like policy.
BACKOFF = (60, 900)


class NoContactRoute(RuntimeError):
    """The counterparty has no usable email address."""


def claim(conn: psycopg.Connection, tenant_id: str, worker: str, *,
          batch: int = BATCH) -> list[dict[str, Any]]:
    """Take up to `batch` sendable rows, atomically, and commit that before sending.

    `FOR UPDATE SKIP LOCKED` for the same reason the fleet uses it: two senders running
    at once must not queue behind each other, and must never both take the same row.

    Note what is *absent* from the predicate — there is no "or the previous claim
    expired" clause. `fleet.claim` has one, and it is what makes leads self-healing after
    a worker dies. Its absence here is the at-most-once guarantee: a claimed row stays
    claimed until a human looks at it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE outbox
               SET state = 'claimed', claimed_by = %(worker)s, claimed_at = now()
             WHERE tenant_id = %(tenant)s
               AND id IN (
                   SELECT id FROM outbox
                    WHERE tenant_id = %(tenant)s
                      AND state = 'pending'
                      AND not_before <= now()
                    ORDER BY not_before
                    LIMIT %(batch)s
                    FOR UPDATE SKIP LOCKED)
         RETURNING id, tenant_id, thread_id, message_id, kind, attempts
            """,
            {"worker": worker, "tenant": tenant_id, "batch": batch},
        )
        return list(cur.fetchall())


def _prepare(conn: psycopg.Connection, row: dict[str, Any]) -> dict[str, Any]:
    """Everything needed to send, including an address we are permitted to use.

    The route predicate is the compliance boundary and it lives in SQL rather than in
    Python so that no caller can forget it. `state IN ('unverified','verified')` excludes
    `bounced`, `invalid` and `opted_out` by construction — an allowlist, because a
    denylist acquires a hole every time somebody adds a state.

    **An `inferred` route is refused unless a human has verified it.** Migration 018 says
    what an inferred route is — a pattern guess like `music@<domain>` — and that "a
    guessed address is the single fastest way to earn a spam complaint, so it is labelled
    and the sender is expected to refuse it". The first version of this query expressed
    that only in the `ORDER BY`, which *deprioritises* a guess rather than refusing it: a
    counterparty whose only route was guessed still got mailed, and the resulting
    complaint lands against the verified SES identity that every other pitch depends on.
    Ordering is a preference; this needs a predicate.

    Ordered so a verified route beats an unverified one, and a route somebody asserted
    beats one a harvester guessed.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.subject, m.body, m.idempotency_key, m.approved_by,
                      t.counterparty_id, p.name AS counterparty_name
                 FROM message m
                 JOIN thread t ON t.tenant_id = m.tenant_id AND t.id = m.thread_id
                 JOIN party p ON p.tenant_id = t.tenant_id AND p.id = t.counterparty_id
                WHERE m.tenant_id = %s AND m.id = %s""",
            (row["tenant_id"], row["message_id"]),
        )
        message = cur.fetchone()
        if message is None:
            raise NoContactRoute("the message this outbox row delivers is gone")

        cur.execute(
            """SELECT value, state, provenance FROM contact_route
                WHERE tenant_id = %s AND party_id = %s AND channel = 'email'
                  AND state IN ('unverified', 'verified')
                  AND NOT (provenance = 'inferred' AND state != 'verified')
                ORDER BY (state = 'verified') DESC,
                         (provenance = 'asserted') DESC,
                         (provenance = 'measured') DESC
                LIMIT 1""",
            (row["tenant_id"], message["counterparty_id"]),
        )
        route = cur.fetchone()

    if route is None:
        raise NoContactRoute(
            f"{message['counterparty_name']} has no sendable email route — every route "
            "is bounced, invalid or opted out, or none was ever found")

    return {**message, "to": route["value"], "route_state": route["state"]}


def _record_sent(conn: psycopg.Connection, row: dict[str, Any], prepared: dict[str, Any],
                 sent: mail.Sent, worker: str) -> None:
    """Outbox, message, thread and `agent_run`, in one transaction. See module header."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """UPDATE outbox SET state = 'sent', sent_at = now(), last_error = ''
                WHERE tenant_id = %s AND id = %s AND state = 'claimed'""",
            (row["tenant_id"], row["id"]),
        )
        if cur.rowcount != 1:
            # Somebody else moved this row while the provider call was in flight. The
            # message is gone regardless, so this is loud rather than silent.
            raise RuntimeError(
                f"outbox {row['id']} was not in 'claimed' when the send returned — "
                "the provider has the message and this row's state is not ours to set")

        cur.execute(
            """UPDATE message SET provider_message_id = %s, sent_at = now()
                WHERE tenant_id = %s AND id = %s""",
            (sent.provider_message_id, row["tenant_id"], row["message_id"]),
        )
        # `awaiting_reply`, not `sent`: `010`'s state machine has both, and the thread's
        # next meaningful event is a curator answering. `distil_lesson` reads the
        # terminal states this eventually reaches.
        cur.execute(
            """UPDATE thread SET state = 'awaiting_reply', updated_at = now(),
                                 next_action_at = now() + INTERVAL '5 days'
                WHERE tenant_id = %s AND id = %s""",
            (row["tenant_id"], row["thread_id"]),
        )
        # A route that carried a message without SES refusing it is a route that at
        # least resolves. Promotion to `verified` on a bounce notification is SNS's job.
        # Scoped by `party_id` as well as address. A submissions address shared across
        # several stations — `music@` at a group that owns four licences — would
        # otherwise have one send stamp `checked_at` on every other party's route,
        # recording evidence about counterparties this message never went to.
        cur.execute(
            """UPDATE contact_route SET checked_at = now()
                WHERE tenant_id = %s AND party_id = %s AND channel = 'email'
                  AND value = %s""",
            (row["tenant_id"], prepared["counterparty_id"], prepared["to"]),
        )
        cur.execute(
            """INSERT INTO agent_run (tenant_id, agent_kind, state, summary, calls,
                                      cost_micro_usd, source, started_at, ended_at)
               VALUES (%s, 'sender', 'ok', %s, 1, %s, 'ses', now(), now())""",
            (row["tenant_id"],
             f"sent to {prepared['counterparty_name'][:60]} "
             f"({prepared['route_state']} route), approved by "
             f"{prepared['approved_by'] or 'nobody recorded'}",
             int(float(sent.cost_usd) * 1_000_000)),
        )


def _record_failure(conn: psycopg.Connection, row: dict[str, Any], reason: str, *,
                    permanent: bool) -> None:
    """Park the row, or return it to `pending` with a backoff.

    A permanent refusal never goes back to `pending`. Retrying a hard bounce is how a
    sending domain's reputation is destroyed, and the address that caused it is exactly
    the kind of thing a human should look at once rather than a loop should discover
    four times.
    """
    attempts = int(row.get("attempts") or 0) + 1
    give_up = permanent or attempts >= MAX_ATTEMPTS
    with conn.transaction(), conn.cursor() as cur:
        if give_up:
            cur.execute(
                """UPDATE outbox SET state = 'failed', attempts = %s, last_error = %s
                    WHERE tenant_id = %s AND id = %s""",
                (attempts, reason[:500], row["tenant_id"], row["id"]),
            )
        else:
            wait = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
            cur.execute(
                """UPDATE outbox
                      SET state = 'pending', attempts = %s, last_error = %s,
                          claimed_by = NULL, claimed_at = NULL,
                          not_before = now() + (%s || ' seconds')::INTERVAL
                    WHERE tenant_id = %s AND id = %s""",
                (attempts, reason[:500], str(wait), row["tenant_id"], row["id"]),
            )
        cur.execute(
            """INSERT INTO agent_run (tenant_id, agent_kind, state, summary, error,
                                      source, started_at, ended_at)
               VALUES (%s, 'sender', %s, %s, %s, 'ses', now(), now())""",
            (row["tenant_id"], "failed" if give_up else "error",
             "parked for a human" if give_up else f"will retry ({attempts})",
             reason[:500]),
        )


def _record_refused(conn: psycopg.Connection, row: dict[str, Any], reason: str) -> None:
    """Return the row to `pending` **without touching `attempts`**.

    A spend refusal is not a failure of the send. The send never happened: no provider was
    called, nothing was delivered, and nothing about this row is any worse than before it
    was claimed. Routing it through `_record_failure` — which the first version of this
    module did — spends the attempt budget on an event that was not an attempt, and with
    `MAX_ATTEMPTS = 3` that means **three drains with the kill switch in its default
    position permanently park every human-approved pitch as `failed`.** Measured on the
    cluster: pass 1 `pending/1`, pass 2 `pending/2`, pass 3 `failed/3`. `RTF_PAID_ENABLED`
    is unset in every fresh deployment, so that was the default path, and recovering it is
    manual work row by row.

    `fleet` settled this the other way and `test_fleet.py` pins it —
    `test_a_refused_spend_defers_without_burning_an_attempt`. This is that rule, for the
    one table where getting it wrong destroys work a human already approved.

    The `agent_run` row carries `refused`, not `failed`, so `/runs` can show a gate that
    held as distinct from a send that broke.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """UPDATE outbox
                  SET state = 'pending', last_error = %s,
                      claimed_by = NULL, claimed_at = NULL,
                      not_before = now() + INTERVAL '15 minutes'
                WHERE tenant_id = %s AND id = %s""",
            (reason[:500], row["tenant_id"], row["id"]),
        )
        cur.execute(
            """INSERT INTO agent_run (tenant_id, agent_kind, state, summary, error,
                                      source, started_at, ended_at)
               VALUES (%s, 'sender', 'refused', %s, %s, 'ses', now(), now())""",
            (row["tenant_id"], "spend gate held; nothing was sent", reason[:500]),
        )


def send_once(conn: psycopg.Connection, tenant_id: str, worker: str, *,
              batch: int = BATCH, mailer: mail.Mailer | None = None) -> int:
    """Claim, send and record one batch. Returns how many were delivered.

    Raises `mail.MailNotConfigured` **before claiming anything** when there is no mailer.
    Claiming first and discovering afterwards would strand rows in `claimed` that nobody
    will ever retry, which is the one state this module works hardest to avoid.
    """
    config = settings_mod.load()
    # Two guards, deliberately split by what they protect.
    #
    # *This* one protects the **message**: CAN-SPAM requires a physical postal address
    # and a working opt-out in the body, and `compliant_body` composes both from these
    # settings. It is checked here rather than only inside `mail.load()` because an
    # injected mailer skips the loader entirely — and without it a caller supplying its
    # own transport emits a blank address line and an opt-out reading "Reply to  to be
    # removed." Lawfulness is a property of the message, not of who carries it.
    #
    # `mail.load()` below protects the **transport**: a verified SES sender identity.
    # Keeping them separate is what lets a test supply a stub mailer and still prove the
    # compliance path, while a dropped `mailer=` argument in that same test still raises
    # instead of quietly building a real SES client.
    #
    # Both run before `claim`. Raising after a row is claimed would strand it in a state
    # this module never retries, so a misconfiguration would consume the outbox rather
    # than refuse to start.
    if not (config.mail_postal_address and config.mail_reply_to):
        raise mail.MailNotConfigured(
            "PLATFORM_MAIL_POSTAL_ADDRESS and PLATFORM_MAIL_REPLY_TO must be set before "
            "anything is claimed: every message needs a physical address and a working "
            "opt-out, and neither is invented here.")

    mailer = mailer or mail.load()
    gate = spend.Gate.open(conn, tenant_id)

    delivered = 0
    for row in claim(conn, tenant_id, worker, batch=batch):
        try:
            prepared = _prepare(conn, row)
        except NoContactRoute as exc:
            _record_failure(conn, row, str(exc), permanent=True)
            continue

        body = mail.compliant_body(
            prepared["body"], postal_address=config.mail_postal_address,
            unsubscribe=f"Reply to {config.mail_reply_to} to be removed.")

        try:
            gate.check("aws:ses:SendEmail", requests=1)
            sent = mailer.send(to=prepared["to"], subject=prepared["subject"],
                               body=body,
                               idempotency_key=prepared["idempotency_key"])
        except spend.SpendRefused as exc:
            # Not a failure of the send; the send never happened. Back to pending with
            # `attempts` untouched, so raising the ceiling really is all it takes to
            # resume — see `_record_refused` for what the first version got wrong here.
            _record_refused(conn, row, f"spend gate: {exc}")
            continue
        except mail.MailRefused as exc:
            _record_failure(conn, row, str(exc), permanent=exc.permanent)
            continue

        _record_sent(conn, row, prepared, sent, worker)
        # Decrement the day's remaining budget *within* this run. Without it the gate
        # re-reads the ceiling from `agent_run` only when a new Gate is opened, so a
        # single long drain could spend the whole day's allowance and never notice —
        # which for this key means mailing far more strangers than the cap allows.
        gate.record(Decimal(sent.cost_usd))
        delivered += 1

    return delivered
