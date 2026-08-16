"""The first recoverable secret this schema has ever held, and the rules around it.

Every other credential in this system is stored as a hash. `account.token_hash` is
sha256 and `listen_token.token_hash` is sha256, and both get away with it because they
are only ever *compared* — the raw value arrives on a request and the stored value only
has to say yes or no. A mailbox credential is different in kind: an OAuth refresh token
or an IMAP password has to be **replayed at a provider**, so it has to come back out.
That is not a weaker version of the same thing, it is a different threat model, and this
module is where it is confined.

The consequence, stated once: a dump of the CockroachDB cluster no longer contains
everything needed to impersonate a tenant, because it never contains the credential —
only the ARN of a secret held in AWS Secrets Manager under a separate IAM boundary.
`mail_account.secret_arn` is a pointer, and a pointer is not a credential.

**DB-free by construction.** Nothing here opens a connection; the vault is a Protocol
and every test drives a fake. That is deliberate — a test that needed a cluster to prove
a redaction rule would be a test nobody runs.
"""

from __future__ import annotations

import json
import unittest

from spindle import vault as vault_mod


class _Boto:
    """A stand-in for `boto3.client("secretsmanager")`, recording what it was asked.

    Only the four calls this module makes are implemented. A fake that accepted
    everything would let a typo'd API call pass, which is the failure mode a fake is
    supposed to make impossible rather than hide.
    """

    class ResourceNotFoundException(Exception):
        pass

    class ResourceExistsException(Exception):
        pass

    def __init__(self) -> None:
        self.stored: dict[str, str] = {}
        self.deleted: list[str] = []
        self.exceptions = self

    def _arn(self, name: str) -> str:
        return f"arn:aws:secretsmanager:us-east-1:000000000000:secret:{name}"

    def create_secret(self, *, Name: str, SecretString: str):
        if Name in self.stored:
            raise self.ResourceExistsException(Name)
        self.stored[Name] = SecretString
        return {"ARN": self._arn(Name), "Name": Name}

    def put_secret_value(self, *, SecretId: str, SecretString: str):
        name = SecretId.rsplit(":", 1)[-1] if SecretId.startswith("arn:") else SecretId
        self.stored[name] = SecretString
        return {"ARN": self._arn(name), "Name": name}

    def get_secret_value(self, *, SecretId: str):
        name = SecretId.rsplit(":", 1)[-1] if SecretId.startswith("arn:") else SecretId
        if name not in self.stored:
            raise self.ResourceNotFoundException(name)
        return {"ARN": self._arn(name), "SecretString": self.stored[name]}

    def delete_secret(self, *, SecretId: str, ForceDeleteWithoutRecovery: bool):
        name = SecretId.rsplit(":", 1)[-1] if SecretId.startswith("arn:") else SecretId
        self.stored.pop(name, None)
        self.deleted.append(name)


TENANT = "7b1c9d2e-0000-4000-8000-00000000abcd"


class TheNameIsTheTenantBoundary(unittest.TestCase):
    """The IAM policy is written against a path prefix, so the path is a security
    control and not a formatting preference. These tests pin its shape."""

    def test_the_name_carries_the_environment_and_the_tenant(self) -> None:
        self.assertEqual(vault_mod.secret_name("prod", TENANT),
                         f"spindle/prod/mailbox/{TENANT}")

    def test_two_tenants_never_collide(self) -> None:
        other = "7b1c9d2e-0000-4000-8000-00000000beef"
        self.assertNotEqual(vault_mod.secret_name("prod", TENANT),
                            vault_mod.secret_name("prod", other))

    def test_two_environments_never_collide(self) -> None:
        """A staging deployment must not be able to read production's mailbox tokens by
        holding the same tenant id — which it does, because staging is usually restored
        from a production backup."""
        self.assertNotEqual(vault_mod.secret_name("prod", TENANT),
                            vault_mod.secret_name("staging", TENANT))

    def test_a_blank_environment_is_refused_rather_than_producing_a_loose_path(self) -> None:
        """`spindle//mailbox/<id>` would sit outside the IAM prefix and still look
        plausible in a log. Refuse it where it is constructed."""
        with self.assertRaises(ValueError):
            vault_mod.secret_name("", TENANT)

    def test_a_blank_tenant_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            vault_mod.secret_name("prod", "")


