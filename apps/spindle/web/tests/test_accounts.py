"""Per-tenant identity: the credential, the claim, and the plan meter.

    python -m unittest discover apps/spindle/web/tests

Two layers, the same split every other suite here uses and for the same reason.

  * **Offline** — hashing, email normalisation, the tier table, and above all the
    *precedence* in `auth.principal_from_cookie`. Those are properties of the code, and a
    fake resolver proves them more sharply than a cluster would.
  * **Against the cluster** — the claim, its bounds, and the plan meter. Those are
    database behaviours: a UNIQUE index refusing a second account for one address, a
    transaction leaving no half-made tenant, a count over `thread.created_at`. A fake
    that returned rows from a dict would prove only that the fake works.

The property this file exists to protect, above the others: **every authenticated
principal carries a real `tenant_id`, and there is no second way to become one.** That is
what the `SETTINGS.tenant_slug` deletions in `routes._tenant_id` and
`api/deps.current_tenant` rest on, and `TheAdminTokenIsGone` below holds each half of it.

This file used to say the opposite — that the shared `PLATFORM_ADMIN_TOKEN` still
authenticated and was still checked before anything touched the database. It was a real
guarantee and it was given up deliberately on 2026-08-15; `auth.py` records the trade.
"""

from __future__ import annotations

import os
import unittest
import uuid
from decimal import Decimal

from spindle import (
    accounts, auth, outreach, plans, settings as settings_mod,
)

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

ADMIN = "operator-token-for-tests"


class TheToken(unittest.TestCase):
    """Minting and hashing. No database: these are properties of the functions."""

    def test_a_token_is_not_guessable(self):
        """Two mints must differ, and by more than a counter. This does not prove
        randomness — nothing cheap does — it proves the obvious failure of a fixed or
        sequential token, which is what a refactor would introduce."""
        minted = {accounts.mint_token() for _ in range(100)}
        self.assertEqual(100, len(minted))

    def test_a_token_carries_the_entropy_the_hash_choice_depends_on(self):
        """`hash_token` uses a fast hash, and migration 033 argues that is correct
        *because* the input is 256 bits of CSPRNG output. If somebody shortens the token,
        that argument silently stops holding — so the length is asserted here, next to
        the reasoning, rather than left as a constant nobody rechecks."""
        self.assertEqual(32, accounts.TOKEN_BYTES)
        # base64url of 32 bytes, unpadded: 43 characters.
        self.assertGreaterEqual(len(accounts.mint_token()), 43)

    def test_the_hash_is_deterministic_and_hex(self):
        token = accounts.mint_token()
        self.assertEqual(accounts.hash_token(token), accounts.hash_token(token))
        digest = accounts.hash_token(token)
        self.assertEqual(64, len(digest))
        self.assertEqual(digest, digest.lower())
        int(digest, 16)                       # raises if it is not hex

    def test_the_hash_matches_the_constraint_the_schema_enforces(self):
        """Migration 033's `account_token_hash_is_sha256` CHECK is `^[0-9a-f]{64}$`. If
        `hash_token` ever changed algorithm, every insert would start failing at the
        database — which is the intended loud failure, and this test is where it is
        noticed before a deploy rather than after one."""
        import re

        pattern = re.compile(r"^[0-9a-f]{64}$")
        for _ in range(20):
            self.assertRegex(accounts.hash_token(accounts.mint_token()), pattern)

    def test_the_raw_token_never_equals_its_stored_form(self):
        """The whole point of the column. Trivially true, and asserted because the
        version of this code that stores the token directly also passes every other test
        in this file."""
        token = accounts.mint_token()
        self.assertNotEqual(token, accounts.hash_token(token))


