"""A reply arrives. Whose is it, which conversation is it part of, and what if we cannot tell.

Every mail adapter — Gmail, IMAP, the SES receipt rule — ends here. Their only job is to
turn whatever the provider hands them into an `InboundMessage`; everything that decides
*meaning* lives in this file, so adding a fourth provider is a parser and no new logic.

## The ladder, and why it is a ladder

Correlation is four rungs tried in order, and each is tried only because the one above it
did not answer. They are not interchangeable and the order is the design:

  1. **The reply token.** `spindle+<token>@…` in a recipient header. Authoritative,
     because we minted it for exactly one thread. This is the only rung that also
     *resolves the tenant*, which is what lets one platform mailbox serve everybody.
  2. **The threading headers.** `In-Reply-To`, then `References`, matched against the
     `provider_message_id` `sender.py` stored when the pitch went out. Reliable when the
     curator's client behaves, which is most of the time.
  3. **The sender's address.** Through `contact_route` to a party, and from the party to
     its one open thread. Safe only because `one_open_thread_per_counterparty` — 010's
     partial unique index — guarantees "its one open thread" is not a plural.
  4. **Nothing.** Recorded in `inbound_unmatched`, never discarded.

Rung 4 is the one worth defending. It would be less code to drop a message we cannot
place, and the cost of that is invisible by construction: a curator's reply is the single
most valuable message this system receives, and a pipeline that loses one silently gives
no signal that it is losing them. So an unattributable reply becomes a row a person can
see and file, and `inbound_unmatched.reason` records which rung ran out.

## The tenant is taken from the token, never from the caller

`deliver` accepts a `tenant_id` and treats it as a *hint that the token may overrule*.
That inversion is deliberate and `test_inbound.py` pins it. On the platform path every
tenant's replies land in one mailbox, so the token is the only evidence of ownership that
came with the message; a caller-supplied tenant is at best context and at worst the
mailbox owner's own id. If the two disagree, the token wins, because the token is what we
minted and the caller is what we assumed.

When there is neither a token nor a stated tenant, this raises `Unattributable` rather
than picking one. The SES path can afford that: the receipt rule has already written the
raw message to S3 before any of this runs, so refusing here loses nothing that is not
still on disk.

## Idempotency, because every adapter re-reads

A Gmail `historyId` reset, an IMAP `UIDVALIDITY` bump, a crash between fetching and
committing — all of them replay messages already seen. Both write paths are keyed:
`message` through `outreach.record_reply`'s `idempotency_key` (`UNIQUE (tenant_id,
idempotency_key)`), and `inbound_unmatched` through `UNIQUE (tenant_id,
provider_message_id)`. Delivering the same message twice is a no-op, not a duplicate, and
that is enforced by the database rather than by a check-then-insert that races.
"""

from __future__ import annotations

import secrets
import unicodedata
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.utils import getaddresses
from typing import Any

import psycopg

from spindle import outreach

#: Characters in a minted token. Unambiguous in a monospace font and safe in the local
#: part of an address without quoting — no `+` (it is the separator), no `.` (some
#: providers fold it), nothing needing escaping.
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"

#: Token length. 22 characters over a 32-symbol alphabet is 110 bits, which is not a
#: security boundary — the token is a lookup key, not a capability — but is comfortably
#: past any chance of collision in the partial unique index, and past guessing by
#: somebody who wants to inject a reply into a thread they do not own.
TOKEN_CHARS = 22


class Unattributable(RuntimeError):
    """No token and no tenant. There is nowhere to record this and no way to learn whose
    it is, so it raises. See the module header on why that loses nothing."""


@dataclass(frozen=True)
class InboundMessage:
    """One arrived message, in the only shape this module reads.

    Frozen because every adapter constructs one and hands it over; a mutable carrier
    would let a correlation step quietly rewrite what arrived, and what arrived is
    evidence.
    """

    provider_message_id: str
    from_address: str
    to_addresses: tuple[str, ...] = ()
    subject: str = ""
    body: str = ""
    in_reply_to: str = ""
    references: tuple[str, ...] = ()


