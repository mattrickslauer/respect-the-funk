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

# `agents` does not import this module, so this direction is safe and the reverse would
# not be: `justification` replays a shortlist, and the shortlist is agents' to define.
from rtf_platform import agents

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
                counterparty_id: str,
                decided: tuple[Any, int, float] | None = None) -> dict[str, Any]:
    """Open a conversation, and take the counterparty off the shortlist.

    The insert may fail on `one_open_thread_per_counterparty`, and that failure is the
    feature: somebody else is already talking to this person. It propagates as
    `UniqueViolation` for the caller to render, because the two callers want different
    things — an operator wants to be told, and a fleet worker wants to move on.

    Both writes are in one transaction — `db.connect` runs autocommit, so the block is
    what makes that true rather than the fact that the statements are adjacent.
    `contact_state` is a cache of the index, and a cache written in a second transaction
    is a cache that can be wrong.

    `decided` is `(hlc, rank, distance)` from the shortlist that produced this
    counterparty, or `None` when a human picked the name themselves. It is written in the
    same transaction as the thread for the same reason `contact_state` is: a justification
    committed separately from the thing it justifies is a justification that can be
    missing, and the one moment anybody will want it is the moment something went wrong.

    **`None` is a real answer and not a missing one.** `025_decision_provenance.sql`
    argues this at length: defaulting it to the current timestamp would let a
    hand-opened thread present "the ranking at the instant somebody typed a name" as its
    reason, which reads exactly like a real justification. The column stays NULL, and
    the schema's `thread_decision_is_whole` CHECK means a caller cannot write half of one
    — a rank without the instant it was a rank at cannot be replayed, and an instant
    without a rank cannot be checked against the replay.
    """
    hlc, rank, distance = decided if decided is not None else (None, None, None)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO thread (tenant_id, campaign_id, counterparty_id, state,
                                       decided_at_hlc, decided_rank, decided_distance)
                   VALUES (%s, %s, %s, 'discovered', %s, %s, %s)
                   RETURNING id, state, campaign_id, counterparty_id,
                             decided_at_hlc, decided_rank, decided_distance""",
                (tenant_id, campaign_id, counterparty_id, hlc, rank, distance))
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

                # A closed thread is the only moment this system knows how an approach
                # actually went, so it is the moment there is something to learn. What
                # commits here is the *intent* to learn — a lead — and not the lesson
                # itself, because writing the lesson means embedding its text, and an
                # HTTP call inside this transaction would hold the `FOR UPDATE` above
                # across the network. `agents.distil_lesson` does the network half under
                # its own lease and the insert in its own transaction.
                #
                # This is `SCOPE-RESET §2a` rule 2 — processes contribute facts, they do
                # not only consume them — and it is the rule this repository had already
                # broken: `lessons.write` existed, was tested, and had no caller in the
                # entire codebase, so `lesson` was read by every shortlist and written by
                # nothing. That is precisely the write-back failure `MEMORY-SPEC §1`
                # diagnosed and this rule was written to prevent.
                #
                # `ON CONFLICT DO NOTHING` against the target hash: re-closing an already
                # closed thread is refused by the state machine above, but a thread that
                # reaches `closed_lost` from two different callers in a race must not
                # queue two identical lessons.
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, party_id, kind, mode,
                                         adapter, target, target_hash, reason, state,
                                         next_action_at)
                       VALUES (%s, 'party', %s, 'distil_lesson', 'internal', 'internal',
                               %s, %s, %s, 'pending', now())
                    ON CONFLICT DO NOTHING""",
                    (tenant_id, row["counterparty_id"], str(thread_id),
                     idempotency_key(thread_id, 0, f"distil:{to}"),
                     f"thread closed {to}"))
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


class NotJustified(RuntimeError):
    """This thread carries no ranking to replay.

    Distinct from `agents.HistoryExpired`, and the distinction is the whole point of
    having two exceptions: this one means *nobody ever recorded a reason* — a human
    opened the thread, or it predates `025_decision_provenance.sql`. The other means a
    reason was recorded and the cluster no longer retains the memory to reconstruct it.

    "We never had one" and "we had one and it aged out" are different answers to
    somebody asking why they were contacted, and a console that renders them
    identically is lying about one of them.
    """


def justification(conn: psycopg.Connection, tenant_id: str,
                  thread_id: str, *, limit: int = 12) -> dict[str, Any]:
    """Reconstruct the shortlist that opened this thread, as it stood at that instant.

    The whole reason `025_decision_provenance.sql` exists. Given a thread — reachable
    from any message anybody actually sent — this returns the ranking that caused it,
    read from the vector index as it was, not as it is.

    Three outcomes, and keeping them separate is more important than any of them:

      * a ranking, plus `matched` saying whether the replay still puts this counterparty
        where the thread recorded it;
      * `NotJustified`, when nothing was ever recorded;
      * `agents.HistoryExpired`, when the instant has passed the GC window.

    **`matched` is the part worth arguing for.** The replay alone is a strong claim and
    an unfalsifiable one: whatever it returns looks like an answer. Comparing it against
    the rank and distance the thread stored at decision time makes the claim checkable —
    if the embedding model changed underneath, or the replay is reading a different query
    than the one that ran, the numbers diverge and the console can say so instead of
    presenting a confident reconstruction of the wrong thing. A claim that can fail is
    worth more than one that cannot, and this is the cheapest way to let it.

    `distance` is compared with a tolerance rather than for equality because it is a
    FLOAT that has been through a database round trip; the rank is compared exactly,
    because a position in a list either is or is not the one recorded.
    """
    with conn.cursor() as cur:
        cur.execute(
            # The join carries its own `tenant_id` rather than borrowing the thread's.
            # `test_tenant_scoping` refuses the version without it and is right to: a
            # `WHERE` on the left table does not scope the right one, so a campaign id
            # colliding across tenants would join a foreign row. UUIDs make that
            # vanishingly unlikely and "unlikely" is not the guarantee this codebase
            # claims — the whole point of proving it statically is that nobody has to
            # reason about the odds.
            """SELECT t.counterparty_id, t.decided_at_hlc, t.decided_rank,
                      t.decided_distance, c.party_id AS artist_id
                 FROM thread t
                 JOIN campaign c ON c.id = t.campaign_id AND c.tenant_id = t.tenant_id
                WHERE t.tenant_id = %s AND t.id = %s""",
            (tenant_id, thread_id))
        row = cur.fetchone()

    if row is None:
        raise LookupError(f"no thread {thread_id!r} in this tenant")
    if row["decided_at_hlc"] is None:
        raise NotJustified(
            f"thread {thread_id} records no shortlist behind it. Either somebody opened "
            "it by hand, or it predates migration 025 — see that file for why an absent "
            "reason is left absent rather than given a plausible default.")

    # Raises HistoryExpired if the instant has been collected. Deliberately not caught:
    # the caller has to decide what to tell a person, and this module cannot.
    ranking = agents.shortlist_as_of(
        conn, tenant_id, str(row["artist_id"]),
        as_of=str(row["decided_at_hlc"]), limit=limit)

    here = next(((i + 1, r) for i, r in enumerate(ranking)
                 if str(r["id"]) == str(row["counterparty_id"])), None)
    matched = bool(
        here and here[0] == row["decided_rank"]
        and abs(float(here[1]["distance"]) - float(row["decided_distance"])) < 1e-6)

    return {
        "as_of": str(row["decided_at_hlc"]),
        "recorded_rank": row["decided_rank"],
        "recorded_distance": float(row["decided_distance"]),
        "replayed_rank": here[0] if here else None,
        "ranking": ranking,
        "matched": matched,
    }
