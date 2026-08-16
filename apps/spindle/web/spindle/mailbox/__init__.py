"""A tenant's connected mailbox: the row, its lifecycle, and the poll that reads it.

The adapters live beside this module — `mailbox.gmail` and `mailbox.imap` — and both
have the same tiny contract: given an account and a credential, return the messages that
have arrived since the cursor, and the new cursor. Everything else is here, because
everything else is the same for both.

## The poller enumerates tenants rather than sweeping the table

The obvious query is `SELECT … FROM mail_account WHERE state = 'connected'` across every
tenant at once, and it is not written that way on purpose. `mail_account` is a
tenant-scoped table and `test_tenant_scoping.py` lints every statement against one; a
cross-tenant sweep would have to be excused in that file's ALLOWLIST, and the standard
that file sets for an entry is narrow — a statement that *discovers* a tenant, cut down
to the minimum. A poller's convenience is not that.

So `due()` enumerates `tenant` (which carries no `tenant_id` and is legitimately outside
the lint) and asks per tenant. That is one query per tenant per poll, which is the same
shape `sender.send_once(conn, tenant_id, worker)` already has, and at the scale this
runs — every few minutes, over the number of labels a deployment has — it is not the
cost worth trading a security lint for.

## Failure is a state, not a retry

`mark_failed` exists because retrying a rejected credential is actively harmful. A
provider that has refused a password will lock the account at its end if asked enough
times, and the tenant then cannot fix it from their side either. So a rejected credential
stops the poller for that tenant, records why in words they can act on, and waits for a
reconnect — which is the one event that can plausibly have changed the answer.

That is also why `connect` clears `state` and `last_error`. Without it a tenant fixes
their app password, reconnects, and is skipped forever by a poller still reading a stale
failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from spindle import inbound, vault as vault_mod

#: Columns every read of this table wants. One string so the three readers cannot drift
#: into disagreeing about what an `Account` is.
_COLUMNS = ("tenant_id, provider, address, secret_arn, imap_host, imap_port, "
            "smtp_host, smtp_port, sync_cursor, state, last_error")


class MailboxRefused(RuntimeError):
    """The provider rejected the credential. Terminal until the tenant reconnects."""


@dataclass(frozen=True)
class Account:
    """One tenant's connected mailbox, as the adapters see it.

    Note what is absent: the credential. An `Account` is safe to log, safe to put in an
    error message and safe to hold in a list across a whole poll — `secret_arn` is a
    pointer, and resolving it is a deliberate separate step through `vault`.
    """

    tenant_id: str
    provider: str
    address: str
    secret_arn: str
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    sync_cursor: str = ""
    state: str = "connected"
    last_error: str = ""


def _account_from(row: dict[str, Any]) -> Account:
    return Account(
        tenant_id=str(row["tenant_id"]), provider=row["provider"],
        address=row["address"], secret_arn=row["secret_arn"],
        imap_host=row["imap_host"], imap_port=int(row["imap_port"]),
        smtp_host=row["smtp_host"], smtp_port=int(row["smtp_port"]),
        sync_cursor=row["sync_cursor"], state=row["state"],
        last_error=row["last_error"])


# ------------------------------------------------------------------- the row

def load_account(conn: psycopg.Connection, tenant_id: str) -> Account | None:
    """This tenant's mailbox, or `None` if they have not connected one.

    `None` is not a default and this function does not invent one. What absence *means*
    is the caller's decision and the two callers decide differently: `mail.identity_for`
    reads it as "send through the platform identity", and the poller reads it as "nothing
    to poll". Returning a synthetic platform `Account` here would have made the first
    convenient and the second wrong.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM mail_account WHERE tenant_id = %s",
                    (tenant_id,))
        row = cur.fetchone()
    return _account_from(row) if row else None