@dataclass(frozen=True)
class Delivery:
    """What `deliver` concluded. `thread_id` is `None` when nothing matched."""

    tenant_id: str
    thread_id: str | None
    message_id: str | None
    matched_by: str
    reason: str = ""


# --------------------------------------------------------------------------- parsing

def parse(raw: bytes) -> InboundMessage:
    """RFC 822 bytes to an `InboundMessage`.

    `policy.default` rather than the compat32 default, because it decodes RFC 2047
    encoded-words in headers — a subject line in any non-ASCII language arrives as
    `=?utf-8?B?…?=` otherwise, and would be stored and shown that way.
    """
    parsed = message_from_bytes(raw, policy=policy.default)

    # `getaddresses` rather than splitting on ','. A display name is allowed to contain a
    # comma — `"Curator, A." <curator@example.org>` is one address — and the naive split
    # produces two, one of which is a fragment that matches no contact route.
    from_pairs = getaddresses(_header_values(parsed, "From"))
    from_address = from_pairs[0][1].strip().lower() if from_pairs else ""

    # Every header a token can arrive in. `Delivered-To` is the one that survives a
    # forward, and `X-Original-To` is what several providers use for the same purpose.
    recipients = getaddresses(
        _header_values(parsed, "To", "Cc", "Delivered-To", "X-Original-To"))

    return InboundMessage(
        provider_message_id=_unbracket(_first(parsed, "Message-ID")),
        from_address=from_address,
        to_addresses=tuple(addr.strip() for _, addr in recipients if addr.strip()),
        subject=_first(parsed, "Subject"),
        body=_body_of(parsed),
        in_reply_to=_unbracket(_first(parsed, "In-Reply-To")),
        references=tuple(_unbracket(r) for r in _first(parsed, "References").split()),
    )


def _header_values(parsed: Any, *names: str) -> list[str]:
    return [str(v) for name in names for v in parsed.get_all(name, [])]


def _first(parsed: Any, name: str) -> str:
    got = parsed.get(name)
    return str(got).strip() if got is not None else ""


def _unbracket(value: str) -> str:
    return value.strip().strip("<>").strip()


def _body_of(parsed: Any) -> str:
    """The text a person wrote, preferring `text/plain` over `text/html`.

    A curator's client sends both halves of a `multipart/alternative`. The plain part is
    what they typed; the HTML part is what their client made of it. Storing the markup
    would put tags into the thread view and into whatever later reads the text for
    meaning, so the plain part wins whenever there is one.
    """
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                return _text_of(part)
        for part in parsed.walk():
            if part.get_content_type() == "text/html":
                return _text_of(part)
        return ""
    return _text_of(parsed)


def _text_of(part: Any) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        # An unknown charset, or bytes that are not what the charset claims. Both
        # happen with real mail. Returning the raw payload keeps the message rather
        # than losing it to an encoding argument nobody is present to settle.
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if payload else ""
    # NFC, because the same address or word typed on macOS and on Windows can be two
    # different byte sequences that compare unequal. Normalising once here means every
    # comparison downstream is against a single form.
    return unicodedata.normalize("NFC", str(content)).strip()


# ------------------------------------------------------------------ plus-addressing

