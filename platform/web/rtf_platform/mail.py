"""The one irreversible act in the product, behind a Protocol.

Everything else this system does can be undone. A wrong fact is superseded, a bad lead is
parked, a duplicated party is merged. **A sent email cannot be recalled**, and the whole
shape of `outbox` — one row per message, a unique idempotency key, an approver recorded
before the row exists — is built around that asymmetry. This module is where it actually
leaves the building.

## Why Amazon SES and not Gmail

Gmail was considered and rejected on three counts, all of them fatal rather than
inconvenient:

  * **Wrong layer.** A Gmail MCP server is a tool an *assistant* holds. The thing that
    needs to send here is a worker draining `outbox` — possibly in Lambda, possibly on a
    laptop at 3am — and it cannot borrow somebody's interactive OAuth session.
  * **Limits.** 500 messages/day on consumer Gmail, 2,000 on Workspace, and cold
    outreach from a personal mailbox is the fastest known way to have that mailbox
    suspended. Losing the label's actual email to a hackathon demo is not a trade worth
    making.
  * **No feedback loop.** Gmail will not tell you a message hard-bounced. SES publishes
    bounces and complaints to SNS, and those map exactly onto the `contact_route.state`
    values migration 018 already defined — `bounced` and `opted_out`. The route learns
    from the send, which is the same accumulation argument the `lesson` loop makes.

SES is also AWS, which keeps the deployment on one cloud and one IAM model.

## At-most-once, deliberately

The gap between "we called SES" and "we recorded that we called SES" is the only place a
crash can produce a double send. Two disciplines close it, and the second is a choice
worth defending:

1. The outbox row is moved to `claimed` **in its own committed transaction** before the
   network call. A worker that dies mid-send leaves a `claimed` row, not a `pending` one.
2. **A stale `claimed` row is never automatically retried.** It surfaces for a human.

That is at-most-once, and it means a crash at the wrong moment can lose a send. That is
the correct direction to fail. `010`'s own comment hoped the provider would deduplicate on
the idempotency key; SES does not offer that, so the guarantee has to come from here
instead of from a hope about the provider. A missed pitch costs nothing. A second pitch to
a curator who already got one costs the label its reputation with that curator.

## No silent no-op adapter

There is no `NullMailer` that logs and returns success. An unconfigured deployment raises
`MailNotConfigured`, and `sender.py` refuses to claim the outbox at all. A send that
quietly did not happen, recorded as `sent`, is the worst failure this table can have: the
thread advances, the lesson loop learns from an outcome that never occurred, and nobody
finds out until a curator says they never heard from you.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from rtf_platform import settings as settings_mod


class MailNotConfigured(RuntimeError):
    """No verified sender, reply-to, or postal address. Nothing may be sent."""


class MailRefused(RuntimeError):
    """The provider rejected the message. Carries whether retrying could ever work.

    `permanent` is the difference between "SES is throttling us" and "that address does
    not exist". The first goes back to `pending` with a backoff; the second must not,
    because retrying a hard bounce is how a sender's reputation is destroyed.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True)
class Sent:
    """What the provider said. `provider_message_id` goes on the `message` row."""

    provider_message_id: str
    cost_usd: str


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, body: str,
             idempotency_key: str) -> Sent: ...


#: SES hard-fails these; retrying makes the sender's reputation worse, never better.
_PERMANENT = (
    "MessageRejected", "MailFromDomainNotVerified", "AccountSendingPausedException",
    "ConfigurationSetDoesNotExist", "InvalidParameterValue",
)


class SESMailer:
    """Amazon SES, via boto3.

    boto3 is not in `requirements.txt` for the same reason `storage.py` gives: the Lambda
    Python runtime already ships it, and vendoring it costs ~90 MB in a bundle whose whole
    design is to stay small.

    The `idempotency_key` is sent as a custom header rather than passed to an API
    parameter, because SES has no idempotency parameter. It is there so that a message
    arriving twice can be *identified* as the same message by anyone reading the raw
    headers — a forensic aid, not a guarantee. The guarantee is the claim discipline in
    this module's header.
    """

    def __init__(self, *, sender: str, reply_to: str, region: str) -> None:
        self.sender = sender
        self.reply_to = reply_to
        self.region = region

    def send(self, *, to: str, subject: str, body: str,
             idempotency_key: str) -> Sent:
        import boto3                                   # noqa: PLC0415 — see docstring
        from botocore.exceptions import ClientError    # noqa: PLC0415

        client = boto3.client("ses", region_name=self.region)
        try:
            response = client.send_email(
                Source=self.sender,
                Destination={"ToAddresses": [to]},
                ReplyToAddresses=[self.reply_to],
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                },
                Tags=[{"Name": "idempotency-key", "Value": idempotency_key[:256]}],
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise MailRefused(f"SES refused: {code}: {exc}",
                              permanent=code in _PERMANENT) from exc

        return Sent(provider_message_id=response.get("MessageId", ""),
                    cost_usd="0.0001")


def load() -> Mailer:
    """The configured mailer, or a refusal. Never a no-op."""
    config = settings_mod.load()
    if not config.mail_configured:
        raise MailNotConfigured(
            "PLATFORM_MAIL_SENDER, PLATFORM_MAIL_REPLY_TO and "
            "PLATFORM_MAIL_POSTAL_ADDRESS must all be set before anything can be sent. "
            "The sender is not a component that degrades — it either sends a lawful, "
            "replyable message or it does not run.")
    return SESMailer(sender=config.mail_sender, reply_to=config.mail_reply_to,
                     region=config.region)


def compliant_body(body: str, *, postal_address: str, unsubscribe: str) -> str:
    """The message as it will actually be sent, with the footer the law requires.

    CAN-SPAM §7704(a)(5) requires a valid physical postal address in every commercial
    message, and §7704(a)(3) a working opt-out. Both are appended here rather than left to
    whoever composed the draft, because a compliance rule that depends on an author
    remembering it is a rule that will be broken on the day somebody is in a hurry.

    The opt-out line names `contact_route`'s terminal state on purpose: replying STOP is
    not a courtesy, it sets `state = 'opted_out'`, which migration 018 made impossible for
    any discovery stage to overwrite.
    """
    return (
        f"{body.rstrip()}\n\n"
        f"—\n"
        f"{postal_address}\n"
        f"Reply STOP and we will not contact you again from this system.\n"
        f"{unsubscribe}".rstrip() + "\n"
    )
