"""Campaigns, threads, drafts and the send gate — the write side of outreach.

`research.py` reads these tables; this module is the only thing that writes them. The
split matters more here than anywhere else in the product, because this is where the one
irreversible act lives: everything else in the console can be undone by editing a row,
and a sent email cannot.

Three rules are enforced here rather than trusted to callers.

**A state change is a transition, not an assignment.** `ALLOWED` is
`PLATFORM-SPEC §2d`'s state machine written as a table, and `advance` refuses anything
absent from it. The database's `thread_state_known` CHECK catches a *misspelled* state;
this catches a *legal state reached out of order*, which is the one that produces a
thread that looks fine and is wrong — a draft that skipped the human, most of all.

**Opening and closing a thread also writes `party.contact_state`.** The partial unique
index is the real lock and the denormalised column is the shortlist's accelerated view
of it, so they are written in one transaction or neither. Migration 009 argued the
column into existence; letting the two drift would take the argument back.

**A send is prepared in one transaction and performed by nobody.** `approve` writes the
`message` row and the `outbox` row together, sharing an idempotency key, exactly as
`§3b` requires. It does not send. No provider is wired, no sender claims `outbox`, and a
row sitting there in `pending` is a send that is fully prepared and has not happened —
which is the honest state of this system and is visible as such in the console rather
than hidden behind a button that appears to work.
"""

from __future__ import annotations

import hashlib
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

#: `PLATFORM-SPEC §2d`'s state machine, as `state -> the states it may become`.
#:
#: Read the shape rather than the entries: every state can reach a closed one, because
#: an operator abandoning a conversation is always legal and a machine that made you
#: walk a thread forward to drop it would be walked around. Only the forward edges are
#: narrow.
ALLOWED: dict[str, frozenset[str]] = {
    "discovered":     frozenset({"shortlisted", "closed_lost"}),
    "shortlisted":    frozenset({"approved", "closed_lost"}),
    # An operator approving the *approach*. The draft does not exist yet.
    "approved":       frozenset({"drafted", "closed_lost"}),
    "drafted":        frozenset({"awaiting_human", "closed_lost"}),
    # The gate. `drafted` is the way back — a rejected draft is redrafted, not discarded,
    # so rejecting costs the thread nothing.
    "awaiting_human": frozenset({"queued", "drafted", "closed_lost"}),
    # `queued` means an outbox row exists. Reaching `sent` is a sender's job and nothing
    # in this build does it.
    "queued":         frozenset({"sent", "closed_lost"}),
    "sent":           frozenset({"awaiting_reply", "closed_lost"}),
    "awaiting_reply": frozenset({"replied", "closed_no_reply", "closed_lost"}),
    "replied":        frozenset({"negotiating", "agreed", "closed_lost"}),
    "negotiating":    frozenset({"agreed", "closed_lost"}),
    "agreed":         frozenset({"delivered", "closed_lost"}),
    "delivered":      frozenset({"verified", "closed_lost"}),
    "verified":       frozenset({"closed_won"}),
    "closed_won":     frozenset(),
    "closed_lost":    frozenset(),
    "closed_no_reply": frozenset(),
}

#: Out of the index in `one_open_thread_per_counterparty`, and therefore no longer
#: holding the counterparty. Kept as one definition because the SQL predicate, the
#: release logic and the view's "open" count all mean the same thing by it.
CLOSED: frozenset[str] = frozenset({"closed_won", "closed_lost", "closed_no_reply"})

#: Where the operator can act. The approvals screen is this set, and the nav badge counts
#: it — a queue nobody is told about is a queue nobody empties.
NEEDS_HUMAN: str = "awaiting_human"


class TransitionRefused(RuntimeError):
    """A state change the machine does not allow.

    Carries both ends so the message is actionable — "drafted → sent" names the step
    that was skipped, where "invalid transition" makes somebody go and read this file.
    """

    def __init__(self, thread_id: Any, was: str, to: str) -> None:
        legal = ", ".join(sorted(ALLOWED.get(was, frozenset()))) or "nothing — it is closed"
        super().__init__(
            f"thread {thread_id} is {was} and cannot become {to}. From {was} it may become: {legal}.")
        self.was, self.to = was, to


