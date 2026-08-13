"""Configuration, from the environment and nowhere else.

Secrets Manager is not used. One secret there is $0.40/month, which is real money
against an idle bill this project intends to keep at zero, and Lambda environment
variables are already encrypted at rest with a KMS key AWS provides free. Revisit
when there is a second consumer that needs rotation without a deploy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_token: str
    tenant_slug: str
    masters_bucket: str
    region: str
    classifier_function: str
    mail_sender: str
    mail_reply_to: str
    mail_postal_address: str

    @property
    def configured(self) -> bool:
        """False in a fresh checkout with no .env — the console says so rather
        than dying with a connection error nobody can act on."""
        return bool(self.database_url)

    @property
    def classifier_configured(self) -> bool:
        """Whether a genre can be produced at all.

        Separate from `storage_configured` because the two fail independently and mean
        different things: no bucket means nothing can be uploaded, no classifier means
        an uploaded master gets a tempo and no genre. Neither is guessed at.
        """
        return bool(self.classifier_function)

    @property
    def mail_configured(self) -> bool:
        """Whether a send can legally and technically happen.

        All three are required and none is defaulted, because each absence produces a
        different kind of wrong. Without a verified `mail_sender` SES rejects the call.
        Without `mail_postal_address` the message violates CAN-SPAM §7704(a)(5), which
        requires a valid physical address in every commercial email — a legal defect the
        provider will happily deliver for you. `mail_reply_to` is required because a
        pitch a curator cannot reply to is not outreach, it is spam with extra steps.

        `sender.py` refuses to claim the outbox when this is false. It does not send a
        degraded message.
        """
        return bool(self.mail_sender and self.mail_reply_to
                    and self.mail_postal_address)

    @property
    def storage_configured(self) -> bool:
        """Whether masters can be uploaded at all.

        Deliberately separate from `configured`: storage is the one capability here
        that can be absent while everything else works, and the console needs to say
        which of the two states it is in rather than rendering an upload form whose
        submit will raise. `storage.py`'s header explains why there is no local
        adapter to fall back to.
        """
        return bool(self.masters_bucket)


def load() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        # Writes require this. Empty means the console is read-only for everyone,
        # which is the correct posture for a deployment nobody has configured.
        admin_token=os.environ.get("PLATFORM_ADMIN_TOKEN", ""),
        tenant_slug=os.environ.get("PLATFORM_TENANT_SLUG", "respect-the-funk"),
        # Where masters go. Empty is a legitimate state — a checkout that has never
        # run `terraform apply` has no bucket — and the console reports it rather
        # than inventing a local directory nothing else in the system can read.
        masters_bucket=os.environ.get("PLATFORM_MASTERS_BUCKET", ""),
        # The genre classifier Lambda. Empty means `analyse_recording` measures tempo
        # and writes no genre at all, rather than falling back to something weaker and
        # labelling it the same way — the run summary says which happened.
        classifier_function=os.environ.get("PLATFORM_CLASSIFIER_FUNCTION", ""),
        # The verified SES identity every pitch is sent from, the address a curator's
        # reply reaches, and the physical postal address CAN-SPAM requires in the body.
        # All empty by default: a deployment nobody has configured must not be one
        # keystroke away from mailing strangers.
        mail_sender=os.environ.get("PLATFORM_MAIL_SENDER", ""),
        mail_reply_to=os.environ.get("PLATFORM_MAIL_REPLY_TO", ""),
        mail_postal_address=os.environ.get("PLATFORM_MAIL_POSTAL_ADDRESS", ""),
        # Used to construct the S3 and SES clients. `AWS_REGION` is set by the Lambda
        # runtime itself, so in the deployed function this needs no configuration;
        # the explicit variable is for a worker or a laptop, and the default matches
        # `platform/infra/variables.tf`.
        region=(os.environ.get("PLATFORM_REGION")
                or os.environ.get("AWS_REGION")
                or "us-east-1"),
    )