class TheEmail(unittest.TestCase):

    def test_it_is_lowercased_and_stripped(self):
        self.assertEqual("steve@example.com",
                         accounts.normalise_email("  Steve@Example.COM "))

    def test_a_missing_address_is_refused_by_name(self):
        with self.assertRaises(accounts.ClaimRefused) as caught:
            accounts.normalise_email("   ")
        self.assertEqual("email_missing", caught.exception.reason)

    def test_nonsense_is_refused_rather_than_cleaned_up(self):
        """The no-fallbacks rule at its most ordinary. An address that cannot be parsed
        must not be stored as whatever survived parsing it."""
        for bad in ("steve", "steve@", "@example.com", "steve@example",
                    "steve @example.com", "a@b@c.com"):
            with self.subTest(bad=bad):
                with self.assertRaises(accounts.ClaimRefused) as caught:
                    accounts.normalise_email(bad)
                self.assertEqual("email_malformed", caught.exception.reason)

    def test_a_very_long_address_is_refused(self):
        with self.assertRaises(accounts.ClaimRefused) as caught:
            accounts.normalise_email("a" * 300 + "@example.com")
        self.assertEqual("email_too_long", caught.exception.reason)


class TheAdminTokenIsGone(unittest.TestCase):
    """The removal, held as tests rather than as an absence nobody notices.

    This class used to be `TheAdminTokenStillWorks`, and it asserted the opposite of
    every line below: that the shared `PLATFORM_ADMIN_TOKEN` authenticated, carried no
    tenant, and was compared *before* the database was touched so the operator could get
    in when the `account` table could not be read.

    That was a compatibility guarantee and it was deliberately given up on 2026-08-15 when
    email-OTP sign-in replaced both credentials. `auth.py` records what it cost. These
    tests exist so the path cannot come back quietly: a re-added admin branch would have
    to delete a test that says, in its name, that the branch is gone.
    """

    def test_there_is_no_admin_token_setting_left_to_read(self):
        """The whole path started with a setting. If this attribute returns, so has the
        credential, and every other test in this class is reachable again."""
        self.assertFalse(hasattr(settings_mod.load(), "admin_token"))

    def test_the_signature_no_longer_accepts_a_shared_secret(self):
        """`principal_from_cookie(token, admin_token, resolve)` was the old three-argument
        form, and forty-odd call sites passed the middle one. Passing anything there now
        is a TypeError rather than a silently ignored argument — which is what makes a
        half-finished revert fail loudly instead of authenticating nobody."""
        with self.assertRaises(TypeError):
            auth.principal_from_cookie("a-token", "an-admin-token", _resolver({}))

    def test_a_token_that_is_not_an_account_authenticates_nobody(self):
        """There is no second thing a cookie can be. Previously this string could have
        matched the shared secret; now the only question asked of it is whether it hashes
        to a row in `account`."""
        self.assertFalse(
            auth.principal_from_cookie("operator-token-for-tests",
                                       _resolver({})).authenticated)

    def test_no_principal_carries_the_operator_plan(self):
        """`OPERATOR_PLAN` existed so `plans.tier()` could not resolve the operator into a
        customer tier and quietly meter them. With no operator there is no such plan, and
        a stray reference should fail at import rather than resolve to something."""
        self.assertFalse(hasattr(auth, "OPERATOR_PLAN"))

    def test_there_is_no_is_operator_predicate(self):
        """It gated the paths that could see across tenants. Nothing may."""
        self.assertFalse(hasattr(auth.ANONYMOUS, "is_operator"))

    def test_an_authenticated_principal_always_carries_a_tenant(self):
        """The property every deletion in this change rests on.

        `routes._tenant_id` and `api/deps.current_tenant` both dropped their
        `SETTINGS.tenant_slug` fallback because the only principal that could reach it was
        the operator. That is only safe if authentication cannot produce a tenantless
        principal, so it is asserted here rather than assumed there.
        """
        principal = auth.principal_from_cookie(
            "tenant-token", _resolver({"tenant-token": TheTenantPrincipal.ROW}))
        self.assertTrue(principal.authenticated)
        self.assertTrue(principal.tenant_id)

    def test_a_resolver_failure_is_not_silently_anonymous(self):
        """Unchanged by any of the above, and more important than it was: with one
        credential there is no other way in, so swallowing a database error here would
        render as the console signing everybody out during an incident."""
        def explode(_token: str):
            raise RuntimeError("cluster unreachable")

        with self.assertRaises(RuntimeError):
            auth.principal_from_cookie("some-tenant-token", explode)