def mint_reply_token() -> str:
    """A fresh token. `secrets` rather than `random` — this ends up in a public header."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(TOKEN_CHARS))


def reply_address(base: str, token: str) -> str:
    """`spindle@domain` + token -> `spindle+token@domain`.

    Refuses a base that already carries a tag. `spindle+old+new@` is not two tags to any
    provider — it is one tag reading `old+new` — so the token could never be read back.
    Failing where the address is built puts the error next to the mistake, rather than in
    a poller three days later that quietly matches nothing.
    """
    local, _, domain = base.partition("@")
    if not domain:
        raise ValueError(
            f"reply_address needs a full address to tag, got {base!r}. This is "
            "PLATFORM_MAIL_REPLY_TO — set it to the mailbox replies should arrive in.")
    if "+" in local:
        raise ValueError(
            f"reply_address was given an already-tagged address ({base!r}). A second "
            "tag does not nest: providers read `a+b+c` as the single tag `b+c`, so the "
            "reply token would never be read back and every reply would fall through "
            "to the weaker rungs of the ladder. Configure an untagged address.")
    return f"{local}+{token}@{domain}"


def token_in(addresses: tuple[str, ...] | list[str]) -> str:
    """The first plus-tag found among the recipients, or `''`.

    Scans all of them rather than only the first: a reply-all puts the artist's own
    address ahead of ours, and a forward moves ours into `Delivered-To` entirely.
    """
    for address in addresses:
        local, _, domain = address.partition("@")
        if not domain or "+" not in local:
            continue
        tag = local.partition("+")[2].strip()
        if tag:
            return tag
    return ""


def message_id_candidates(raw: str) -> tuple[str, ...]:
    """Every form a stored `provider_message_id` might have taken.

    SES's `send_email` returns a bare `MessageId` and `sender.py` stores that, but the
    header SES actually puts on the wire is `<{MessageId}@{region}.amazonses.com>` — so
    what a curator's client quotes back in `In-Reply-To` is *not* string-equal to what we
    recorded. Matching only on equality means rung two never fires for the platform
    transport, which is the transport most likely to need it.

    Both forms are returned and the caller matches on either.
    """
    bare = _unbracket(raw)
    if not bare:
        return ()
    local = bare.partition("@")[0]
    return (bare, local) if local != bare else (bare,)


# ------------------------------------------------------------------------- delivery

def deliver(conn: psycopg.Connection, msg: InboundMessage, *,
            tenant_id: str | None = None) -> Delivery:
    """Attach a reply to its thread, or record that it could not be attached.

    `tenant_id` is a hint, not an instruction. See the module header: the token overrules
    it, because on the platform path the token is the only ownership evidence that
    travelled with the message.
    """
    token = token_in(msg.to_addresses)
    if token:
        found = _thread_by_token(conn, token)
        if found:
            return _record(conn, found["tenant_id"], found["id"], msg, "reply_token")

    if tenant_id is None:
        raise Unattributable(
            f"Message {msg.provider_message_id!r} from {msg.from_address!r} carries no "
            "usable reply token and arrived with no tenant. There is no row that could "
            "be written for it and no way to learn whose it is. On the SES path the raw "
            "message is still in the receipt rule's S3 bucket; nothing has been lost, "
            "but nothing can be filed either.")

    for candidate in (msg.in_reply_to, *msg.references):
        thread_id = _thread_by_provider_id(conn, tenant_id, candidate)
        if thread_id:
            return _record(conn, tenant_id, thread_id, msg, "in_reply_to")

    thread_id = _thread_by_sender(conn, tenant_id, msg.from_address)
    if thread_id:
        return _record(conn, tenant_id, thread_id, msg, "from_address")

    reason = (
        f"No reply token, no thread matching In-Reply-To/References "
        f"({msg.in_reply_to or 'absent'}), and {msg.from_address or 'an empty sender'} "
        f"is not a contact route on any open thread for this tenant.")
    _record_unmatched(conn, tenant_id, msg, reason)
    return Delivery(tenant_id=tenant_id, thread_id=None, message_id=None,
                    matched_by="", reason=reason)


def _thread_by_token(conn: psycopg.Connection, token: str) -> dict[str, Any] | None:
    """Rung one. The one statement in this codebase that looks up a tenant-scoped table
    without a tenant predicate, because the token *is* how the tenant is discovered —
    the position `account.token_hash` holds. Registered in `test_tenant_scoping.py`'s
    ALLOWLIST with that justification; see `040_mailbox.sql`'s header."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, tenant_id FROM thread WHERE reply_token = %s", (token,))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": str(row["id"]), "tenant_id": str(row["tenant_id"])}


def _thread_by_provider_id(conn: psycopg.Connection, tenant_id: str,
                           raw: str) -> str | None:
    """Rung two, scoped to the stated tenant — a header is not evidence of ownership the
    way a token we minted is, so it may not cross the boundary."""
    candidates = message_id_candidates(raw)
    if not candidates:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """SELECT thread_id FROM message
                WHERE tenant_id = %s AND direction = 'outbound'
                  AND provider_message_id = ANY(%s)
                ORDER BY sent_at DESC NULLS LAST LIMIT 1""",
            (tenant_id, list(candidates)))
        row = cur.fetchone()
    return str(row["thread_id"]) if row else None


