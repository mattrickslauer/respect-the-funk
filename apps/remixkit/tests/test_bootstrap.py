"""Where the secrets come from, and why `app/.env` has to be able to say so.

The failure this guards against is specific and was easy to hit: `app/.env` sets
`RK_STORAGE_BACKEND=b2`, which makes B2 credentials mandatory — but the variable naming
where those credentials live could only be read from the process environment. So the
app read half its configuration from the file and refused to start over the half it
could not, and the error talked about missing B2 keys rather than the missing setting
that would have found them.

`source .env` does not close that gap: a plain `KEY=value` line sets a shell variable,
not an exported one, so a child process never sees it.
"""

from __future__ import annotations

import pytest

from remixkit import bootstrap
from remixkit.settings import Settings


class FakeSSM:
    """Stands in for the SSM client, and records how it was constructed."""

    def __init__(self, parameters: list[tuple[str, str]]) -> None:
        self._parameters = parameters
        self.paths: list[str] = []

    def get_paginator(self, _name):
        outer = self

        class _Paginator:
            def paginate(self, *, Path, Recursive, WithDecryption):  # noqa: N803
                outer.paths.append(Path)
                return [{"Parameters": [{"Name": f"{Path}/{n}", "Value": v} for n, v in outer._parameters]}]

        return _Paginator()


@pytest.fixture
def fake_boto(monkeypatch):
    """Patch boto3 at the module boundary so no network or credentials are involved."""
    import types

    created: dict[str, object] = {}
    ssm = FakeSSM([("B2_KEY_ID", "kid"), ("B2_APP_KEY", "app"), ("GMI_API_KEY", "gmi")])

    class _Session:
        def __init__(self, profile_name=None):
            created["profile"] = profile_name

        def client(self, name, region_name=None):
            created["region"] = region_name
            return ssm

    monkeypatch.setitem(
        __import__("sys").modules, "boto3", types.SimpleNamespace(Session=_Session)
    )
    return created, ssm


@pytest.fixture(autouse=True)
def _no_ambient(monkeypatch):
    # Empty rather than deleted for the RK_ names: deleting hands the field back to
    # `app/.env`, which is the very leak conftest closes. An empty environment variable
    # still outranks the file.
    for name in ("RK_SSM_PATH", "RK_AWS_PROFILE"):
        monkeypatch.setenv(name, "")
    for name in ("AWS_PROFILE", "AWS_REGION", "RK_AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    for name in ("RK_B2_KEY_ID", "B2_KEY_ID", "RK_B2_APP_KEY", "B2_APP_KEY", "GMI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------ the setting
def test_ssm_path_is_a_setting_so_the_env_file_can_name_it():
    """The whole point: `app/.env` can say where the secrets live."""
    settings = Settings(_env_file=None, ssm_path="/remixkit/prod", aws_profile="respect-the-funk")
    assert settings.ssm_path == "/remixkit/prod"
    assert settings.aws_profile == "respect-the-funk"


def test_it_defaults_to_empty_so_a_fresh_checkout_needs_nothing():
    settings = Settings(_env_file=None)
    assert settings.ssm_path == "" and settings.aws_profile == ""


# ------------------------------------------------------------------ loading
def test_an_explicit_path_is_used_even_with_no_environment_variable(fake_boto):
    _, ssm = fake_boto
    assert bootstrap.load_secrets("/remixkit/prod") == 3
    assert ssm.paths == ["/remixkit/prod"]


def test_no_path_anywhere_is_a_no_op(fake_boto):
    """The local-development story: nothing to configure, nothing to stub."""
    assert bootstrap.load_secrets() == 0


def test_the_environment_still_wins_when_no_path_is_passed(monkeypatch, fake_boto):
    """Unchanged for the deployment, where Terraform sets RK_SSM_PATH and there is no
    .env at all."""
    _, ssm = fake_boto
    monkeypatch.setenv("RK_SSM_PATH", "/remixkit/staging")
    assert bootstrap.load_secrets() == 3
    assert ssm.paths == ["/remixkit/staging"]


def test_the_profile_is_passed_to_the_session(fake_boto):
    created, _ = fake_boto
    bootstrap.load_secrets("/remixkit/prod", profile="respect-the-funk")
    assert created["profile"] == "respect-the-funk"


def test_no_profile_means_boto_resolves_its_own(fake_boto):
    """Right on Lambda — an execution role has no named profile."""
    created, _ = fake_boto
    bootstrap.load_secrets("/remixkit/prod")
    assert created["profile"] is None


def test_values_land_prefixed_and_provider_keys_land_unprefixed(monkeypatch, fake_boto):
    import os

    bootstrap.load_secrets("/remixkit/prod")
    assert os.environ["RK_B2_KEY_ID"] == "kid"
    # Genblaze reads this one off the environment itself, so it must also be unprefixed.
    assert os.environ["GMI_API_KEY"] == "gmi"


def test_a_placeholder_is_skipped_rather_than_loaded(monkeypatch):
    """Terraform seeds every parameter; an unset one must not reach a provider."""
    import sys
    import types

    ssm = FakeSSM([("GMI_API_KEY", "PLACEHOLDER — set with `aws ssm put-parameter`")])
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(Session=lambda profile_name=None: types.SimpleNamespace(
            client=lambda name, region_name=None: ssm
        )),
    )
    assert bootstrap.load_secrets("/remixkit/prod") == 0


# ------------------------------------------------------------------ hermeticity
def test_the_suite_cannot_reach_ssm_through_a_developers_env_file():
    """`get_settings()` reads `app/.env`, and `create_app()` uses `ssm_path` from it to
    fetch secrets. Without `_hermetic_env` neutralising that, every test on a machine
    with a real `.env` would call AWS — which is slow, needs credentials, and fails
    offline. This asserts the autouse fixture actually closes it."""
    from remixkit.settings import Settings as S

    assert S().ssm_path == "", "a developer's .env must not reach SSM from the suite"