def _resolver(rows: dict[str, dict]):
    """A `principal_from_cookie` resolver backed by a dict, keyed by raw token."""
    return lambda token: rows.get(token)


class TheTenantPrincipal(unittest.TestCase):
    """Resolution of a per-tenant token, against a fake. The SQL has its own tests below."""

    ROW = {"tenant_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
           "email": "steve@example.com", "plan": "label",
           "tenant_slug": "steve-a1b2c3"}

    def test_a_known_token_resolves_to_its_tenant_and_plan(self):
        principal = auth.principal_from_cookie(
            "tenant-token", _resolver({"tenant-token": self.ROW}))
        self.assertTrue(principal.authenticated)
        self.assertEqual("11111111-1111-4111-8111-111111111111", principal.tenant_id)
        self.assertEqual("steve@example.com", principal.subject)
        self.assertEqual("label", principal.plan)
        self.assertEqual("steve-a1b2c3", principal.tenant_slug)

    def test_an_unknown_token_is_anonymous(self):
        principal = auth.principal_from_cookie(
            "not-a-token", _resolver({"tenant-token": self.ROW}))
        self.assertFalse(principal.authenticated)

    def test_an_empty_cookie_never_resolves(self):
        """Checked before the resolver, so no empty-string digest can ever match a row."""
        def explode(_token: str):
            raise AssertionError("the resolver was called for an empty cookie")

        self.assertFalse(auth.principal_from_cookie("", explode).authenticated)
        self.assertFalse(auth.principal_from_cookie(None, explode).authenticated)

    def test_a_missing_resolver_authenticates_nobody(self):
        """The footgun `auth.py` names. It used to be quiet — a caller that forgot the
        resolver still authenticated operators, so the console worked and only tenants
        were mysteriously anonymous. Now forgetting it authenticates nobody at all, which
        fails on the first request instead of looking like a working console."""
        self.assertFalse(auth.principal_from_cookie("tenant-token").authenticated)


class BothCompositionRootsPassTheResolver(unittest.TestCase):
    """`auth.py` names this as the footgun its design accepts: a caller that forgets the
    resolver gets an authentication path that silently never authenticates a tenant.

    The mitigation is that there are exactly two composition roots and both pass it. This
    is that mitigation, checked by *calling* them rather than by reading the source — a
    grep would pass on a line that had been commented out.
    """

    def test_the_console_resolves_tenant_tokens(self):
        from spindle import routes

        seen: list[str] = []
        original = routes._resolve_account
        routes._resolve_account = lambda token: seen.append(token) or None
        try:
            routes.current_principal("some-tenant-token")
        finally:
            routes._resolve_account = original
        self.assertEqual(["some-tenant-token"], seen)

    def test_the_json_api_resolves_tenant_tokens(self):
        from spindle.api import deps

        seen: list[str] = []
        original = deps._resolve_account
        deps._resolve_account = lambda token: seen.append(token) or None
        try:
            deps.current_principal("some-tenant-token")
        finally:
            deps._resolve_account = original
        self.assertEqual(["some-tenant-token"], seen)