def _thread_by_sender(conn: psycopg.Connection, tenant_id: str,
                      address: str) -> str | None:
    """Rung three. `lower()` on both sides because clients quote addresses back with
    whatever capitalisation they please, and `Curator@Example.ORG` is the same mailbox.

    Restricted to threads that are actually open. A closed thread matching by address
    would reopen a conversation the operator ended, which is a different and worse
    failure than not matching at all.
    """
    if not address.strip():
        return None
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.id AS thread_id
                 FROM thread t
                 JOIN contact_route cr
                   ON cr.tenant_id = t.tenant_id AND cr.party_id = t.counterparty_id
                WHERE t.tenant_id = %s
                  AND cr.channel = 'email'
                  AND lower(cr.value) = lower(%s)
                  AND t.state NOT IN ('closed_won', 'closed_lost', 'closed_no_reply')
                ORDER BY t.updated_at DESC LIMIT 1""",
            (tenant_id, address.strip()))
        row = cur.fetchone()
    return str(row["thread_id"]) if row else None


def _record(conn: psycopg.Connection, tenant_id: str, thread_id: str,
            msg: InboundMessage, matched_by: str) -> Delivery:
    """The write, delegated to the function that has always owned it.

    `outreach.record_reply` already writes the row, advances `awaiting_reply -> replied`
    and handles a decline closing the thread — all inside one transaction, with a
    savepoint so a refused transition cannot lose the reply. None of that is reimplemented
    here. What this module adds is only *which thread*, which is the question
    `record_reply`'s docstring said an inbound adapter would one day answer.
    """
    try:
        row = outreach.record_reply(
            conn, tenant_id, thread_id,
            subject=msg.subject, body=msg.body,
            provider_message_id=msg.provider_message_id)
    except psycopg.errors.UniqueViolation:
        # Already recorded. `record_reply` derives an idempotency key from the provider's
        # id and `UNIQUE (tenant_id, idempotency_key)` refuses the second insert — which
        # is the table doing its job, not an error. It surfaces here rather than inside
        # `record_reply` because the two callers want opposite things: a drafter
        # approving a send wants a double-approve to fail loudly, and a poller re-reading
        # a mailbox wants it to be a no-op. Only this side knows which it is.
        #
        # Resolved by lookup rather than by returning `None`, so a re-delivery still
        # reports *which* message it was — a caller that got a null id could not tell
        # "already had it" from "wrote nothing".
        existing = _existing_inbound(conn, tenant_id, thread_id,
                                     msg.provider_message_id)
        return Delivery(tenant_id=tenant_id, thread_id=thread_id,
                        message_id=existing, matched_by=matched_by,
                        reason="already recorded")
    return Delivery(tenant_id=tenant_id, thread_id=thread_id,
                    message_id=str(row["id"]) if row else None,
                    matched_by=matched_by)


def _existing_inbound(conn: psycopg.Connection, tenant_id: str, thread_id: str,
                      provider_message_id: str) -> str | None:
    """The inbound row a re-delivery collided with."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM message
                WHERE tenant_id = %s AND thread_id = %s AND direction = 'inbound'
                  AND provider_message_id = %s
                ORDER BY received_at DESC LIMIT 1""",
            (tenant_id, thread_id, provider_message_id))
        row = cur.fetchone()
    return str(row["id"]) if row else None


def _record_unmatched(conn: psycopg.Connection, tenant_id: str, msg: InboundMessage,
                      reason: str) -> None:
    """Rung four. `ON CONFLICT DO NOTHING` against `UNIQUE (tenant_id,
    provider_message_id)`, so a re-poll does not accumulate copies of the same
    unplaceable reply."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO inbound_unmatched (tenant_id, provider_message_id,
                                              from_address, to_address, subject, body,
                                              reason)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, provider_message_id) DO NOTHING""",
            (tenant_id, msg.provider_message_id, msg.from_address,
             ", ".join(msg.to_addresses), msg.subject, msg.body, reason))
