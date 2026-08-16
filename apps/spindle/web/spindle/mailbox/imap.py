"""IMAP: the adapter for every provider that is not Google.

Deliberately the plainest thing that works. IMAP is a large protocol and this uses four
verbs of it — LOGIN, SELECT, STATUS, UID SEARCH/FETCH — because the job is small: what
has arrived since last time.

## The cursor is `<uidvalidity>:<uid>`, and both halves are load-bearing

A UID is only meaningful within a UIDVALIDITY generation. The RFC is explicit that a
server may renumber a mailbox and signal it by changing UIDVALIDITY, and when that
happens every UID we hold becomes a number that means nothing — it may now name a
different message, or none.

Storing the UID alone is the bug that follows. After a renumber, "everything after UID
5000" is either the whole mailbox (if the new numbering starts lower) or nothing at all
(if it starts higher), and both are silent. So the generation travels with the position,
and a generation change is treated as *re-establish, deliver nothing this round*.

Delivering nothing is the right answer there rather than re-reading everything, and the
reason is the asymmetry this whole feature is built around: re-reading a renumbered
mailbox would re-deliver mail already filed — which `inbound.deliver` would mostly
discard on its idempotency key, except for exactly the messages whose provider ids we
never recorded, which are the unmatched ones. The failure would be a burst of duplicate
`inbound_unmatched` rows at the worst possible moment.

## A first poll delivers nothing, on purpose

An empty cursor means the mailbox has just been connected, and a mailbox that has been in
use for years contains years of mail. Importing it would file a decade of unrelated
correspondence against threads that did not exist when it was sent — mostly as unmatched
rows, since none of it will correlate. So the first poll establishes a position and
returns nothing, and the tenant's *next* reply is the first thing this system sees.
"""

from __future__ import annotations

from typing import Any

from spindle import inbound
from spindle.mailbox import MailboxRefused

#: The mailbox read. Not configurable: a curator replies to a pitch, and a reply lands in
#: INBOX. A tenant who has a filter moving our mail elsewhere has a problem this setting
#: would hide rather than fix.
FOLDER = "INBOX"

#: Messages fetched per poll. A ceiling rather than a target — it exists so that a
#: mailbox with a large backlog does not produce one enormous transaction and a Lambda
#: timeout. The cursor advances to whatever was actually read, so the remainder arrives
#: on the next pass rather than being skipped.
BATCH = 50


def fetch(account: Any, password: str, *,
          client: Any = None) -> tuple[list[inbound.InboundMessage], str]:
    """Everything since `account.sync_cursor`, and the new cursor.

    `client` is injected so a poller can reuse a connection and the suite can drive this
    without a server. When it is `None` a real TLS connection is opened and — importantly
    — closed in the `finally` below, because a poller that leaks sockets dies partway
    through its first few hundred tenants.
    """
    owned = client is None
    if owned:
        client = _connect(account)

    try:
        try:
            client.login(account.address, password)
        except Exception as exc:
            # Authentication is the one failure that must not be retried. See
            # `mailbox/__init__.py`'s header: a provider asked repeatedly for a password
            # it has already refused will lock the account at its end, and the tenant
            # then cannot fix it from their side either.
            raise MailboxRefused(
                f"{account.imap_host} refused the credential for {account.address}. "
                f"Reconnect the mailbox with a current password — for a provider using "
                f"app passwords, this usually means the app password was revoked. "
                f"({type(exc).__name__})") from None

        client.select(FOLDER, readonly=True)
        generation = _uidvalidity(client)
        last_generation, last_uid = _split(account.sync_cursor)

        if not account.sync_cursor or generation != last_generation:
            # No position, or the server renumbered. Establish where we are and stop.
            return [], f"{generation}:{_highest(client)}"

        uids = _search(client, f"UID {last_uid + 1}:*")
        # The `<n>:*` form is inclusive of the last message even when no UID is that
        # high, so a server with nothing new can answer with the last UID we already
        # hold. Filtering rather than trusting the search keeps that from re-delivering.
        uids = [uid for uid in uids if uid > last_uid][:BATCH]
        if not uids:
            return [], account.sync_cursor

        found = []
        for uid in uids:
            raw = _fetch_one(client, uid)
            if raw:
                found.append(inbound.parse(raw))
        return found, f"{generation}:{max(uids)}"
    finally:
        if owned or hasattr(client, "logout"):
            try:
                client.logout()
            except Exception:                              # pragma: no cover
                pass


def _connect(account: Any):
    import imaplib                                   # noqa: PLC0415 — stdlib, lazy
    return imaplib.IMAP4_SSL(account.imap_host, account.imap_port)


def _uidvalidity(client: Any) -> str:
    """`STATUS INBOX (UIDVALIDITY)` -> the bare number."""
    _, data = client.status(FOLDER, "(UIDVALIDITY)")
    text = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    marker = "UIDVALIDITY"
    if marker not in text:
        raise MailboxRefused(
            f"the server did not report UIDVALIDITY for {FOLDER} ({text!r}). Without it "
            "there is no safe way to know whether stored UIDs still mean anything, and "
            "guessing would risk re-delivering or skipping mail.")
    tail = text.split(marker, 1)[1]
    return "".join(ch for ch in tail if ch.isdigit())


def _search(client: Any, criterion: str) -> list[int]:
    _, data = client.uid("SEARCH", None, criterion)
    if not data or not data[0]:
        return []
    raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    return sorted(int(part) for part in raw.split() if part.isdigit())


def _highest(client: Any) -> int:
    uids = _search(client, "ALL")
    return max(uids) if uids else 0


def _fetch_one(client: Any, uid: int) -> bytes | None:
    _, data = client.uid("FETCH", str(uid), "(RFC822)")
    for part in data or []:
        if isinstance(part, tuple) and len(part) > 1:
            return part[1]
    return None


def _split(cursor: str) -> tuple[str, int]:
    """`'111:5'` -> `('111', 5)`. A malformed cursor reads as no cursor.

    Tolerant on purpose, and it is the one place in this feature that is. The alternative
    to treating an unparseable cursor as absent is raising, which would stop a mailbox
    permanently over a value only this module ever writes — and the recovery from
    "absent" is safe by construction: re-establish position, deliver nothing.
    """
    generation, _, uid = cursor.partition(":")
    return generation, int(uid) if uid.isdigit() else 0
