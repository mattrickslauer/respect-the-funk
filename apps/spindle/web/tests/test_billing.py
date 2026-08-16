"""Stripe: configuration that refuses loudly, and a webhook that refuses a forgery.

    python -m unittest discover apps/spindle/web/tests

Entirely offline, and that is the design rather than a limitation of the suite. The two
things worth testing here are *what happens when Stripe is absent* and *what happens when
somebody who is not Stripe posts to the webhook*, and neither needs a network — the first
is settings, and the second is HMAC over bytes, implemented in `billing.py` precisely so
it can be exercised exhaustively with no vendor, no package and no cluster.

What is **not** tested here, stated so the gap is on the record rather than discovered:
no Checkout Session has ever been created by this code. `checkout_session` is exercised
up to the point where it would call Stripe and no further, because doing more would need
a real test-mode key and would be asserting Stripe's behaviour rather than ours. What
that leaves unproven is named in the report: the shape of the `create` call itself.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import time
import unittest

from spindle import billing, plans, settings as settings_mod

SECRET = "whsec_test_secret_value"


def _settings(**overrides) -> settings_mod.Settings:
    """A Settings built explicitly, never from the developer's shell.

    Every field is named. A test that read the ambient environment would pass or fail
    depending on whose laptop it ran on, which is the failure `conftest.py`'s whole
    argument is about one level up.
    """
    base = dict(
        database_url="", masters_bucket="",
        region="us-east-1", classifier_function="", mail_sender="", mail_reply_to="",
        mail_postal_address="", cockroach_api_key="", cockroach_cluster_id="",
        mcp_url="", openai_api_key="",
        stripe_secret_key="", stripe_webhook_secret="",
        stripe_price_label="", stripe_price_roster="",
        stripe_price_label_live="", stripe_price_roster_live="",
    )
    base.update(overrides)
    return settings_mod.Settings(**base)


def _configured(**overrides) -> settings_mod.Settings:
    """Fully configured, in test mode. `overrides` wins, so a test can blank one value.

    The `_live` price ids are deliberately left empty here rather than filled with
    plausible strings. A test-mode deployment has no use for them, and a helper that
    populated both pairs would make `stripe_missing`'s mode-awareness untestable — every
    assertion would pass whether or not the property looked at the mode at all.
    """
    configured = {"stripe_secret_key": "sk_test_abc123",
                  "stripe_webhook_secret": SECRET,
                  "stripe_price_label": "price_test_label",
                  "stripe_price_roster": "price_test_roster"}
    configured.update(overrides)
    return _settings(**configured)


def _configured_live(**overrides) -> settings_mod.Settings:
    """Fully configured against the live ledger, with the test price ids left empty.

    The mirror image of `_configured`, and the pair of them is what pins the rule: which
    price ids a deployment needs is decided by its key, not by which variables somebody
    happened to set.
    """
    configured = {"stripe_secret_key": "sk_live_abc123",
                  "stripe_webhook_secret": SECRET,
                  "stripe_price_label_live": "price_live_label",
                  "stripe_price_roster_live": "price_live_roster"}
    configured.update(overrides)
    return _settings(**configured)


class TheConfigurationIsNamedNotGuessed(unittest.TestCase):
    """The `mcp_configured` / `mcp_missing` pattern, applied to billing. "Not configured"
    is not an actionable message; the names of the four variables are."""

    def test_nothing_configured_names_all_four(self):
        settings = _settings()
        self.assertFalse(settings.stripe_configured)
        self.assertEqual(
            ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
             "STRIPE_PRICE_LABEL", "STRIPE_PRICE_ROSTER"],
            settings.stripe_missing)

    def test_one_missing_variable_is_named_alone(self):
        settings = _configured(stripe_price_roster="")
        self.assertFalse(settings.stripe_configured)
        self.assertEqual(["STRIPE_PRICE_ROSTER"], settings.stripe_missing)

    def test_there_is_no_partial_mode(self):
        """A key with no webhook secret could send somebody to a checkout page and never
        hear that they paid. All four or nothing."""
        self.assertFalse(_configured(stripe_webhook_secret="").stripe_configured)

    def test_everything_set_is_configured(self):
        self.assertTrue(_configured().stripe_configured)
        self.assertEqual([], _configured().stripe_missing)

    def test_the_mode_is_read_off_the_key_rather_than_a_second_variable(self):
        self.assertEqual("test", _configured().stripe_mode)
        self.assertTrue(_configured().stripe_test_mode)
        self.assertEqual("live", _configured(stripe_secret_key="sk_live_x").stripe_mode)
        self.assertFalse(_configured(stripe_secret_key="sk_live_x").stripe_test_mode)

    def test_an_unrecognised_key_is_not_assumed_to_be_test(self):
        """The safe-looking guess is "test", and guessing test about a credential that
        turns out to be live is how a UI reassures somebody while taking their money."""
        settings = _configured(stripe_secret_key="rk_something_else")
        self.assertEqual("unconfigured", settings.stripe_mode)
        self.assertFalse(settings.stripe_test_mode)

    def test_no_fake_key_is_defaulted_in(self):
        """Read through `load()` with the environment cleared: every Stripe variable must
        come back empty rather than as a plausible placeholder."""
        import os
        from unittest import mock

        cleared = {k: "" for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                                   "STRIPE_PRICE_LABEL", "STRIPE_PRICE_ROSTER",
                                   "STRIPE_PRICE_LABEL_LIVE",
                                   "STRIPE_PRICE_ROSTER_LIVE")}
        with mock.patch.dict(os.environ, cleared, clear=False):
            for name in cleared:
                os.environ.pop(name, None)
            loaded = settings_mod.load()
        self.assertEqual("", loaded.stripe_secret_key)
        self.assertEqual("", loaded.stripe_webhook_secret)
        self.assertEqual("", loaded.stripe_price_label)
        self.assertEqual("", loaded.stripe_price_roster)
        self.assertEqual("", loaded.stripe_price_label_live)
        self.assertEqual("", loaded.stripe_price_roster_live)


class TheModeDecidesWhichPriceIdsAreRequired(unittest.TestCase):
    """Added 2026-08-15, when `billing.py` stopped refusing live keys.

    A Stripe price id belongs to exactly one ledger, so "which four variables does this
    deployment need" stopped being a constant and became a function of the key. These
    pin that, because the failure they prevent is expensive and quiet: a live deployment
    reading a test-mode price id charges nothing and 404s at Stripe, and the same pair
    swapped the other way charges a real card while the UI says test mode.
    """

    def test_a_test_deployment_does_not_need_the_live_price_ids(self):
        settings = _configured()
        self.assertTrue(settings.stripe_configured)
        self.assertEqual([], settings.stripe_missing)

    def test_a_live_deployment_does_not_need_the_test_price_ids(self):
        settings = _configured_live()
        self.assertTrue(settings.stripe_configured)
        self.assertEqual([], settings.stripe_missing)

    def test_a_live_key_with_only_test_prices_names_the_live_variables(self):
        """The promotion somebody half-finished: key swapped, price ids not."""
        settings = _configured(stripe_secret_key="sk_live_abc123")
        self.assertFalse(settings.stripe_configured)
        self.assertEqual(["STRIPE_PRICE_LABEL_LIVE", "STRIPE_PRICE_ROSTER_LIVE"],
                         settings.stripe_missing)

    def test_a_test_key_with_only_live_prices_names_the_test_variables(self):
        settings = _configured_live(stripe_secret_key="sk_test_abc123")
        self.assertFalse(settings.stripe_configured)
        self.assertEqual(["STRIPE_PRICE_LABEL", "STRIPE_PRICE_ROSTER"],
                         settings.stripe_missing)

    def test_an_unrecognised_key_is_never_configured_however_much_else_is_set(self):
        """With no mode there is no price pair to require, so `stripe_missing` comes back
        naming nothing. Without the explicit mode check `stripe_configured` would then
        answer True for a deployment that cannot transact at all."""
        settings = _settings(stripe_secret_key="pk_live_publishable_by_mistake",
                             stripe_webhook_secret=SECRET,
                             stripe_price_label="price_test_label",
                             stripe_price_roster="price_test_roster",
                             stripe_price_label_live="price_live_label",
                             stripe_price_roster_live="price_live_roster")
        self.assertEqual("unconfigured", settings.stripe_mode)
        self.assertEqual([], settings.stripe_missing)
        self.assertFalse(settings.stripe_configured)

    def test_the_suffix_convention_is_the_one_plans_spells(self):
        """`settings._STRIPE_PRICE_VARS` and `plans.Tier.price_setting` both encode the
        `_LIVE` suffix. Two spellings of one convention; this is what keeps them equal."""
        for mode in ("test", "live"):
            declared = {attr for _, attr in
                        settings_mod.Settings._STRIPE_PRICE_VARS[mode]}
            from_tiers = {t.price_setting(mode)
                          for t in plans.TIERS if t.purchasable}
            self.assertEqual(declared, from_tiers, f"mode={mode}")

    def test_an_unknown_mode_raises_rather_than_defaulting_to_test(self):
        with self.assertRaises(ValueError):
            plans.tier("label").price_setting("unconfigured")

    def test_a_tier_with_nothing_to_sell_has_no_setting_in_either_mode(self):
        for mode in ("test", "live"):
            self.assertEqual("", plans.tier("free").price_setting(mode))
            self.assertEqual("", plans.tier("catalogue").price_setting(mode))


class CheckoutRefusesRatherThanPretending(unittest.TestCase):

    def test_an_unconfigured_deployment_names_the_variables(self):
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.checkout_session(_settings(), tenant_id="t", email="a@b.com",
                                     plan_key="label", success_url="/", cancel_url="/")
        self.assertEqual("stripe_not_configured", caught.exception.reason)
        for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                     "STRIPE_PRICE_LABEL", "STRIPE_PRICE_ROSTER"):
            self.assertIn(name, str(caught.exception))

    def test_a_live_key_is_no_longer_refused_for_being_live(self):
        """Until 2026-08-15 this raised `stripe_not_test_mode`. It does not any more, and
        the change is deliberate — see billing.py's "Both modes" section.

        A fully configured live deployment gets past `require_configured` and resolves a
        live price id. It is not driven further here for the same reason no test drives
        `checkout_session` to completion: doing so would call Stripe and assert Stripe's
        behaviour rather than ours.
        """
        settings = _configured_live()
        billing.require_configured(settings)          # must not raise
        self.assertEqual("live", settings.stripe_mode)
        self.assertFalse(settings.stripe_test_mode)
        self.assertEqual("price_live_label",
                         billing.price_id_for(plans.tier("label"), settings))

    def test_a_live_key_missing_its_live_prices_is_refused_by_name(self):
        """The guard that replaced the blanket refusal. Swapping the key without
        swapping the price ids is the realistic way a promotion goes wrong."""
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.checkout_session(_configured(stripe_secret_key="sk_live_real"),
                                     tenant_id="t", email="a@b.com", plan_key="label",
                                     success_url="/", cancel_url="/")
        self.assertEqual("stripe_not_configured", caught.exception.reason)
        self.assertIn("STRIPE_PRICE_LABEL_LIVE", str(caught.exception))

    def test_a_key_that_is_neither_prefix_is_refused_before_anything_else(self):
        """Checked first, and reported as its own reason. `stripe_missing` cannot help
        here — with no mode it names nothing — so an operator who pasted a truncated key
        would otherwise be told billing was unconfigured without being told what to fix.
        """
        settings = _configured(stripe_secret_key="sk_tset_typo")
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.checkout_session(settings, tenant_id="t", email="a@b.com",
                                     plan_key="label", success_url="/", cancel_url="/")
        self.assertEqual("stripe_key_unrecognised", caught.exception.reason)
        self.assertIn("sk_test_", str(caught.exception))
        self.assertIn("sk_live_", str(caught.exception))

    def test_the_free_tier_cannot_be_bought(self):
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.checkout_session(_configured(), tenant_id="t", email="a@b.com",
                                     plan_key="free", success_url="/", cancel_url="/")
        self.assertEqual("tier_not_purchasable", caught.exception.reason)

    def test_an_unknown_plan_is_refused_with_the_known_set(self):
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.checkout_session(_configured(), tenant_id="t", email="a@b.com",
                                     plan_key="enterprise", success_url="/",
                                     cancel_url="/")
        self.assertEqual("unknown_plan", caught.exception.reason)
        self.assertIn("roster", str(caught.exception))

    def test_a_tier_with_no_price_id_is_refused_by_name(self):
        settings = _configured(stripe_price_roster="")
        # `stripe_configured` is false with a price missing, so the first refusal names
        # the variable. That is the behaviour worth pinning: the operator is told
        # STRIPE_PRICE_ROSTER, not "checkout failed".
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.checkout_session(settings, tenant_id="t", email="a@b.com",
                                     plan_key="roster", success_url="/", cancel_url="/")
        self.assertIn("STRIPE_PRICE_ROSTER", str(caught.exception))

    def test_price_lookup_reads_the_setting_the_tier_names(self):
        """`plans.Tier.price_setting` holds the *name* of a settings attribute, so a price
        id is never compiled into the source and differs between test and live."""
        settings = _configured()
        self.assertEqual("price_test_label",
                         billing.price_id_for(plans.tier("label"), settings))
        self.assertEqual("price_test_roster",
                         billing.price_id_for(plans.tier("roster"), settings))

    def test_price_lookup_follows_the_key_and_not_whatever_is_set(self):
        """The one that matters. Both pairs are populated, so the only thing choosing
        between them is the key's prefix — which is the property that makes a `.env`
        holding test and live ids at once safe to keep."""
        both = _settings(stripe_webhook_secret=SECRET,
                         stripe_price_label="price_test_label",
                         stripe_price_roster="price_test_roster",
                         stripe_price_label_live="price_live_label",
                         stripe_price_roster_live="price_live_roster")
        as_test = dataclasses.replace(both, stripe_secret_key="sk_test_abc123")
        as_live = dataclasses.replace(both, stripe_secret_key="sk_live_abc123")
        self.assertEqual("price_test_label",
                         billing.price_id_for(plans.tier("label"), as_test))
        self.assertEqual("price_live_label",
                         billing.price_id_for(plans.tier("label"), as_live))
        self.assertEqual("price_test_roster",
                         billing.price_id_for(plans.tier("roster"), as_test))
        self.assertEqual("price_live_roster",
                         billing.price_id_for(plans.tier("roster"), as_live))


def _sign(payload: bytes, secret: str = SECRET, *, timestamp: int | None = None) -> str:
    """A genuine `Stripe-Signature` header, built the way Stripe builds one."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


