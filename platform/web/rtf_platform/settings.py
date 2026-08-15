"""Configuration, from the environment and nowhere else.

Secrets Manager is not used. One secret there is $0.40/month, which is real money
against an idle bill this project intends to keep at zero, and Lambda environment
variables are already encrypted at rest with a KMS key AWS provides free. Revisit
when there is a second consumer that needs rotation without a deploy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class Settings:
    database_url: str
    masters_bucket: str
    region: str
    classifier_function: str
    mail_sender: str
    mail_reply_to: str
    mail_postal_address: str
    cockroach_api_key: str
    cockroach_cluster_id: str
    mcp_url: str
    openai_api_key: str
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_label: str
    stripe_price_roster: str

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
    def mcp_configured(self) -> bool:
        """Whether the Ask screen can reach anything at all.

        All four are required and none has a working default. The Cockroach key and
        cluster id address the managed MCP server; `OPENAI_API_KEY` pays for the one
        classification that turns English into a choice from `mcp.QUESTIONS`; and
        `database_url` is where the database *name* comes from, because the MCP query
        must run against the database the rest of the console reads and two settings
        holding one fact is how they end up disagreeing.

        This property exists so the console can render the screen's own empty state —
        naming the missing variables — instead of offering a box whose submit raises.
        `mcp.load()` still refuses independently; a page that only checked here would be
        one deploy away from a form that posts into a traceback.
        """
        return bool(self.cockroach_api_key and self.cockroach_cluster_id
                    and self.openai_api_key and self.database_url)

    #: The four variables billing needs, paired with the attribute each lands in. One
    #: tuple so `stripe_configured` and `stripe_missing` cannot come to check different
    #: sets — the bug where a page says "configured" and the endpoint then names a
    #: variable the page did not know about.
    #:
    #: `ClassVar` and not a field: this is a description of the settings, not one of
    #: them, and without the annotation `dataclass` would add it to `__init__` as a
    #: fifteenth constructor argument that every caller would then be able to override.
    _STRIPE_VARS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("STRIPE_SECRET_KEY", "stripe_secret_key"),
        ("STRIPE_WEBHOOK_SECRET", "stripe_webhook_secret"),
        ("STRIPE_PRICE_LABEL", "stripe_price_label"),
        ("STRIPE_PRICE_ROSTER", "stripe_price_roster"),
    )

    @property
    def stripe_missing(self) -> list[str]:
        """Exactly which Stripe variables are absent, by environment-variable name.

        The same shape as `_mcp_missing` on the Ask screen, and for the same reason:
        "billing is not configured" is not an actionable message, and this needs four
        things. Naming them is the difference between a refusal somebody fixes in a
        minute and one they file a bug about.

        A list rather than a formatted sentence, because two surfaces render it — the
        checkout refusal as JSON, and the pricing page as prose — and a pre-formatted
        string would force one of them to parse English.
        """
        return [name for name, attr in self._STRIPE_VARS if not getattr(self, attr)]

    @property
    def stripe_configured(self) -> bool:
        """Whether a checkout can be created at all.

        All four are required and none has a default, because each absence produces a
        different kind of wrong and none of them is recoverable at runtime. Without the
        secret key there is no API call. Without the webhook secret a completed payment
        cannot be *verified*, and an unverified webhook is worse than none — it is an
        unauthenticated endpoint that grants plans to whoever posts to it. Without the
        two price ids there is nothing to sell; Stripe prices are objects created in the
        dashboard and their ids differ between test and live mode, which is precisely why
        they are configuration and not constants in `plans.py`.

        There is no partial mode. A deployment with a key and no webhook secret could
        technically send somebody to a checkout page, and it would take their money and
        never hear that it happened. `billing.py` refuses instead.
        """
        return not self.stripe_missing

    @property
    def stripe_mode(self) -> str:
        """`"test"`, `"live"` or `"unconfigured"`, read from the key's own prefix.

        Stripe stamps the mode into the credential — `sk_test_…` and `sk_live_…` — so the
        mode is a fact about the key rather than a second setting that could disagree
        with it. A separate `STRIPE_TEST_MODE` variable is the obvious alternative and it
        is strictly worse: it can be set to `test` next to a live key, and the deployment
        would then label real charges as test charges in the UI. Deriving it means the
        label cannot lie.

        A key that is neither prefix is `"unconfigured"` rather than assumed to be test.
        This is the no-fallbacks rule at its most literal: the safe-looking guess is
        "test", and guessing "test" about a credential that turned out to be live is how
        a UI ends up reassuring somebody while taking their money.
        """
        if self.stripe_secret_key.startswith("sk_test_"):
            return "test"
        if self.stripe_secret_key.startswith("sk_live_"):
            return "live"
        return "unconfigured"

    @property
    def stripe_test_mode(self) -> bool:
        """The flag a UI puts a "test mode" badge behind. Derived, so it cannot drift."""
        return self.stripe_mode == "test"

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
        # `PLATFORM_ADMIN_TOKEN` and `PLATFORM_TENANT_SLUG` were both read here until
        # 2026-08-15. They went together, because they were two halves of one thing: the
        # shared operator token authenticated a principal with no tenant, and the slug
        # was how the console decided which tenant that principal meant. With email-OTP
        # sign-in every principal carries a real `tenant_id` from its own `account` row,
        # so neither has anything left to answer. `auth.py` records what was given up
        # with the operator, which is more than an unused setting.
        #
        # Setting either on a deployment now does nothing at all, which is why they are
        # named here rather than silently absent.
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
        # CockroachDB Cloud, for the Ask screen's route through the Cloud Managed MCP
        # Server. The key is a plain bearer token against `cockroachlabs.cloud/mcp` and
        # is *not* something a human has to mint by hand: `ccloud auth login` already
        # writes one to ~/.config/.cockroachdb/credentials.json, verified 2026-08-13 to
        # authenticate that endpoint. It is still read from the environment and only
        # from the environment, because that file does not exist in Lambda and a loader
        # that reads two sources answers differently depending on where it runs.
        #
        # The cluster id is deliberately not defaulted to the one in `.mcp.json`. A
        # default here would point a differently-deployed console at this project's
        # cluster and answer its questions confidently from somebody else's data.
        cockroach_api_key=os.environ.get("COCKROACH_API_KEY", ""),
        cockroach_cluster_id=os.environ.get("COCKROACH_CLUSTER_ID", ""),
        # The vendor's endpoint. Defaulted, unlike everything else above, because it is
        # not a credential and there is exactly one correct value; the variable exists so
        # a test or a future regional endpoint can point elsewhere without a code change.
        mcp_url=(os.environ.get("COCKROACH_MCP_URL")
                 or "https://cockroachlabs.cloud/mcp"),
        # The classifier that maps an operator's English onto one of `mcp.QUESTIONS`.
        # OpenAI rather than Bedrock because Bedrock is not reachable from this account —
        # `spend.py`'s rate card records both Claude and Titan on-demand quotas at 0 RPM,
        # measured. Empty means the Ask screen says so rather than guessing a question.
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        # Stripe. All four empty by default, and there is deliberately no test-mode
        # placeholder among them — not `sk_test_...`, not a dummy price id, nothing.
        #
        # A fake key is the tempting default because it makes the code path "work" in
        # development, and it is the exact failure this repository's standing rule
        # forbids: the checkout would build a request, Stripe would reject it, and the
        # operator would be debugging an authentication error from a vendor instead of
        # reading "STRIPE_SECRET_KEY is unset". `stripe_missing` names what is absent;
        # `billing.py` refuses before it constructs anything.
        #
        # The free tier does not read any of these. `POST /claim` works end to end with
        # all four empty, which is not an accident of the implementation but the
        # requirement it was built to: the tier a judge uses cannot depend on a payment
        # provider being configured.
        stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
        # Stripe signs every webhook with this and `billing.verify_signature` checks it.
        # Absent, the webhook refuses every request rather than trusting the body — an
        # unverified webhook endpoint is an unauthenticated way to grant paid plans.
        stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        # Price object ids from the Stripe dashboard, one per purchasable tier. They are
        # configuration rather than constants in `plans.py` because a price id is
        # mode-specific: the test-mode id and the live-mode id for the same $49 product
        # are different strings, and a value compiled into the source could not be both.
        # `plans.Tier.stripe_price_setting` names which of these a tier uses.
        stripe_price_label=os.environ.get("STRIPE_PRICE_LABEL", ""),
        stripe_price_roster=os.environ.get("STRIPE_PRICE_ROSTER", ""),
        # Used to construct the S3 and SES clients. `AWS_REGION` is set by the Lambda
        # runtime itself, so in the deployed function this needs no configuration;
        # the explicit variable is for a worker or a laptop, and the default matches
        # `platform/infra/variables.tf`.
        region=(os.environ.get("PLATFORM_REGION")
                or os.environ.get("AWS_REGION")
                or "us-east-1"),
    )