class ACredentialRoundTrips(unittest.TestCase):

    def setUp(self) -> None:
        self.boto = _Boto()
        self.vault = vault_mod.SecretsManagerVault(env="prod", client=self.boto)

    def test_what_goes_in_comes_back_out(self) -> None:
        arn = self.vault.put(TENANT, {"refresh_token": "1//abc", "kind": "gmail"})
        self.assertEqual(self.vault.get(arn),
                         {"refresh_token": "1//abc", "kind": "gmail"})

    def test_put_returns_an_arn_that_names_the_scoped_path(self) -> None:
        arn = self.vault.put(TENANT, {"refresh_token": "1//abc"})
        self.assertIn(f"spindle/prod/mailbox/{TENANT}", arn)

    def test_reconnecting_replaces_rather_than_failing(self) -> None:
        """A tenant who disconnects and reconnects, or re-runs consent after revoking,
        must not hit `ResourceExistsException`. The second put wins."""
        self.vault.put(TENANT, {"refresh_token": "old"})
        arn = self.vault.put(TENANT, {"refresh_token": "new"})
        self.assertEqual(self.vault.get(arn), {"refresh_token": "new"})

    def test_the_stored_form_is_json_and_not_a_repr(self) -> None:
        """Pinned because `str(dict)` round-trips through `ast.literal_eval` and would
        appear to work, then break the first time a password contains a quote."""
        self.vault.put(TENANT, {"password": "he said \"hi\""})
        raw = self.boto.stored[f"spindle/prod/mailbox/{TENANT}"]
        self.assertEqual(json.loads(raw), {"password": "he said \"hi\""})

    def test_delete_removes_it(self) -> None:
        arn = self.vault.put(TENANT, {"refresh_token": "abc"})
        self.vault.delete(arn)
        with self.assertRaises(vault_mod.SecretMissing):
            self.vault.get(arn)


class AbsenceIsLoud(unittest.TestCase):
    """`no-fallbacks`, applied to the one module where a silent default would mean
    sending mail as nobody, or worse, as the previous tenant."""

    def setUp(self) -> None:
        self.boto = _Boto()
        self.vault = vault_mod.SecretsManagerVault(env="prod", client=self.boto)

    def test_a_missing_secret_raises_rather_than_returning_none(self) -> None:
        with self.assertRaises(vault_mod.SecretMissing):
            self.vault.get("arn:aws:secretsmanager:us-east-1:0:secret:spindle/prod/mailbox/nope")

    def test_the_refusal_names_what_was_missing(self) -> None:
        """An operator reading this in CloudWatch needs to know which tenant lost its
        credential, or the message is just noise."""
        with self.assertRaises(vault_mod.SecretMissing) as caught:
            self.vault.get(f"arn:aws:secretsmanager:us-east-1:0:secret:spindle/prod/mailbox/{TENANT}")
        self.assertIn(TENANT, str(caught.exception))


class TheCredentialNeverReachesAnErrorMessage(unittest.TestCase):
    """The realistic leak is not a database dump — it is a traceback in CloudWatch with
    a refresh token in the frame. Everything that can raise while holding the payload is
    checked here."""

    def test_a_provider_failure_during_put_does_not_quote_the_payload(self) -> None:
        class Exploding(_Boto):
            def create_secret(self, *, Name: str, SecretString: str):
                raise RuntimeError("AccessDeniedException: not authorised")

        vault = vault_mod.SecretsManagerVault(env="prod", client=Exploding())
        with self.assertRaises(Exception) as caught:
            vault.put(TENANT, {"refresh_token": "1//SUPERSECRET"})
        self.assertNotIn("SUPERSECRET", str(caught.exception))

    def test_the_vault_repr_holds_no_payload(self) -> None:
        vault = vault_mod.SecretsManagerVault(env="prod", client=_Boto())
        vault.put(TENANT, {"refresh_token": "1//SUPERSECRET"})
        self.assertNotIn("SUPERSECRET", repr(vault))


class LoadRefusesRatherThanReturningANoOp(unittest.TestCase):
    """The same argument `mail.load()` makes: a no-op vault would let a tenant click
    connect, see success, and have stored nothing."""

    def test_an_unset_environment_name_refuses(self) -> None:
        with self.assertRaises(vault_mod.SecretsUnconfigured):
            vault_mod.load(env="", region="us-east-1")

    def test_an_unset_region_refuses(self) -> None:
        with self.assertRaises(vault_mod.SecretsUnconfigured):
            vault_mod.load(env="prod", region="")


if __name__ == "__main__":
    unittest.main()