class TheWebhookRefusesABadSignature(unittest.TestCase):
    """The endpoint is public. The signature is the only thing standing between it and a
    stranger granting themselves a paid plan, so every way it can be wrong is tested."""

    PAYLOAD = b'{"type":"checkout.session.completed","data":{"object":{}}}'

    def test_a_good_signature_passes(self):
        billing.verify_signature(self.PAYLOAD, _sign(self.PAYLOAD), SECRET)

    def test_a_wrong_secret_is_refused(self):
        header = _sign(self.PAYLOAD, "whsec_somebody_elses_secret")
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.verify_signature(self.PAYLOAD, header, SECRET)
        self.assertEqual("signature_invalid", caught.exception.reason)

    def test_a_tampered_body_is_refused(self):
        """The signature covers the bytes. Changing the tenant after signing must fail —
        this is the attack the check exists for."""
        header = _sign(self.PAYLOAD)
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.verify_signature(self.PAYLOAD + b" ", header, SECRET)
        self.assertEqual("signature_invalid", caught.exception.reason)

    def test_a_missing_header_is_refused(self):
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.verify_signature(self.PAYLOAD, None, SECRET)
        self.assertEqual("signature_missing", caught.exception.reason)

    def test_an_unset_secret_refuses_everything_including_a_valid_signature(self):
        """The most important line in the module. A deployment that forgot
        `STRIPE_WEBHOOK_SECRET` must not have an endpoint that grants plans to whoever
        posts to it — so "no secret" cannot mean "skip the check"."""
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.verify_signature(self.PAYLOAD, _sign(self.PAYLOAD), "")
        self.assertEqual("webhook_secret_missing", caught.exception.reason)

    def test_a_stale_delivery_is_refused(self):
        """A signature over a fixed body never expires on its own. The timestamp is what
        makes a captured delivery unreplayable."""
        header = _sign(self.PAYLOAD, timestamp=int(time.time()) - 3600)
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.verify_signature(self.PAYLOAD, header, SECRET)
        self.assertEqual("signature_stale", caught.exception.reason)

    def test_a_future_timestamp_is_refused_too(self):
        """Symmetric, because an attacker who can choose the timestamp would otherwise
        choose one far enough ahead to keep a capture valid indefinitely."""
        header = _sign(self.PAYLOAD, timestamp=int(time.time()) + 3600)
        with self.assertRaises(billing.BillingRefused):
            billing.verify_signature(self.PAYLOAD, header, SECRET)

    def test_a_malformed_header_is_refused_rather_than_partly_understood(self):
        for header in ("", "garbage", "t=abc,v1=deadbeef", "v1=deadbeef",
                       "t=123", "t=123,v0=deadbeef"):
            with self.subTest(header=header):
                with self.assertRaises(billing.BillingRefused):
                    billing.verify_signature(self.PAYLOAD, header, SECRET)

    def test_a_rotation_signature_with_two_v1_elements_passes(self):
        """During a secret rotation Stripe signs with both. Refusing the header because
        one of them does not match would take the webhook down for the length of the
        rotation."""
        ts = int(time.time())
        good = _sign(self.PAYLOAD, timestamp=ts).split("v1=")[1]
        header = f"t={ts},v1=00{good[2:]},v1={good}"
        billing.verify_signature(self.PAYLOAD, header, SECRET)

    def test_the_verified_bytes_are_the_raw_body(self):
        """Re-serialising a parsed body changes key order and whitespace and breaks a
        perfectly valid signature. This asserts the failure so nobody 'fixes' the handler
        by parsing first."""
        payload = b'{"b":1,"a":2}'
        header = _sign(payload)
        billing.verify_signature(payload, header, SECRET)
        reserialised = json.dumps(json.loads(payload)).encode()
        self.assertNotEqual(payload, reserialised)
        with self.assertRaises(billing.BillingRefused):
            billing.verify_signature(reserialised, header, SECRET)