class TheTierTable(unittest.TestCase):
    """`plans.py` is the single source of truth, and these are the properties that make
    that claim true rather than aspirational."""

    def test_the_four_tiers_are_the_ones_that_were_priced(self):
        self.assertEqual(["free", "label", "roster", "catalogue"],
                         [t.key for t in plans.TIERS])

    def test_the_numbers_are_the_ones_on_the_pricing_page(self):
        """Pinned so the pricing UI, which imports these, cannot be silently contradicted
        by an edit here. Changing a price is changing this test, on purpose."""
        free, label, roster, catalogue = plans.TIERS
        self.assertEqual((0, 5, 1, True), (free.price_usd_month, free.open_conversations,
                                           free.artists, free.sends_enabled))
        self.assertEqual((49, 50, 3), (label.price_usd_month, label.open_conversations,
                                       label.artists))
        self.assertEqual((199, 250, None), (roster.price_usd_month,
                                            roster.open_conversations, roster.artists))
        self.assertIsNone(catalogue.price_usd_month)

    def test_the_free_tier_may_send_but_not_from_its_own_address(self):
        """Replaces `test_the_free_tier_cannot_send`, and the replacement is a product
        decision rather than a relaxed assertion — so it is worth being explicit about
        what was traded.

        The old test guarded against sending being wired for free accounts *by accident*,
        back when nothing sent at all. Sending is wired now (`sender.py` drains the
        outbox), and the decision taken on 2026-08-16 is that a free tenant sends through
        the platform's SES identity: an outreach product a prospect cannot watch work is
        not a trial of anything.

        What actually holds the line has moved rather than disappeared, and this test now
        pins both halves. A free tenant may send (`sends_enabled`), and may **not** send
        from their own connected mailbox (`connects_mailbox`) — that is the paid boundary,
        and it is the one with a real per-tenant cost behind it, a stored credential in
        Secrets Manager. The volume ceiling is unchanged and asserted below and in
        `test_the_free_tier_has_a_small_money_ceiling`.
        """
        self.assertTrue(plans.FREE.sends_enabled)
        self.assertFalse(plans.FREE.connects_mailbox)
        self.assertEqual(plans.FREE.open_conversations, 5)

    def test_the_free_tier_has_a_small_money_ceiling(self):
        """`POST /claim` is public. This number is what stops an account that gets
        created from costing anything."""
        self.assertLessEqual(plans.FREE.daily_spend_cap_usd, Decimal("1.00"))
        self.assertGreater(plans.FREE.daily_spend_cap_usd, Decimal("0"))

    def test_only_the_two_metered_tiers_are_purchasable(self):
        self.assertEqual(["label", "roster"],
                         [t.key for t in plans.TIERS if t.purchasable])

    def test_an_unknown_plan_raises_rather_than_falling_back_to_free(self):
        """The tempting fallback is the most restrictive tier, which looks safe and would
        silently downgrade a paying tenant on a typo."""
        with self.assertRaises(KeyError):
            plans.tier("enterprise")

    def test_every_tier_key_is_in_the_schemas_check_constraint(self):
        """Two places declare which plans exist: `plans.TIERS` and migration 033's
        `account_plan_known`. They must agree or a legal plan becomes unwritable, and the
        symptom would be a webhook failing on an insert nobody could explain."""
        from pathlib import Path

        # `apps/spindle/web/tests` -> `apps/spindle`; see test_api_surface._migration on
        # why this anchors on the app root instead of the repository root.
        migration = (Path(__file__).resolve().parents[2]
                     / "schema" / "033_account_billing.sql").read_text()
        for key in plans.KEYS:
            self.assertIn(f"'{key}'", migration,
                          f"{key!r} is a tier the schema will not accept")


class TheConversationMeter(unittest.TestCase):
    """`Tier.conversation_refusal` is pure, so the arithmetic of the limit is provable
    without a cluster. The wiring into `open_thread` is proved against one, below."""

    def test_the_free_tier_allows_five_and_refuses_the_sixth(self):
        free = plans.FREE
        for opened in range(5):
            self.assertIsNone(free.conversation_refusal(opened),
                              f"{opened} already opened should still allow one more")
        self.assertIsNotNone(free.conversation_refusal(5))

    def test_the_refusal_names_the_plan_and_both_numbers(self):
        """The sentence is the product. A client that got `plan_limit_reached` and no
        numbers could not tell the operator how many they have or what to do."""
        message = plans.FREE.conversation_refusal(5)
        self.assertIn("Free / Judge", message)
        self.assertIn("5", message)
        self.assertIn(plans.PERIOD, message)

    def test_a_negotiated_tier_is_not_silently_capped(self):
        """Catalogue has no numeric allowance. Refusing would break a tenant whose limit
        this code does not know; inventing one would enforce a limit nobody agreed to."""
        catalogue = plans.tier("catalogue")
        self.assertIsNone(catalogue.conversation_refusal(10_000))