def idempotency_key(thread_id: Any, ordinal: int, body: str) -> str:
    """The key the message row and the outbox row share, per `§3b`.

    Derived rather than random, so a retry of the same approval produces the same key
    and the `UNIQUE (tenant_id, idempotency_key)` refuses the duplicate instead of
    queueing a second copy. The body is in the digest because redrafting and re-approving
    is a genuinely different send and must not collide with the one it replaced.
    """
    digest = hashlib.sha256(f"{thread_id}:{ordinal}:{body}".encode()).hexdigest()
    return f"th-{str(thread_id)[:8]}-{ordinal}-{digest[:16]}"


# ------------------------------------------------------------------- campaigns

def create_campaign(conn: psycopg.Connection, tenant_id: str, *, party_id: str,
                    name: str, channel: str = "curator", goal: str = "",
                    recording_id: str | None = None) -> dict[str, Any]:
    """A campaign starts in `draft` and holds no threads.

    It does not start running on creation. Running means the fleet may open threads
    against it, and that is a second, deliberate act — the same reasoning as the send
    gate, one step earlier and much cheaper to get wrong.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO campaign (tenant_id, party_id, recording_id, name, channel, goal)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, name, state, channel""",
            (tenant_id, party_id, recording_id, name.strip(), channel, goal.strip()))
        return cur.fetchone()


def set_campaign_state(conn: psycopg.Connection, tenant_id: str, campaign_id: str,
                       state: str) -> None:
    """`started_at` is stamped the first time it runs and never restamped, so pausing
    and resuming does not rewrite when the campaign began."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE campaign
                  SET state = %s,
                      started_at = CASE WHEN %s = 'running' AND started_at IS NULL
                                        THEN now() ELSE started_at END,
                      ended_at   = CASE WHEN %s = 'done' THEN now() ELSE NULL END,
                      updated_at = now()
                WHERE tenant_id = %s AND id = %s""",
            (state, state, state, tenant_id, campaign_id))


# --------------------------------------------------------------------- threads

def open_thread(conn: psycopg.Connection, tenant_id: str, *, campaign_id: str,
                counterparty_id: str) -> dict[str, Any]:
    """Open a conversation, and take the counterparty off the shortlist.

    The insert may fail on `one_open_thread_per_counterparty`, and that failure is the
    feature: somebody else is already talking to this person. It propagates as
    `UniqueViolation` for the caller to render, because the two callers want different
    things — an operator wants to be told, and a fleet worker wants to move on.

    Both writes are in one transaction — `db.connect` runs autocommit, so the block is
    what makes that true rather than the fact that the statements are adjacent.
    `contact_state` is a cache of the index, and a cache written in a second transaction
    is a cache that can be wrong.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO thread (tenant_id, campaign_id, counterparty_id, state)
                   VALUES (%s, %s, %s, 'discovered')
                   RETURNING id, state, campaign_id, counterparty_id""",
                (tenant_id, campaign_id, counterparty_id))
            row = cur.fetchone()
            cur.execute(
                "UPDATE party SET contact_state = 'in_thread' WHERE tenant_id = %s AND id = %s",
                (tenant_id, counterparty_id))
    return row