class TheEventBody(unittest.TestCase):

    def test_a_non_json_body_is_refused(self):
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.parse_event(b"not json")
        self.assertEqual("body_malformed", caught.exception.reason)

    def test_a_json_array_is_refused(self):
        with self.assertRaises(billing.BillingRefused):
            billing.parse_event(b"[1,2,3]")

    def test_an_unrelated_event_is_acknowledged_and_ignored(self):
        """Not refused. A 4xx makes Stripe retry and eventually disable the endpoint, so
        refusing events nobody asked for would kill the ones that matter."""
        result = billing.handle_event(None, {"type": "invoice.paid"})
        self.assertFalse(result["handled"])
        self.assertEqual("ignored", result["reason"])
        self.assertEqual("invoice.paid", result["type"])

    def test_a_completed_checkout_with_no_tenant_is_loud(self):
        """Somebody paid and there is nobody to grant it to. That cannot be a shrug."""
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.handle_event(None, {
                "type": billing.CHECKOUT_COMPLETED,
                "data": {"object": {"metadata": {}}}})
        self.assertEqual("no_tenant_in_event", caught.exception.reason)

    def test_a_completed_checkout_naming_the_free_tier_grants_nothing(self):
        """The metadata is a string that travelled to Stripe and back. A valid signature
        proves it came from Stripe, not that it says something this build should act on."""
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.handle_event(None, {
                "type": billing.CHECKOUT_COMPLETED,
                "data": {"object": {"metadata": {"tenant_id": "t", "plan": "free"}}}})
        self.assertEqual("tier_not_purchasable", caught.exception.reason)

    def test_a_completed_checkout_naming_an_unknown_tier_grants_nothing(self):
        with self.assertRaises(billing.BillingRefused) as caught:
            billing.handle_event(None, {
                "type": billing.CHECKOUT_COMPLETED,
                "data": {"object": {"metadata": {"tenant_id": "t",
                                                 "plan": "enterprise"}}}})
        self.assertEqual("unknown_plan", caught.exception.reason)


