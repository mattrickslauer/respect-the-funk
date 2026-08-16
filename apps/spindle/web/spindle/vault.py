"""Where a tenant's mailbox credential lives, which is deliberately not this database.

## The distinction this module exists to hold

Every other credential in this system is a hash. `account.token_hash` is sha256 and
`033_account_billing.sql`'s header spends a page on why: the raw token is never stored,
so a dump of that table — "a backup on somebody's laptop, a screenshot in a ticket" — is
not a set of working credentials. `listen_token` repeats the argument for the same reason.

A mailbox credential cannot follow that rule and it is important to say why rather than
to appear to have forgotten. A Gmail refresh token and an IMAP password are **replayed at
a provider**: we do not compare them against something a caller presents, we present them
ourselves, so they have to come back out in their original form. Hashing them would not
be stricter, it would be non-functional.

So the rule that replaces it: the credential is not in CockroachDB at all.
`mail_account.secret_arn` holds an ARN, an ARN is a *pointer*, and the thing it points at
lives in AWS Secrets Manager behind an IAM boundary this application's database role has
nothing to do with. The property preserved from `033` is the one that matters — a cluster
dump is still not a set of working credentials — and it is preserved by a different
mechanism because the old mechanism does not apply.

## The name is a security control

`spindle/<env>/mailbox/<tenant_id>`, and the IAM policy attached to the console and worker
roles is written against that prefix. That makes the path an access boundary rather than a
formatting convention, which is why `secret_name` refuses a blank environment or a blank
tenant instead of cheerfully producing `spindle//mailbox/` — a path that sits outside the
prefix the policy grants, still looks plausible in a log, and fails at the API with a
permission error that names nothing useful.

The environment is in the path because staging is usually restored from a production
backup. Same tenant ids, different deployment; without the `<env>` segment, staging would
hold ARNs that resolve, and a test run would send mail as a real label.

## What is never allowed to happen

The realistic leak here is not a database dump. It is a traceback in CloudWatch with a
refresh token sitting in a stack frame, or a `logger.info` of a request body during an
incident. So: no method here puts the payload into an exception message, `__repr__` holds
no payload, and the payload is a local that dies with the call. `test_vault.py` asserts
both of those directly rather than trusting the convention.

## Why boto3 is imported lazily

`storage.py` gives the full argument and it applies unchanged: the SDK ships inside the
Lambda runtime, vendoring it costs ~90 MB in a bundle designed to stay small, and the
console imports this module on every request that renders `/account`. The import is
deferred so a page that has nothing to do with mailboxes does not require boto3 to exist.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class SecretsUnconfigured(RuntimeError):
    """No environment name or no region. `load` raises rather than returning a no-op.

    The same argument `mail.load()` makes for refusing to return a mailer that silently
    discards: a vault that accepted `put` and stored nothing would let a tenant complete
    an OAuth consent screen, see "connected", and own a `mail_account` row pointing at an
    ARN that was never created. The failure would surface days later as a poll that
    cannot authenticate, with nothing linking it back to the connect that lied.
    """


class SecretMissing(RuntimeError):
    """The ARN resolved to nothing. Raised rather than returning `None`.

    A caller that got `None` would have to remember to check, and the cost of forgetting
    is an attempt to send mail with no credential — which, depending on the transport,
    is either an exception much further from the cause or an unauthenticated SMTP
    connection. Neither is discoverable from the symptom.
    """


def secret_name(env: str, tenant_id: str) -> str:
    """The path, which the IAM policy is written against. See the module header.

    Both arguments are checked because both are load-bearing, and because the values
    that reach here come from configuration and a database row respectively — neither is
    a literal, so neither is guaranteed non-empty by inspection.
    """
    if not env.strip():
        raise ValueError(
            "vault.secret_name was given a blank environment. The name is the prefix "
            "the IAM policy grants against, so a blank segment produces a path outside "
            "it — set PLATFORM_ENV rather than storing a secret nothing can read.")
    if not tenant_id.strip():
        raise ValueError(
            "vault.secret_name was given a blank tenant id. Every mailbox credential "
            "belongs to exactly one tenant and a shared path would let one tenant's "
            "reconnect overwrite another's token.")
    return f"spindle/{env.strip()}/mailbox/{tenant_id.strip()}"


class Vault(Protocol):
    """Three operations, which is all a mailbox connection needs.

    Rotation is deliberately absent. Secrets Manager's own rotation is built for
    credentials it can mint — a database password it may change on both ends — and it
    cannot mint a Google refresh token. Re-consent is the rotation, and re-consent is a
    `put` that replaces.
    """

    def put(self, tenant_id: str, payload: dict[str, Any]) -> str: ...

    def get(self, arn: str) -> dict[str, Any]: ...

    def delete(self, arn: str) -> None: ...


class SecretsManagerVault:
    """The one adapter. `client` is injected so the suite can drive it without AWS."""

    def __init__(self, *, env: str, client: Any = None, region: str = "") -> None:
        self.env = env
        self._client = client if client is not None else _client(region)

    def __repr__(self) -> str:
        """Names the boundary and nothing inside it. Asserted by the suite — see the
        module header on where credentials actually leak."""
        return f"<SecretsManagerVault env={self.env!r}>"

    def put(self, tenant_id: str, payload: dict[str, Any]) -> str:
        """Create, or replace if it is already there.

        The `ResourceExistsException` path is not an edge case — it is the normal second
        connect. A tenant who revokes access in their Google account and reconnects, or
        who rotates an app password, arrives here with a secret already at this name. The
        second put wins, because the alternative is asking a tenant to delete something
        they cannot see.
        """
        name = secret_name(self.env, tenant_id)
        body = json.dumps(payload)
        try:
            try:
                got = self._client.create_secret(Name=name, SecretString=body)
            except self._client.exceptions.ResourceExistsException:
                got = self._client.put_secret_value(SecretId=name, SecretString=body)
        except Exception as exc:
            # `from None`, and a message built only from the name. `exc` is re-raised
            # nowhere and not chained, because botocore puts the request it was given
            # into the exception's own string on some error paths, and the request is
            # the thing holding the credential. Losing the provider's wording is a real
            # cost and it is smaller than the alternative — see the module header.
            raise RuntimeError(
                f"Secrets Manager refused to store the mailbox credential at {name}. "
                f"The provider's message is withheld deliberately: it can quote the "
                f"request, and the request is the credential. Check the IAM policy "
                f"grants secretsmanager:CreateSecret and PutSecretValue on "
                f"spindle/{self.env}/mailbox/*, then look at CloudTrail for the "
                f"denied call. ({type(exc).__name__})") from None
        return str(got["ARN"])

    def get(self, arn: str) -> dict[str, Any]:
        try:
            got = self._client.get_secret_value(SecretId=arn)
        except self._client.exceptions.ResourceNotFoundException:
            raise SecretMissing(
                f"No mailbox credential at {arn}. The mail_account row points at a "
                f"secret that is not there, which means the two stores disagree — "
                f"either the secret was deleted out of band or the connect that wrote "
                f"this row did not finish. Disconnect and reconnect the mailbox.") from None
        return dict(json.loads(got["SecretString"]))

    def delete(self, arn: str) -> None:
        """Immediate, not the 30-day recovery window.

        `ForceDeleteWithoutRecovery` because a disconnected mailbox must stop being
        replayable *now*. The recovery window is designed for credentials whose loss
        breaks a service; this one's loss breaks nothing — the tenant reconnects — while
        its lingering existence is a live token for a mailbox somebody asked us to let go
        of. That asymmetry decides it.
        """
        self._client.delete_secret(SecretId=arn, ForceDeleteWithoutRecovery=True)


def load(*, env: str, region: str) -> Vault:
    """The configured vault, or a refusal. Never a no-op. See `SecretsUnconfigured`."""
    if not env.strip() or not region.strip():
        raise SecretsUnconfigured(
            "A mailbox credential needs both PLATFORM_ENV and PLATFORM_REGION to be "
            "set: the first is the IAM prefix the secret is written under and the "
            "second is the account-local region it lives in. "
            f"env={env!r} region={region!r}. Until both are set, connecting a mailbox "
            "is refused rather than stored somewhere nothing will look for it.")
    return SecretsManagerVault(env=env, region=region)


def _client(region: str):
    """Deferred import, for the reason in the module header and `storage.py`'s."""
    try:
        import boto3                                   # noqa: PLC0415 — see docstring
    except ImportError as exc:                         # pragma: no cover
        raise SecretsUnconfigured(
            "boto3 is not installed in this process. It ships inside the Lambda runtime "
            "and is listed in requirements-dev.txt and requirements-worker.txt for "
            "local and worker use; install those rather than adding it to "
            "requirements.txt, which is sized for the bundle.") from exc
    return boto3.client("secretsmanager", region_name=region)