def advance(conn: psycopg.Connection, tenant_id: str, thread_id: str, to: str, *,
            reason: str = "") -> str:
    """Move a thread, or refuse.

    The current state is read `FOR UPDATE` so two workers cannot both read
    `awaiting_human` and both queue a send. That lock only means anything inside a
    transaction, which is what the block below is for — under autocommit the row would
    be released before the check that depends on it had been acted on, and the state
    machine would be advisory. The race it loses is the expensive one.

    Nested inside a caller's transaction this opens a savepoint, so `approve` and
    `draft` compose with it without either having to know.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state, counterparty_id FROM thread WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, thread_id))
            row = cur.fetchone()
            if row is None:
                raise TransitionRefused(thread_id, "missing", to)
            was = row["state"]
            if to not in ALLOWED.get(was, frozenset()):
                raise TransitionRefused(thread_id, was, to)

            cur.execute(
                """UPDATE thread
                      SET state = %s, reason = %s, updated_at = now(),
                          closed_at = CASE WHEN %s THEN now() ELSE closed_at END,
                          owner_agent = NULL, lease_expires_at = NULL
                    WHERE tenant_id = %s AND id = %s""",
                (to, reason, to in CLOSED, tenant_id, thread_id))

            # Closing releases the counterparty in both places at once: the partial index
            # drops the row, and the shortlist's equality column follows it. `declined` is
            # kept distinct from `contactable` because a no is worth remembering.
            if to in CLOSED:
                cur.execute(
                    "UPDATE party SET contact_state = %s WHERE tenant_id = %s AND id = %s",
                    ("declined" if to == "closed_lost" else "contactable",
                     tenant_id, row["counterparty_id"]))
    return was


# -------------------------------------------------------------------- drafting

def draft(conn: psycopg.Connection, tenant_id: str, thread_id: str, *,
          subject: str, body: str, channel: str = "email") -> dict[str, Any]:
    """Write an outbound message and stop at the gate.

    The message row is written *unsent*: no `sent_at`, no `idempotency_key` that means
    anything yet, no outbox row. It exists so the operator has something to read, and
    reading it is the entire point of the approvals screen — you cannot judge a pitch
    from a row.

    The thread lands on `awaiting_human` whichever state it came from, walking
    `approved → drafted → awaiting_human` so no edge is skipped.

    The message and the two moves are one transaction: a draft that exists on a thread
    still reading `approved` is invisible to the approvals screen, which selects on
    state — so a partial commit here loses the draft rather than showing it late.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state FROM thread WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, thread_id))
            row = cur.fetchone()
            if row is None:
                raise TransitionRefused(thread_id, "missing", "drafted")
            state = row["state"]

            cur.execute(
                "SELECT count(*) AS n FROM message WHERE tenant_id = %s AND thread_id = %s "
                "AND direction = 'outbound'", (tenant_id, thread_id))
            ordinal = cur.fetchone()["n"]

            cur.execute(
                """INSERT INTO message (tenant_id, thread_id, direction, channel,
                                        subject, body, idempotency_key)
                   VALUES (%s, %s, 'outbound', %s, %s, %s, %s)
                   RETURNING id, subject, body, created_at""",
                (tenant_id, thread_id, channel, subject.strip(), body.strip(),
                 idempotency_key(thread_id, ordinal, body)))
            message = cur.fetchone()

        if state == "approved":
            advance(conn, tenant_id, thread_id, "drafted", reason="draft written")
            state = "drafted"
        if state == "drafted":
            advance(conn, tenant_id, thread_id, "awaiting_human", reason="waiting for a human")
    return message


def approve(conn: psycopg.Connection, tenant_id: str, thread_id: str, message_id: str,
            *, approver: str) -> dict[str, Any]:
    """The send gate. Prepares the send; does not perform it.

    `§3b` in one function: the message is stamped approved and the `outbox` row is
    written **in the same transaction**, sharing the key the message already carries.
    Without the transaction, a crash between "queued" and "recorded as queued" either
    double-sends or silently drops — and the first of those burns a relationship you
    burn exactly once.

    `UNIQUE (message_id)` on `outbox` makes a double approval a failed insert rather
    than a second copy in flight, so the gate is safe to double-click.

    The thread move is inside the same block as the two writes. It is the third thing
    that must not happen alone: a `queued` thread with no outbox row is a send that will
    never happen and that nothing is waiting on, and it reads on screen exactly like one
    that will.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.id, m.idempotency_key, m.subject, m.body, m.channel, m.thread_id
                     FROM message m
                    WHERE m.tenant_id = %s AND m.id = %s AND m.thread_id = %s
                      AND m.direction = 'outbound' AND m.sent_at IS NULL
                    FOR UPDATE""",
                (tenant_id, message_id, thread_id))
            message = cur.fetchone()
            if message is None:
                raise TransitionRefused(thread_id, "no unsent draft", "queued")

            cur.execute(
                """UPDATE message SET approved_by = %s, approved_at = now()
                    WHERE tenant_id = %s AND id = %s""",
                (approver, tenant_id, message_id))
            cur.execute(
                """INSERT INTO outbox (tenant_id, thread_id, message_id, kind, payload_json)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, state, created_at""",
                (tenant_id, thread_id, message_id, message["channel"],
                 Jsonb({
                     "subject": message["subject"],
                     "body": message["body"],
                     "idempotency_key": message["idempotency_key"],
                 })))
            row = cur.fetchone()

        advance(conn, tenant_id, thread_id, "queued", reason=f"approved by {approver}")
    return row