class TheWebhookWritesTwoValuesAndEnforcesNothing(unittest.TestCase):
    """The design's central claim, checked rather than asserted in prose.

    `handle_event` is driven against a recording stand-in for `accounts.set_plan`. What is
    being proved is negative and structural: the only write is the plan and its ceiling.
    If a future edit made the webhook open threads, adjust a limit, or call a gate, this
    test would not notice — but the seam it pins is the one that keeps enforcement in
    `spend.Gate` and `outreach.open_thread`, where it worked before payments existed.
    """

    def setUp(self) -> None:
        self.calls: list[tuple] = []
        self._original = billing.accounts.set_plan

        def recording(conn, tenant_id, plan_key, **kw):
            self.calls.append((tenant_id, plan_key, kw))
            return plans.tier(plan_key)

        billing.accounts.set_plan = recording

    def tearDown(self) -> None:
        billing.accounts.set_plan = self._original

    def _event(self, **object_fields) -> dict:
        return {"type": billing.CHECKOUT_COMPLETED,
                "data": {"object": {"metadata": {"tenant_id": "tenant-1",
                                                 "plan": "label"},
                                    **object_fields}}}

    def test_it_writes_the_plan_and_the_ceiling_and_says_who_wrote_it(self):
        result = billing.handle_event(None, self._event(
            customer="cus_test", subscription="sub_test"))

        self.assertEqual(1, len(self.calls))
        tenant_id, plan_key, kw = self.calls[0]
        self.assertEqual("tenant-1", tenant_id)
        self.assertEqual("label", plan_key)
        self.assertEqual("stripe_webhook", kw["written_by"])
        self.assertEqual("cus_test", kw["stripe_customer_id"])
        self.assertEqual("sub_test", kw["stripe_subscription_id"])

        self.assertTrue(result["handled"])
        self.assertEqual("label", result["plan"])
        self.assertEqual(str(plans.tier("label").daily_spend_cap_usd),
                         result["daily_ceiling_usd"])

    def test_a_replayed_delivery_writes_the_same_thing(self):
        """Stripe delivers at least once. Idempotency here comes from the write being an
        assignment rather than an increment, which is why there is no dedup table."""
        event = self._event(customer="cus_test", subscription="sub_test")
        first = billing.handle_event(None, event)
        second = billing.handle_event(None, event)
        self.assertEqual(first, second)
        self.assertEqual(self.calls[0], self.calls[1])

    def test_client_reference_id_is_used_when_metadata_is_absent(self):
        billing.handle_event(None, {
            "type": billing.CHECKOUT_COMPLETED,
            "data": {"object": {"client_reference_id": "tenant-2",
                                "metadata": {"plan": "roster"}}}})
        self.assertEqual("tenant-2", self.calls[0][0])