def connect(conn: psycopg.Connection, tenant_id: str, *, provider: str, address: str,
            secret_arn: str, imap_host: str = "", imap_port: int = 993,
            smtp_host: str = "", smtp_port: int = 587) -> Account:
    """Attach a mailbox, replacing whatever was there.

    `UPSERT` rather than insert-or-update, because the primary key already states that a
    tenant has one mailbox — the second connect is a *change* of mailbox, not a conflict
    to resolve. `sync_cursor` deliberately resets to empty: a different mailbox has a
    different numbering, and carrying the old cursor across would either skip everything
    or replay everything depending on which way the numbers happened to fall.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPSERT INTO mail_account (tenant_id, provider, address, secret_arn,
                                         imap_host, imap_port, smtp_host, smtp_port,
                                         sync_cursor, state, last_error, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '', 'connected', '', now())""",
            (tenant_id, provider, address, secret_arn, imap_host, imap_port,
             smtp_host, smtp_port))
    got = load_account(conn, tenant_id)
    if got is None:                                       # pragma: no cover
        raise MailboxRefused("the mailbox row vanished immediately after being written")
    return got


def disconnect(conn: psycopg.Connection, tenant_id: str) -> str:
    """Detach the mailbox and hand back the ARN whose secret the caller must delete.

    The row goes and the secret does not, because they live in different stores and this
    function only has a transaction over one of them. Returning the ARN rather than
    deleting it here keeps that honest: the caller does both, in an order it can see, and
    a failure to delete the secret leaves an orphan that `vault.delete` can still reach
    rather than one whose pointer has already been thrown away.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mail_account WHERE tenant_id = %s RETURNING secret_arn",
            (tenant_id,))
        row = cur.fetchone()
    return str(row["secret_arn"]) if row else ""


def mark_failed(conn: psycopg.Connection, tenant_id: str, reason: str) -> None:
    """Stop polling this mailbox and say why. See the module header."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_account SET state = 'failed', last_error = %s,
                                       last_polled_at = now(), updated_at = now()
                WHERE tenant_id = %s""",
            (reason[:1000], tenant_id))


def record_poll(conn: psycopg.Connection, tenant_id: str, cursor: str) -> None:
    """A poll finished. Advance the cursor and stamp the clock, in one statement.

    Both together, because a cursor advanced without the clock would let the same mailbox
    win `due()` forever, and a clock stamped without the cursor would re-read the same
    messages on the next pass. They are one fact about one poll.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_account SET sync_cursor = %s, last_polled_at = now(),
                                       last_error = '', updated_at = now()
                WHERE tenant_id = %s""",
            (cursor, tenant_id))


def due(conn: psycopg.Connection) -> list[Account]:
    """Every connected mailbox, oldest poll first. See the module header on the shape.

    The ordering matters under a batch limit: without it one tenant whose mailbox sorts
    early is polled every pass and a tenant who sorts late is never polled at all.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM tenant")
        tenant_ids = [str(row["id"]) for row in cur.fetchall()]

    accounts = [load_account(conn, tenant_id) for tenant_id in tenant_ids]
    connected = [a for a in accounts if a is not None and a.state == "connected"]
    return connected


# ------------------------------------------------------------------- the poll

def poll_once(conn: psycopg.Connection, account: Account, *,
              vault: Any, client: Any = None) -> list[inbound.Delivery]:
    """Read one mailbox and file everything in it.

    The ordering is chosen for the same reason `sender.py`'s is, pointed the other way.
    Sending is irreversible so it claims *before* acting; receiving is idempotent — every
    write path here is keyed — so it acts first and records the cursor **after** the
    deliveries have committed. A crash between the two re-reads a handful of messages
    that `inbound.deliver` then recognises and discards, which is the cheap direction.
    Advancing the cursor first would lose them permanently, which is not.

    A rejected credential ends the poll for this tenant and is not retried; see the
    module header.
    """
    from spindle.mailbox import gmail, imap        # noqa: PLC0415 — avoids a cycle

    try:
        credential = vault.get(account.secret_arn)
    except vault_mod.SecretMissing as exc:
        mark_failed(conn, account.tenant_id, str(exc))
        return []

    adapter = gmail if account.provider == "gmail" else imap
    key = "refresh_token" if account.provider == "gmail" else "password"
    try:
        messages, cursor = adapter.fetch(account, credential.get(key, ""), client=client)
    except MailboxRefused as exc:
        mark_failed(conn, account.tenant_id, str(exc))
        return []

    delivered = [inbound.deliver(conn, msg, tenant_id=account.tenant_id)
                 for msg in messages]
    record_poll(conn, account.tenant_id, cursor)
    return delivered