def reject(conn: psycopg.Connection, tenant_id: str, thread_id: str, message_id: str,
           *, reason: str = "") -> None:
    """Send the draft back rather than deleting it.

    The message row stays. What the drafter wrote and what an operator refused is the
    only training signal this part of the product produces, and deleting it to keep the
    screen tidy would throw it away.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE message SET approved_by = '', approved_at = NULL "
                "WHERE tenant_id = %s AND id = %s AND thread_id = %s",
                (tenant_id, message_id, thread_id))
        advance(conn, tenant_id, thread_id, "drafted", reason=reason or "rejected by operator")


# ---------------------------------------------------------------------- inbox

def record_reply(conn: psycopg.Connection, tenant_id: str, thread_id: str, *,
                 subject: str, body: str, intent: str = "", confidence: float = 0.0,
                 provider_message_id: str = "", channel: str = "email") -> dict[str, Any]:
    """An inbound message, and the thread move it implies.

    Nothing calls this from a mail provider yet — there is no inbound adapter. It exists
    because the inbox view reads `message` and a view over a table only one absent
    integration can fill would otherwise have to be a fixture again.

    The intent decides the move, and an unrecognised or absent one leaves the thread
    where it is. Guessing here would be the drift failure: a classifier that is unsure
    should hand the thread to a person, and `replied` is the state that does that.
    """
    key = idempotency_key(thread_id, 0, provider_message_id or body)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO message (tenant_id, thread_id, direction, channel, subject,
                                        body, intent, confidence, provider_message_id,
                                        idempotency_key, received_at)
                   VALUES (%s, %s, 'inbound', %s, %s, %s, %s, %s, %s, %s, now())
                   RETURNING id, subject, intent, received_at""",
                (tenant_id, thread_id, channel, subject.strip(), body.strip(), intent,
                 confidence, provider_message_id, f"in-{key}"))
            row = cur.fetchone()
            cur.execute("SELECT state FROM thread WHERE tenant_id = %s AND id = %s",
                        (tenant_id, thread_id))
            state = (cur.fetchone() or {}).get("state", "")

        if state == "awaiting_reply":
            advance(conn, tenant_id, thread_id, "replied",
                    reason=f"reply received ({intent or 'unclassified'})")
        if intent == "declined":
            # Only from a state that can reach it. A decline arriving mid-negotiation
            # closes the thread; one arriving after it already closed changes nothing.
            # The savepoint `advance` opens is what makes this `except` safe — without
            # it a refused transition would poison the enclosing transaction and lose
            # the reply we just recorded.
            try:
                advance(conn, tenant_id, thread_id, "closed_lost", reason="they declined")
            except TransitionRefused:
                pass
    return row


# ------------------------------------------------------------------ statistics

def counts(conn: psycopg.Connection, tenant_id: str) -> dict[str, int]:
    """What the nav badges and the view headers need, in one round trip.

    One query rather than five, because these render on every console page and five
    counts against a cluster that scales to zero is five cold reads.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM thread
                   WHERE tenant_id = %(t)s AND state = 'awaiting_human') AS awaiting_human,
                 (SELECT count(*) FROM thread
                   WHERE tenant_id = %(t)s AND state NOT IN ('closed_won','closed_lost','closed_no_reply')) AS open_threads,
                 (SELECT count(*) FROM message
                   WHERE tenant_id = %(t)s AND direction = 'inbound') AS inbound,
                 (SELECT count(*) FROM outbox
                   WHERE tenant_id = %(t)s AND state = 'pending') AS queued,
                 (SELECT count(*) FROM campaign
                   WHERE tenant_id = %(t)s AND state = 'running') AS running""",
            {"t": tenant_id})
        return dict(cur.fetchone() or {})