class TheEndpointsThemselves(unittest.TestCase):
    """The two console routes, called directly.

    There is no HTTP client in this suite — `test_public_surface.py` renders templates
    through Jinja for the same reason — so the handlers are invoked as functions, which is
    what `test_api_endpoints.py` already does with the JSON API's actions. What that
    leaves untested is FastAPI's own wiring (form parsing, the dependency graph); the
    dependency graph has its own check in `test_api_surface.py`, and the route table is
    asserted below.

    The webhook is public. That is not an oversight and it is asserted here, next to the
    assertion that it refuses anything without a valid signature — those two facts are
    only safe together.
    """

    def _request(self, body: bytes, headers: dict[str, str]):
        """A real Starlette `Request` over a canned body, so `await request.body()` is
        exercised rather than stubbed. Byte-exactness of the body is the whole point of
        the signature check, so a fake that returned a string would test the wrong thing.
        """
        from starlette.requests import Request

        scope = {
            "type": "http", "method": "POST", "path": "/billing/webhook",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "query_string": b"", "root_path": "", "scheme": "https",
            "server": ("testserver", 443), "client": ("test", 1),
        }
        sent = {"done": False}

        async def receive():
            if sent["done"]:
                return {"type": "http.disconnect"}
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(scope, receive)

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_the_webhook_is_public_and_the_checkout_is_not(self):
        from fastapi.routing import APIRoute

        from spindle import routes

        gated = {}
        for route in routes.router.routes:
            if isinstance(route, APIRoute) and route.path in (
                    "/signin/code", "/billing/checkout", "/billing/webhook"):
                names, stack = set(), list(route.dependant.dependencies)
                while stack:
                    dep = stack.pop()
                    names.add(getattr(dep.call, "__name__", ""))
                    stack.extend(dep.dependencies)
                gated[route.path] = "require_signed_in" in names

        # `/signin/code` replaces `/claim` as the public, unauthenticated entry point —
        # it has to be public, because it is how somebody stops being anonymous — and it
        # carries its own bounds in `otp.py` rather than relying on a gate.
        self.assertEqual({"/signin/code": False, "/billing/checkout": True,
                          "/billing/webhook": False}, gated)

    def test_the_webhook_refuses_a_bad_signature_with_the_declared_code(self):
        """End to end through the handler: forged body in, 400 and a branchable code out,
        and — the part that matters — no database call on the way."""
        from spindle import routes
        from spindle.api import errors

        original_conn = routes._conn
        routes._conn = lambda: (_ for _ in ()).throw(
            AssertionError("the webhook reached the database before verifying"))
        original_settings = routes.SETTINGS
        routes.SETTINGS = _configured()
        try:
            request = self._request(
                b'{"type":"checkout.session.completed"}',
                {"stripe-signature": "t=1,v1=deadbeef"})
            response = self._run(routes.billing_webhook(request))
        finally:
            routes._conn = original_conn
            routes.SETTINGS = original_settings

        self.assertEqual(400, response.status_code)
        body = json.loads(response.body)
        self.assertEqual(errors.BILLING_SIGNATURE_INVALID, body["error"]["code"])
        # The envelope, identical to the JSON API's, mirrored sentence included.
        self.assertEqual(body["message"], body["error"]["message"])

    def test_the_webhook_refuses_everything_when_the_secret_is_unset(self):
        from spindle import routes

        original_settings = routes.SETTINGS
        routes.SETTINGS = _configured(stripe_webhook_secret="")
        payload = b'{"type":"checkout.session.completed"}'
        try:
            request = self._request(payload, {"stripe-signature": _sign(payload)})
            response = self._run(routes.billing_webhook(request))
        finally:
            routes.SETTINGS = original_settings
        self.assertEqual(400, response.status_code)
        self.assertIn("STRIPE_WEBHOOK_SECRET", json.loads(response.body)["message"])

    def test_checkout_refuses_a_principal_with_no_tenant(self):
        """There used to be a principal that legitimately reached this route with no
        tenant — the shared admin token, scoped to none by construction — and this route
        refused it with a 409 rather than attaching a real subscription to the wrong
        party.

        That principal no longer exists: every authenticated one carries its own tenant.
        So the guard moved into `_tenant_id`, where it is a 500 rather than a 409, and
        the difference in status code is the point. A tenantless principal here is not a
        situation a caller can be in and be told about; it is a route that was annotated
        `Principal` instead of `SignedIn`, which is a bug in this repository and must
        look like one in the logs.
        """
        from fastapi import HTTPException

        from spindle import auth, routes

        tenantless = auth.Principal(tenant_id=None, subject="nobody",
                                    authenticated=True)
        with self.assertRaises(HTTPException) as caught:
            routes.billing_checkout(None, tenantless, plan="label")
        self.assertEqual(500, caught.exception.status_code)
        self.assertIn("SignedIn", caught.exception.detail)

    def test_checkout_names_the_missing_variables_rather_than_pretending(self):
        from spindle import accounts, auth, routes

        tenant = auth.Principal(tenant_id="tenant-1", subject="a@b.com",
                                authenticated=True, plan="free")
        original_conn, original_get = routes._conn, accounts.get_account
        original_settings = routes.SETTINGS
        routes._conn = lambda: None
        accounts.get_account = lambda conn, tenant_id: {"email": "a@b.com"}
        routes.SETTINGS = _settings()             # no Stripe configuration at all
        try:
            response = routes.billing_checkout(
                self._request(b"", {"host": "testserver"}), tenant, plan="label")
        finally:
            routes._conn, accounts.get_account = original_conn, original_get
            routes.SETTINGS = original_settings

        self.assertEqual(503, response.status_code)
        message = json.loads(response.body)["message"]
        for name in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_LABEL"):
            self.assertIn(name, message)
        self.assertNotIn("url", json.loads(response.body))

    def test_a_successful_checkout_redirects_the_browser_to_stripe(self):
        """The bug this pins, found 2026-08-15: the handler returned the session as JSON
        and both callers are plain HTML forms with no JavaScript. The session was real
        and the browser rendered `{"id": ..., "url": ...}` as text instead of going to
        it. Nothing in the suite caught it because no test drove this to a success.

        `billing.checkout_session` is stubbed rather than called — reaching Stripe needs
        a key and a network, and what is under test is what this handler does with a
        session, not how Stripe builds one.
        """
        from spindle import accounts, auth, billing as billing_mod, routes

        tenant = auth.Principal(tenant_id="tenant-1", subject="a@b.com",
                                authenticated=True, plan="free")
        stripe_url = "https://checkout.stripe.com/c/pay/cs_test_deadbeef"

        original_conn, original_get = routes._conn, accounts.get_account
        original_settings, original_session = routes.SETTINGS, billing_mod.checkout_session
        routes._conn = lambda: None
        accounts.get_account = lambda conn, tenant_id: {"email": "a@b.com"}
        routes.SETTINGS = _configured()
        billing_mod.checkout_session = lambda *a, **k: {
            "id": "cs_test_deadbeef", "url": stripe_url,
            "plan": "label", "mode": "test", "test_mode": True}
        try:
            response = routes.billing_checkout(
                self._request(b"", {"host": "testserver"}), tenant, plan="label")
        finally:
            routes._conn, accounts.get_account = original_conn, original_get
            routes.SETTINGS, billing_mod.checkout_session = original_settings, original_session

        self.assertEqual(303, response.status_code)
        self.assertEqual(stripe_url, response.headers["location"])
        # And emphatically not the session as a body for a browser to display.
        self.assertNotIn(b"cs_test_deadbeef", bytes(getattr(response, "body", b"")))