class ThePerTenantCeilingOnlyNarrows(unittest.TestCase):
    """The one property of the money ceiling that has to hold whatever else changes.

    Offline, and deliberately not in the cluster class below: `RTF_DAILY_CEILING_USD` is
    the operator's kill switch — the one thing that can be changed without a deploy to
    stop all spending everywhere — and a row that a *payment provider's webhook* writes
    must never be able to raise it. That is arithmetic, so it is proved as arithmetic and
    runs on every invocation of this suite rather than only where a cluster exists.
    """

    def _gate(self, *, deployment: str, tenant: str | None,
              paused: bool = False):
        from spindle import spend

        return spend.Gate(
            policy=spend.Policy(paid_enabled=True,
                                daily_ceiling_usd=Decimal(deployment),
                                per_call_ceiling_usd=Decimal("0.05"), dry_run=False),
            already_spent_usd=Decimal("0"), refused=[],
            tenant_ceiling_usd=None if tenant is None else Decimal(tenant),
            tenant_paused=paused)

    def test_a_larger_tenant_ceiling_cannot_raise_the_deployments(self):
        self.assertEqual(Decimal("1.00"),
                         self._gate(deployment="1.00", tenant="100.00").ceiling_usd)

    def test_a_smaller_tenant_ceiling_binds(self):
        self.assertEqual(Decimal("0.20"),
                         self._gate(deployment="1.00", tenant="0.20").ceiling_usd)

    def test_no_tenant_row_leaves_the_previous_behaviour_exactly(self):
        """Every tenant that predates migration 033, the operator's included."""
        self.assertEqual(Decimal("1.00"),
                         self._gate(deployment="1.00", tenant=None).ceiling_usd)

    def test_a_paused_tenant_is_refused_with_its_own_reason(self):
        """Distinct from a $0 ceiling. "No allowance today" and "somebody stopped this
        tenant" have different remedies, and one of them sends a person to look at a
        number that is fine."""
        from spindle import spend

        gate = self._gate(deployment="10.00", tenant="10.00", paused=True)
        with self.assertRaises(spend.SpendRefused) as caught:
            gate.check("openai:gpt-4o-mini", tokens_in=1000)
        self.assertEqual("tenant_paused", caught.exception.reason)

    def test_a_free_call_is_still_free_for_a_paused_tenant(self):
        """`check` short-circuits unpriced keys before any policy runs. Losing MusicBrainz
        when a tenant is paused would make pausing too expensive to ever use — the same
        argument the kill switch already makes."""
        gate = self._gate(deployment="0", tenant="0", paused=True)
        self.assertEqual(Decimal("0"), gate.check("musicbrainz"))


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL — these test the database, not the code")
class AgainstTheCluster(unittest.TestCase):
    """The claim, its bounds and the meter, against real indexes and a real transaction.

    Every test runs in tenants it creates and deletes. `account` and `tenant_budget` both
    cascade from `tenant`, which is why the fixture is a tenant rather than a list of rows
    somebody has to remember to clean up.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.made: list[str] = []

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            for tenant_id in self.made:
                cur.execute("DELETE FROM tenant WHERE id = %s", (tenant_id,))
        self.conn.close()

    def _claim(self, email: str | None = None) -> tuple[dict, str]:
        account, token = accounts.claim(
            self.conn, email or f"claim-{uuid.uuid4().hex[:12]}@example.com")
        self.made.append(str(account["tenant_id"]))
        return account, token

    # -------------------------------------------------- the credential round trip

    def test_a_minted_token_resolves_to_its_own_account_and_nothing_else(self):
        first, first_token = self._claim()
        second, second_token = self._claim()

        found = accounts.account_for_token(self.conn, first_token)
        self.assertIsNotNone(found)
        self.assertEqual(str(first["tenant_id"]), str(found["tenant_id"]))

        other = accounts.account_for_token(self.conn, second_token)
        self.assertEqual(str(second["tenant_id"]), str(other["tenant_id"]))
        self.assertNotEqual(str(found["tenant_id"]), str(other["tenant_id"]))

    def test_the_raw_token_is_not_in_the_database(self):
        """The one property the hash exists for. A dump of this table must hand over
        nothing presentable as a credential."""
        _, token = self._claim()
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM account WHERE token_hash = %s",
                        (token,))
            self.assertEqual(0, cur.fetchone()["n"])
            cur.execute("SELECT count(*) AS n FROM account WHERE token_hash = %s",
                        (accounts.hash_token(token),))
            self.assertEqual(1, cur.fetchone()["n"])

    def test_an_unknown_token_resolves_to_nothing(self):
        self.assertIsNone(accounts.account_for_token(self.conn, accounts.mint_token()))

    def test_an_empty_token_resolves_to_nothing_without_a_query(self):
        self.assertIsNone(accounts.account_for_token(self.conn, ""))

    # ------------------------------------------------------- a bounded new tenant

    def test_a_claim_creates_a_tenant_a_free_plan_and_a_budget(self):
        account, _ = self._claim("Judge@Example.com")
        self.assertEqual("judge@example.com", account["email"])
        self.assertEqual("free", account["plan"])

        with self.conn.cursor() as cur:
            cur.execute("SELECT daily_ceiling_usd, paused, written_by FROM tenant_budget "
                        "WHERE tenant_id = %s", (account["tenant_id"],))
            budget = cur.fetchone()
        self.assertIsNotNone(budget, "a claimed tenant must be bounded from the start")
        self.assertEqual(plans.FREE.daily_spend_cap_usd,
                         Decimal(budget["daily_ceiling_usd"]))
        self.assertFalse(budget["paused"])
        self.assertEqual("claim", budget["written_by"])

    def test_the_free_tier_is_genuinely_bounded(self):
        """Not "has a plan named free" — actually limited, in the three ways that cost
        money or reach strangers."""
        account, _ = self._claim()
        tier = accounts.plan_for_tenant(self.conn, str(account["tenant_id"]))
        self.assertEqual(5, tier.open_conversations)
        self.assertEqual(1, tier.artists)
        self.assertFalse(tier.sends_enabled)

    def test_the_spend_gate_reads_the_claimed_tenants_ceiling(self):
        """The budget row is only worth writing if the gate reads it. This is the join
        between the claim flow and the enforcement that predates it."""
        from spindle import spend

        account, _ = self._claim()
        ceiling, paused = spend.tenant_ceiling(self.conn, str(account["tenant_id"]))
        self.assertEqual(plans.FREE.daily_spend_cap_usd, ceiling)
        self.assertFalse(paused)

    def test_two_claims_cannot_share_an_address(self):
        """No email verification exists, so re-issuing a token for a known address would
        be account takeover by typing somebody else's email into a public form."""
        self._claim("taken@example.com")
        with self.assertRaises(accounts.ClaimRefused) as caught:
            accounts.claim(self.conn, "TAKEN@example.com")
        self.assertEqual("email_taken", caught.exception.reason)

    def test_a_refused_claim_writes_nothing(self):
        """The bounds are checked before the writes, so a refusal costs a read and leaves
        no tenant behind. A half-made tenant with no budget row would be the one partial
        state that is genuinely dangerous."""
        self._claim("only-once@example.com")
        before = accounts.account_count(self.conn)
        with self.assertRaises(accounts.ClaimRefused):
            accounts.claim(self.conn, "only-once@example.com")
        self.assertEqual(before, accounts.account_count(self.conn))

    def test_two_claims_from_the_same_local_part_get_different_tenants(self):
        """`info@a.com` and `info@b.com` collide on the slug base. `ensure_tenant` is
        `ON CONFLICT DO NOTHING`, so without the random suffix the second claimant would
        silently be handed the first one's tenant."""
        first, _ = self._claim("info@one.example.com")
        second, _ = self._claim("info@two.example.com")
        self.assertNotEqual(str(first["tenant_id"]), str(second["tenant_id"]))
        self.assertNotEqual(first["tenant_slug"], second["tenant_slug"])

    def test_the_slug_does_not_contain_the_domain(self):
        """A tenant slug is rendered in the console header. Putting the full address in
        it publishes somebody's email to whoever is looking at the screen."""
        account, _ = self._claim("someone@private-label.example.com")
        self.assertNotIn("private-label", account["tenant_slug"])
        self.assertNotIn("@", account["tenant_slug"])

    def test_the_hourly_bound_refuses_rather_than_slowing_down(self):
        """The rate limit is a refusal with a reason, not a sleep. Driven by pushing the
        count past the constant rather than by creating twenty accounts."""
        original = accounts.MAX_CLAIMS_PER_HOUR
        self._claim()
        accounts.MAX_CLAIMS_PER_HOUR = 0
        try:
            with self.assertRaises(accounts.ClaimRefused) as caught:
                accounts.claim(self.conn, "burst@example.com")
        finally:
            accounts.MAX_CLAIMS_PER_HOUR = original
        self.assertEqual("rate_limited", caught.exception.reason)

    def test_the_total_bound_refuses_too(self):
        original = accounts.MAX_ACCOUNTS
        accounts.MAX_ACCOUNTS = 0
        try:
            with self.assertRaises(accounts.ClaimRefused) as caught:
                accounts.claim(self.conn, "full@example.com")
        finally:
            accounts.MAX_ACCOUNTS = original
        self.assertEqual("accounts_full", caught.exception.reason)

    # ------------------------------------------------------------- the plan meter

    def _party(self, tenant_id: str, name: str, party_class: str) -> str:
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, slug, name, party_class) "
                "VALUES (%s, %s, %s, %s, %s)",
                (party_id, tenant_id, f"{party_id[:8]}", name, party_class))
        return party_id

    def test_the_free_tier_refuses_the_sixth_conversation(self):
        """The one that matters. Five open, the sixth refused, and the refusal is
        `PlanLimitReached` rather than the `UniqueViolation` a name clash raises."""
        account, _ = self._claim()
        tenant_id = str(account["tenant_id"])
        artist = self._party(tenant_id, "Test Artist", "roster")
        campaign = str(outreach.create_campaign(
            self.conn, tenant_id, party_id=artist, name="plan meter")["id"])

        for i in range(5):
            counterparty = self._party(tenant_id, f"Curator {i}", "counterparty")
            outreach.open_thread(self.conn, tenant_id, campaign_id=campaign,
                                 counterparty_id=counterparty)

        sixth = self._party(tenant_id, "Curator 6", "counterparty")
        with self.assertRaises(outreach.PlanLimitReached) as caught:
            outreach.open_thread(self.conn, tenant_id, campaign_id=campaign,
                                 counterparty_id=sixth)
        self.assertEqual("free", caught.exception.plan)
        self.assertEqual(5, caught.exception.allowance)
        self.assertEqual(5, caught.exception.opened)

    def test_the_refused_conversation_left_no_row(self):
        """The check is before the insert, so a refusal must not have half-opened
        anything — no thread, and no `party.contact_state` moved to `in_thread`."""
        account, _ = self._claim()
        tenant_id = str(account["tenant_id"])
        artist = self._party(tenant_id, "Test Artist", "roster")
        campaign = str(outreach.create_campaign(
            self.conn, tenant_id, party_id=artist, name="plan meter")["id"])
        for i in range(5):
            outreach.open_thread(
                self.conn, tenant_id, campaign_id=campaign,
                counterparty_id=self._party(tenant_id, f"Curator {i}", "counterparty"))

        sixth = self._party(tenant_id, "Curator 6", "counterparty")
        with self.assertRaises(outreach.PlanLimitReached):
            outreach.open_thread(self.conn, tenant_id, campaign_id=campaign,
                                 counterparty_id=sixth)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM thread WHERE tenant_id = %s",
                        (tenant_id,))
            self.assertEqual(5, cur.fetchone()["n"])
            cur.execute("SELECT contact_state FROM party WHERE id = %s", (sixth,))
            self.assertNotEqual("in_thread", cur.fetchone()["contact_state"])

    def test_closing_a_conversation_does_not_give_the_allowance_back(self):
        """The meter counts conversations *started*. Counting open ones would measure
        tidiness and let a free tenant cycle forever — `plans.py` argues it at length and
        this is the assertion behind the argument."""
        account, _ = self._claim()
        tenant_id = str(account["tenant_id"])
        artist = self._party(tenant_id, "Test Artist", "roster")
        campaign = str(outreach.create_campaign(
            self.conn, tenant_id, party_id=artist, name="plan meter")["id"])
        threads = []
        for i in range(5):
            threads.append(str(outreach.open_thread(
                self.conn, tenant_id, campaign_id=campaign,
                counterparty_id=self._party(tenant_id, f"Curator {i}", "counterparty")
            )["id"]))
        for thread_id in threads:
            outreach.advance(self.conn, tenant_id, thread_id, "closed_lost")

        with self.assertRaises(outreach.PlanLimitReached):
            outreach.open_thread(
                self.conn, tenant_id, campaign_id=campaign,
                counterparty_id=self._party(tenant_id, "Curator 6", "counterparty"))

    def test_a_tenant_with_no_account_row_is_not_metered(self):
        """The operator's tenant and every tenant older than migration 033. Treating an
        absent row as the free tier would cap the operator console at five conversations
        a month for a reason nobody configured."""
        tenant_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (tenant_id, f"unmetered-{tenant_id[:8]}", "unmetered"))
        self.made.append(tenant_id)

        self.assertIsNone(accounts.plan_for_tenant(self.conn, tenant_id))
        artist = self._party(tenant_id, "Test Artist", "roster")
        campaign = str(outreach.create_campaign(
            self.conn, tenant_id, party_id=artist, name="unmetered")["id"])
        for i in range(6):
            outreach.open_thread(
                self.conn, tenant_id, campaign_id=campaign,
                counterparty_id=self._party(tenant_id, f"Curator {i}", "counterparty"))

    def test_an_upgrade_raises_both_the_allowance_and_the_ceiling(self):
        """`set_plan` writes the plan and the money ceiling in one transaction. Either
        one without the other leaves a tenant paying for one tier and bounded by another."""
        from spindle import spend

        account, _ = self._claim()
        tenant_id = str(account["tenant_id"])
        tier = accounts.set_plan(self.conn, tenant_id, "roster",
                                 written_by="test", stripe_customer_id="cus_test123")

        self.assertEqual("roster", tier.key)
        self.assertEqual(250, accounts.plan_for_tenant(self.conn, tenant_id)
                         .open_conversations)
        ceiling, _paused = spend.tenant_ceiling(self.conn, tenant_id)
        self.assertEqual(tier.daily_spend_cap_usd, ceiling)
        self.assertEqual("cus_test123",
                         accounts.get_account(self.conn, tenant_id)["stripe_customer_id"])

    def test_setting_a_plan_for_a_tenant_with_no_account_is_loud(self):
        """A webhook naming a tenant this cluster cannot resolve means somebody paid and
        nothing was granted. That must not be a silent no-op."""
        with self.assertRaises(accounts.ClaimRefused) as caught:
            accounts.set_plan(self.conn, str(uuid.uuid4()), "label", written_by="test")
        self.assertEqual("no_account", caught.exception.reason)
