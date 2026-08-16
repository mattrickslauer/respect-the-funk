"""Whose address a pitch goes out from. One decision, made in one place.

Everything that sends outreach asks `identity_for`, and it answers with exactly one of
two things: the platform's SES identity, or the tenant's own connected mailbox. There is
no third answer, and — the part worth defending — there is no *degradation* between them.

## Why a failed mailbox refuses instead of falling back

The tempting behaviour is: tenant's mailbox is broken, so send through the platform
anyway, because a delivered pitch beats an undelivered one. It is wrong, and not
marginally.

A tenant connects a mailbox precisely so their outreach carries their own name. Falling
back would put their pitch in front of a curator under a `From` they never agreed to,
signed by a domain that is not theirs, in a conversation they will be judged on. They
would find out from the curator. `no-fallbacks` is a rule in this repository about silent
defaults hiding the error that caused them, and this is the case it was written for: the
error is "your mailbox stopped working", the tenant can fix that in a minute, and hiding
it behind a successful-looking send means nobody ever tells them.

So `MailboxUnavailable` carries the provider's own reason, in the words `mailbox.py`
recorded for the tenant, and the send does not happen.

## The three refusals, in the order they are checked

The order is load-bearing and each check is prior to the next:

1. **`sends_enabled`** — a plan matter, checked first so that turning sending off for a
   tier cannot be defeated by a mailbox row that is still connected.
2. **`connects_mailbox` against a row that exists** — a tenant who connected on a paid
   plan and then downgraded. The row and the entitlement disagree; that is a decision for
   a person, not something a send path should quietly resolve by picking either one.
3. **`state == 'connected'`** — the mailbox itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from spindle import mail, mailbox, settings as settings_mod, vault as vault_mod


class MailboxUnavailable(RuntimeError):
    """A tenant's own mailbox exists but cannot be used, and no substitute is made."""


class SendingNotPermitted(RuntimeError):
    """The plan does not allow outbound mail at all."""


@dataclass(frozen=True)
class Identity:
    """Who is sending, resolved. `account` is `None` on the platform identity.

    `uses_reply_token` is the one behavioural difference the callers care about, and it
    is here rather than inferred from `kind` so the reason travels with the value. The
    platform sends every tenant's mail from one address, so a reply carries no other
    indication of which thread it belongs to and the plus-addressed token is the only
    routing there is. A tenant sending from their own mailbox has no such problem: the
    reply arrives in the mailbox we poll, and `In-Reply-To` identifies the thread — so
    tagging their address would add a token to no purpose and make their `Reply-To` look
    like a tracking address to a curator reading it.
    """

    kind: str
    sender: str
    reply_to: str
    uses_reply_token: bool
    account: mailbox.Account | None = None


def identity_for(conn: psycopg.Connection, tenant_id: str, tier: Any) -> Identity:
    """The identity this tenant's outreach sends under, or a refusal naming why."""
    if not tier.sends_enabled:
        raise SendingNotPermitted(
            f"The {tier.name} plan does not permit outbound mail. Nothing has been "
            "sent and no message has been consumed from the outbox.")

    account = mailbox.load_account(conn, tenant_id)

    if account is None:
        return _platform_identity()

    if not tier.connects_mailbox:
        raise MailboxUnavailable(
            f"{account.address} is connected as this tenant's sending mailbox, but the "
            f"{tier.name} plan does not include a connected mailbox — this account was "
            "most likely downgraded after connecting. Sending has stopped rather than "
            "silently switching to the platform address, because a pitch signed by this "
            "label should not go out from somebody else's domain. Upgrade to resume, or "
            "disconnect the mailbox to send through the platform instead.")

    if account.state != "connected":
        raise MailboxUnavailable(
            f"{account.address} is not usable: {account.last_error or account.state}. "
            "Reconnect it from the account page. Nothing has been sent — this label's "
            "outreach is not switched to the platform address behind your back.")

    return Identity(kind="tenant", sender=account.address, reply_to=account.address,
                    uses_reply_token=False, account=account)


def for_tenant(conn: psycopg.Connection, tenant_id: str) -> Identity:
    """`identity_for`, with the tier looked up rather than passed in.

    The convenience form for callers that have a tenant and no opinion about plans —
    `sender.send_once` is the one that matters. `accounts.plan_for_tenant` returns `None`
    for a tenant that is not plan-metered at all (a fixture, a seeded demo), and that is
    not an error: those tenants predate billing and have always been allowed to send, so
    they resolve straight to the platform identity without a plan check there is no plan
    to make.
    """
    from spindle import accounts                     # noqa: PLC0415 — avoids a cycle

    tier = accounts.plan_for_tenant(conn, tenant_id)
    if tier is None:
        return _platform_identity()
    return identity_for(conn, tenant_id, tier)


def _platform_identity() -> Identity:
    """The shared SES identity, which is what a tenant with no mailbox sends on.

    Reads `settings` rather than taking configuration as an argument for the same reason
    `mail.load()` does.

    ## It checks the reply-to and deliberately not the sender

    `send_once` already draws this line and its comment names it: some guards protect the
    **message** and some protect the **transport**, and they are kept apart so that a
    caller supplying its own mailer still has the message checked while a caller that
    forgot one still fails.

    An `Identity` is message-level. What it contributes to a send is the address a reply
    comes back to and the address the opt-out line names — both `mail_reply_to`. Whether
    there is a *verified SES sending identity* is the transport's question, `mail.load()`
    asks it, and `mailer_for` below reaches that on the same call path a moment later.

    Checking `transactional_mail_configured` here — which requires both — conflated the
    two, and the conflation was not theoretical: it made identity resolution fail for a
    caller that had injected a working mailer and never needed SES at all. Worse, it
    would have required `PLATFORM_MAIL_SENDER` to be set before a tenant sending from
    *their own* connected mailbox could resolve an identity, which is backwards.
    """
    config = settings_mod.load()
    if not config.mail_reply_to:
        raise mail.MailNotConfigured(
            "PLATFORM_MAIL_REPLY_TO is unset, and it is the address a curator's reply "
            "comes back to for every tenant without a connected mailbox. Set it, or "
            "have the tenant connect their own mailbox.")
    return Identity(kind="platform", sender=config.mail_sender,
                    reply_to=config.mail_reply_to, uses_reply_token=True)


def mailer_for(identity: Identity, *, vault: Any = None) -> mail.Mailer:
    """The transport for a resolved identity.

    The credential is fetched here and nowhere earlier, which is why `Account` carries an
    ARN rather than a secret: an identity can be resolved, logged and passed around
    freely, and only the moment something actually sends does a credential exist in
    memory at all.

    `vault` is optional and resolved lazily *inside the tenant branch*, which is not a
    style preference. Constructing a vault builds a Secrets Manager client and imports
    boto3, and the platform identity needs neither — so taking a required `vault`
    argument meant every platform send paid for an AWS client it never called, and
    would fail outright in any process where boto3 is absent. The platform path is the
    one every free tenant uses; it must not depend on the machinery that exists for
    paying ones.
    """
    if identity.kind == "platform":
        return mail.load()

    account = identity.account
    if account is None:                                   # pragma: no cover
        raise MailboxUnavailable("a tenant identity with no account row")

    if vault is None:
        config = settings_mod.load()
        vault = vault_mod.load(env=config.env, region=config.region)
    credential = vault.get(account.secret_arn)
    if account.provider == "gmail":
        return mail.GmailMailer(sender=account.address,
                                refresh_token=credential.get("refresh_token", ""))
    return mail.SMTPMailer(sender=account.address, host=account.smtp_host,
                           port=account.smtp_port,
                           password=credential.get("password", ""))