class TheLandingPageOffersNoControlItCannotHonour(unittest.TestCase):
    """`GET /` renders the landing page only for an anonymous principal — a signed-in
    visitor gets the queue instead (`routes.home`). So every visitor who sees the pricing
    table is anonymous, and `POST /billing/checkout` is `SignedIn`.

    Until 2026-08-15 the paid tiers rendered a checkout form there anyway. It could not
    work for anyone who could see it: the post 303'd to `/`, which re-rendered the same
    page, so the button read as broken rather than as refused. It could not have worked
    even in principle — a Checkout Session needs a tenant and an email to bill, and an
    anonymous visitor supplies neither.
    """

    def _landing(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        return (root / "spindle/templates/landing.html").read_text(encoding="utf-8")

    def test_the_anonymous_branch_offers_sign_in_rather_than_checkout(self):
        source = self._landing()
        self.assertIn("Sign in to choose", source)

    def test_any_checkout_form_is_behind_an_authenticated_guard(self):
        """The form may still exist for a signed-in render; it must not be what an
        anonymous visitor gets."""
        source = self._landing()
        if 'action="/billing/checkout"' not in source:
            return                     # no form at all is also a correct answer here
        before = source[:source.index('action="/billing/checkout"')]
        self.assertIn("principal.authenticated", before[-500:],
                      "the landing page's checkout form is not guarded on being signed "
                      "in, and every visitor to that page is anonymous by construction")

    def test_home_serves_the_landing_page_only_when_signed_out(self):
        """The premise the two assertions above rest on. If `/` ever starts serving this
        template to a signed-in principal, the guard becomes load-bearing rather than
        defensive and this test should be the thing that says so."""
        from pathlib import Path

        from spindle import routes

        source = Path(routes.__file__).read_text(encoding="utf-8")
        home = source[source.index('@router.get("/", response_class=HTMLResponse)'):]
        self.assertIn("if not principal.authenticated:", home[:1200])


class NothingClaimsRevenue(unittest.TestCase):
    """`docs/submission/SUBMISSION.md`'s "what this submission does not claim" section is
    a standing rule, and billing code is exactly where it would be broken first — a
    comment saying "customers on the Label plan" costs nothing to write and is a lie in
    the repository.

    Grepped rather than reasoned about, because the failure mode is one careless sentence
    in a docstring six months from now.
    """

    #: Phrases that cannot be written honestly by this repository, whatever surrounds
    #: them. Deliberately short and unambiguous rather than a broad word list: "revenue
    #: nobody charged for" is a correct sentence in `plans.py` and a word-level check
    #: would have banned it, which teaches people to weaken the test rather than fix the
    #: prose.
    FORBIDDEN = ("our customers", "paying customers", "customers on the",
                 "monthly recurring revenue", "payments received",
                 "we have charged")

    def test_no_billing_module_claims_customers_or_revenue(self):
        from pathlib import Path

        for module in (billing, plans):
            source = Path(module.__file__).read_text(encoding="utf-8").lower()
            for phrase in self.FORBIDDEN:
                with self.subTest(module=Path(module.__file__).name, phrase=phrase):
                    self.assertNotIn(phrase, source)

    def test_the_disclaimers_are_present_rather_than_merely_the_claims_absent(self):
        """Absence is not the same as honesty. Both modules say, in words, that nothing
        has been billed — because a judge reading either one should not have to infer it
        from what is missing."""
        from pathlib import Path

        billing_source = Path(billing.__file__).read_text(encoding="utf-8").lower()
        plans_source = Path(plans.__file__).read_text(encoding="utf-8").lower()
        self.assertIn("no customer has been billed", billing_source)
        self.assertIn("no revenue exists", billing_source)
        self.assertIn("no claim that any of this has been paid", plans_source)


class TheTemplatesDoNotAssertWhatALiveDeploymentFalsifies(unittest.TestCase):
    """Added 2026-08-15 with live-key support.

    Two user-facing sentences — "No customer is paying for this today" on the landing
    page and "Nothing on this page has ever been billed" in the console — were rendered
    unconditionally. They were safe while a live key was refused outright and became
    claims the deployment itself could falsify the moment it was not.

    The failure this guards is quiet in a specific way worth naming. The guard is
    `{% if stripe_mode != 'live' %}`, and if `stripe_mode` ever stopped being injected
    into the template context, Jinja would compare `Undefined != 'live'`, get `True`, and
    render both sentences again — on a live deployment, under a button that charges a
    card, with no error anywhere. So this checks the guard *and* the variable feeding it.
    """

    LANDING = "spindle/templates/landing.html"
    ACCOUNT = "spindle/templates/console/account.html"

    #: (template, sentence). Each must sit behind a live-mode guard.
    GUARDED = (
        (LANDING, "No customer is paying for this today"),
        (ACCOUNT, "Nothing on this page has ever been billed"),
    )

    GUARD = "stripe_mode != 'live'"

    def _read(self, relative: str) -> str:
        from pathlib import Path

        # tests/ sits beside spindle/, so the package root is one level up.
        root = Path(__file__).resolve().parent.parent
        return (root / relative).read_text(encoding="utf-8")

    def test_each_claim_sits_behind_a_live_mode_guard(self):
        for relative, sentence in self.GUARDED:
            with self.subTest(template=relative):
                source = self._read(relative)
                self.assertIn(sentence, source,
                              "the sentence moved; re-point this test rather than "
                              "deleting it")
                before = source[:source.index(sentence)]
                self.assertIn(
                    self.GUARD, before[-600:],
                    f"{sentence!r} in {relative} is not inside a {self.GUARD!r} block. "
                    f"A live deployment would render a claim it falsifies.")

    def test_live_mode_renders_a_badge_of_its_own(self):
        """`stripe_test_mode` is false for live *and* for unconfigured, so a template
        branching only on it leaves the loudest state as the silent one."""
        for relative in (self.LANDING, self.ACCOUNT):
            with self.subTest(template=relative):
                source = self._read(relative)
                self.assertIn("stripe_mode == 'live'", source)

    def test_the_context_actually_carries_stripe_mode(self):
        """Without this the guards above degrade to always-true and say so to nobody."""
        from spindle import routes

        source = self._read("spindle/routes.py")
        self.assertIn('"stripe_mode": SETTINGS.stripe_mode', source)
        # And the attribute the context reads must exist on Settings, so a rename of one
        # cannot leave the other pointing at nothing.
        self.assertTrue(hasattr(routes.SETTINGS, "stripe_mode"))

    def test_the_guard_renders_the_way_it_is_meant_to(self):
        """The logic itself, exercised rather than assumed. Jinja is templated by hand
        here because the surrounding pages need a route's worth of unrelated context."""
        from jinja2 import Environment, StrictUndefined

        env = Environment(undefined=StrictUndefined, autoescape=True)
        template = env.from_string(
            "{% if stripe_test_mode %}TEST"
            "{% elif stripe_mode == 'live' %}LIVE{% endif %}"
            "{% if stripe_mode != 'live' %}CLAIM{% endif %}")
        self.assertEqual(
            "TESTCLAIM",
            template.render(stripe_mode="test", stripe_test_mode=True))
        self.assertEqual(
            "LIVE",
            template.render(stripe_mode="live", stripe_test_mode=False))
        self.assertEqual(
            "CLAIM",
            template.render(stripe_mode="unconfigured", stripe_test_mode=False))
