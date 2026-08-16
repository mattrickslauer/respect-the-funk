"""The beginner rails: what they say, when they stop saying it, and what they cost.

    python -m unittest discover apps/spindle/web/tests

`spindle/onboarding.py` decides whether a signed-in tenant is still a beginner by
counting three tables. Everything below exists because that decision is invisible when
it goes wrong — a banner that never appears and a banner that never leaves are both
silent, and neither shows up in an error log.

The properties this file is for:

  * **Nothing is stored, so nothing can go stale.** There is no column to assert
    against. The tests below add real rows and watch the steps tick, which is the only
    way to prove the derivation is the derivation.
  * **The banner leaves.** `state()` returns `None` when every step is done, and that
    `None` is what makes the feature acceptable at all. A checklist that survives its
    own completion is an advert.
  * **The upsell quotes `plans.TIERS` rather than restating it.** `plans.py` opens with
    the argument: a number typed somewhere else goes on being wrong after the tier
    changes, and the first symptom is a customer quoting it back. So the assertion here
    is against `plans` objects, never against a literal.
  * **The upsell never recommends a downgrade, and never nags a negotiated tenant.**
    `upsell()` walks `TIERS` positionally and checks price anyway; both halves are
    tested, because the positional walk is the part that breaks silently if somebody
    reorders the tuple.
  * **Dismissal is a cookie and is not a credential.** The flags differ from the session
    cookie's on purpose. Four values, individually easy to get wrong and invisible when
    wrong — the same reason `test_signin.py` pins the session cookie's.
"""

from __future__ import annotations

import os
import unittest
import uuid

from spindle import auth, onboarding, plans, repo, routes

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


def _request(path: str = "/", query: str = "", cookies: dict[str, str] | None = None):
    """A real Starlette `Request`. Same helper, same reasons, as `test_signin.py`.

    Cookies are passed as a header rather than set on the object, because that is where
    Starlette parses them from and a test that assigned `request.cookies` directly would
    prove the assignment rather than the parsing.
    """
    from starlette.requests import Request

    headers = [(b"host", b"testserver")]
    if cookies:
        jar = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", jar.encode()))

    scope = {
        "type": "http", "method": "GET", "path": path,
        "headers": headers, "query_string": query.encode(),
        "root_path": "", "scheme": "https",
        "server": ("testserver", 443), "client": ("test", 1),
        "app": None,
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _signed_in(plan: str = "free") -> auth.Principal:
    return auth.Principal(tenant_id=str(uuid.uuid4()), subject="operator@example.com",
                          authenticated=True, plan=plan, tenant_slug="a-label")


class TheUpsellReadsThePricingTable(unittest.TestCase):
    """`upsell()` has no database in it, so it is testable exactly."""

    def test_free_is_offered_the_cheapest_thing_that_can_actually_be_sold(self):
        offer = onboarding.upsell(plans.FREE)
        self.assertIsNotNone(offer)
        # Against the table, not against the string "Label". If the cheapest paid tier
        # is renamed or repriced this test follows it; if it stops being purchasable,
        # this test fails, which is correct — the banner would be offering a button
        # `/billing/checkout` cannot honour.
        cheapest = min((t for t in plans.TIERS if t.purchasable),
                       key=lambda t: t.price_usd_month)
        self.assertEqual(cheapest.key, offer.key)
        self.assertTrue(offer.purchasable)

    def test_nothing_is_offered_above_the_most_expensive_purchasable_tier(self):
        top = max((t for t in plans.TIERS if t.purchasable),
                  key=lambda t: t.price_usd_month)
        self.assertIsNone(onboarding.upsell(top))

    def test_a_negotiated_tenant_is_not_nudged(self):
        """Catalogue is priced in a conversation with a human. An automated strip
        suggesting they buy a smaller plan is at best noise and at worst insulting."""
        negotiated = [t for t in plans.TIERS if t.price_usd_month is None]
        self.assertTrue(negotiated, "TIERS no longer has a negotiated tier")
        for tier in negotiated:
            with self.subTest(tier=tier.key):
                self.assertIsNone(onboarding.upsell(tier))

    def test_the_offer_is_never_cheaper_than_what_the_tenant_already_pays(self):
        """The walk over `TIERS` is positional, and `TIERS` is ordered cheapest-first by
        convention rather than by construction. If somebody reorders it, the positional
        walk would start recommending a downgrade — so price is checked as well, and
        this is the test that would catch the reorder."""
        for tier in plans.TIERS:
            offer = onboarding.upsell(tier)
            if offer is None:
                continue
            with self.subTest(tier=tier.key):
                self.assertGreater(offer.price_usd_month, tier.price_usd_month)


class TheStepsAreStable(unittest.TestCase):
    """Shape assertions that need no cluster. Copy can change; these should not."""

    def test_a_dismissed_cookie_suppresses_the_banner_before_any_query(self):
        """`_start_rails` checks the cookie before it opens a cursor. Asserted by
        passing a connection that would raise if touched — if the order ever reverses,
        every page render for a dismissed operator pays for a query nobody reads."""

        class ExplodingConnection:
            def cursor(self, *a, **k):
                raise AssertionError("the cookie check must come before the query")

        rails = routes._start_rails(
            _request(cookies={onboarding.COOKIE_NAME: onboarding.DISMISSED}),
            _signed_in(), ExplodingConnection(), "some-tenant")
        self.assertIsNone(rails)

    def test_an_unrecognised_cookie_value_shows_the_banner(self):
        """Errs toward showing the hint. Being shown something you have finished with is
        a mild annoyance; being silently denied the only guidance in the product because
        of a stale cookie from a future build is not."""

        class ExplodingConnection:
            def cursor(self, *a, **k):
                raise AssertionError("reached the query, which is the point")

        with self.assertRaises(AssertionError):
            routes._start_rails(
                _request(cookies={onboarding.COOKIE_NAME: "yes"}),
                _signed_in(), ExplodingConnection(), "some-tenant")

    def test_anonymous_never_sees_rails(self):
        self.assertIsNone(
            routes._start_rails(_request(), auth.ANONYMOUS, object(), None))

    def test_dismissal_sets_a_cookie_that_is_not_a_credential(self):
        """Deliberately *not* httpOnly — it protects nothing, and claiming otherwise
        would suggest it does. Still `secure` and `samesite`, because there is no reason
        for it to cross plain HTTP or be settable cross-origin."""
        response = routes.start_dismiss(_signed_in(), back="/tracks?sel=abc")
        header = response.headers["set-cookie"]
        self.assertIn(f"{onboarding.COOKIE_NAME}={onboarding.DISMISSED}", header)
        self.assertIn("Secure", header)
        self.assertIn("samesite=lax", header.lower())
        self.assertNotIn("httponly", header.lower())
        self.assertEqual("/tracks?sel=abc", response.headers["location"])

    def test_dismissal_will_not_redirect_off_site(self):
        """`back` arrives in a form post. `//evil.example` is protocol-relative and
        browsers follow it off-origin — the same check every other return-to-where-you-
        were redirect in `routes.py` uses."""
        for hostile in ("//evil.example", "https://evil.example", "javascript:alert(1)"):
            with self.subTest(back=hostile):
                response = routes.start_dismiss(_signed_in(), back=hostile)
                self.assertEqual("/", response.headers["location"])


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL")
class TheRailsAreDerivedFromTheCatalogue(unittest.TestCase):
    """The whole argument of the module: the steps are counts, not stored flags.

    Each test adds a real row and watches the derivation move. Nothing here writes an
    onboarding record, because there is no onboarding record to write.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenant (slug, name) VALUES (%s, %s) RETURNING id",
                (f"rails-{uuid.uuid4().hex[:12]}", "Rails Test Label"))
            self.tenant_id = str(cur.fetchone()["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            # `tenant` cascades to `party`, `party_role`, `recording` and
            # `recording_asset`, so this one statement is the whole cleanup.
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant_id,))
        self.conn.close()

    def _state(self) -> onboarding.Rails | None:
        return onboarding.state(self.conn, self.tenant_id, plans.DEFAULT_KEY)

    def _step(self, key: str) -> onboarding.Step:
        rails = self._state()
        self.assertIsNotNone(rails, "expected an incomplete checklist")
        found = next(s for s in rails.steps if s.key == key)
        return found

    def _add_artist(self, tenant_id: str | None = None) -> None:
        """Through `repo.create_party`, not a raw INSERT.

        There is no `artist` table to insert into — migration 005 made the roster a
        party plus a `party_role` row, and a test that wrote the two by hand would be
        asserting against its own idea of what the console does. Going through the same
        function `POST /artists` calls is what makes this a claim about the product:
        whatever the write path considers an artist is what the checklist has to count.
        """
        repo.create_party(self.conn, tenant_id or self.tenant_id,
                          name=f"An Act {uuid.uuid4().hex[:6]}", type_="solo")

    def _add_recording(self) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recording (tenant_id, slug, title) VALUES (%s,%s,%s) "
                "RETURNING id",
                (self.tenant_id, f"trk-{uuid.uuid4().hex[:8]}", "A Track"))
            return str(cur.fetchone()["id"])

    def _add_master(self, recording_id: str, *, state: str = "stored") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO recording_asset
                     (tenant_id, recording_id, kind, bucket, object_key, content_hash,
                      state, uploaded_at)
                   VALUES (%s,%s,'master','b',%s,%s,%s,
                           CASE WHEN %s = 'stored' THEN now() ELSE NULL END)""",
                (self.tenant_id, recording_id, f"k/{uuid.uuid4().hex}",
                 uuid.uuid4().hex + uuid.uuid4().hex, state, state))

    # ------------------------------------------------------------ the ladder

    def test_a_fresh_tenant_has_three_things_to_do(self):
        rails = self._state()
        self.assertIsNotNone(rails)
        self.assertEqual(("artist", "track", "master"),
                         tuple(s.key for s in rails.steps))
        self.assertEqual(0, rails.done_count)
        self.assertFalse(rails.complete)
        self.assertEqual("artist", rails.next_step.key)

    def test_adding_an_artist_ticks_the_first_step_and_advances_the_next(self):
        self._add_artist()
        rails = self._state()
        self.assertTrue(rails.steps[0].done)
        self.assertEqual("track", rails.next_step.key)
        self.assertEqual(1, rails.done_count)

    def test_the_master_step_has_nowhere_to_point_until_there_is_a_track(self):
        """The audio panel lives in the Tracks inspector for a *selected* recording. A
        button offered before there is one leads to an empty screen, which is the kind
        of dead end that makes a tutorial worse than none."""
        self.assertFalse(self._step("master").ready)
        recording_id = self._add_recording()
        master = self._step("master")
        self.assertTrue(master.ready)
        self.assertIn(recording_id, master.href)

    def test_a_pending_upload_is_not_a_master(self):
        """`state = 'pending'` means a row was claimed and the bytes never arrived.
        Counting it would tick the step for an upload that failed halfway, and the
        operator would be told they had finished with nothing in the bucket."""
        recording_id = self._add_recording()
        self._add_master(recording_id, state="pending")
        self.assertFalse(self._step("master").done)
        self._add_master(recording_id, state="stored")
        self.assertTrue(self._step("master").done)

    def test_the_banner_disappears_when_the_work_is_real(self):
        """The property that makes the feature acceptable. `None` means render nothing."""
        self._add_artist()
        recording_id = self._add_recording()
        self._add_master(recording_id)
        self.assertIsNone(self._state())

    def test_it_comes_back_if_the_catalogue_does_not_hold(self):
        """Derived state behaves correctly under deletion, and a stored flag would not:
        a tenant who removes their only artist is looking at an empty console again, and
        the sentence explaining why should be there again too."""
        self._add_artist()
        recording_id = self._add_recording()
        self._add_master(recording_id)
        self.assertIsNone(self._state())

        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM party WHERE tenant_id = %s", (self.tenant_id,))

        rails = self._state()
        self.assertIsNotNone(rails)
        self.assertEqual("artist", rails.next_step.key)

    def test_a_withdrawn_row_still_counts(self):
        """The step asks whether the operator has added one of these, not whether they
        have a live one. Reading it the other way would resurrect the whole checklist
        for a label that archived its back catalogue."""
        self._add_artist()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE party SET status = 'archived' WHERE tenant_id = %s",
                        (self.tenant_id,))
        self.assertTrue(self._step("artist").done)

    def test_a_party_that_is_not_on_the_roster_does_not_tick_the_step(self):
        """The roster is a role, and this is the test that says so.

        Counting `party` bare would pass every other test in this class and still be
        wrong here: a label that has harvested creators or imported counterparties off a
        statement has parties and an empty roster. Ticking their first step sends them to
        `/artists` with nothing on it — worse than never having shown the checklist.
        """
        repo.create_party(self.conn, self.tenant_id, name="Some Creator", type_="",
                          role="creator")
        self.assertFalse(self._step("artist").done)
        self._add_artist()
        self.assertTrue(self._step("artist").done)

    def test_another_tenants_catalogue_does_not_tick_these_steps(self):
        """Every subquery is scoped by `tenant_id`. A missing predicate on any one of
        them would mark a brand-new account complete because somebody else had work."""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenant (slug, name) VALUES (%s,%s) RETURNING id",
                (f"rails-other-{uuid.uuid4().hex[:10]}", "Someone Else"))
            other = str(cur.fetchone()["id"])
        try:
            self._add_artist(other)
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO recording (tenant_id, slug, title) VALUES (%s,%s,%s)",
                    (other, "their-track", "Their Track"))
            rails = self._state()
            self.assertEqual(0, rails.done_count)
        finally:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM tenant WHERE id = %s", (other,))

    def test_an_unknown_plan_key_raises_rather_than_resolving_to_free(self):
        """`plans.tier` is the loud direction and this path must not soften it. Quietly
        rendering the free tier's numbers in the upsell for a paying tenant would be a
        pricing page that contradicts the gate."""
        with self.assertRaises(KeyError):
            onboarding.state(self.conn, self.tenant_id, "enterprise-platinum")

    # ------------------------------------------------------------- rendering
    #
    # `state()` being right is half the feature. The other half is `_console.html`
    # actually drawing it, on every screen, and a Jinja error in that block would take
    # the whole console down rather than degrade — so the markup is exercised rather
    # than trusted. These render the real handlers, which is the house style here.

    def _principal(self, plan: str = "free") -> auth.Principal:
        return auth.Principal(tenant_id=self.tenant_id, subject="op@example.com",
                              authenticated=True, plan=plan, tenant_slug="rails-test")

    def _render(self, handler, path: str) -> str:
        real = routes._conn
        routes._conn = lambda: self.conn
        try:
            return handler(_request(path), self._principal()).body.decode()
        finally:
            routes._conn = real

    def test_the_banner_is_on_every_console_screen_not_just_today(self):
        """The point of putting it in `_console.html` rather than in `today.html`. An
        operator who lands on Artists from a link is the one who most needs it."""
        for handler, path in ((routes.home, "/"),
                              (routes.artists_console, "/artists"),
                              (routes.tracks, "/tracks"),
                              (routes.imports_console, "/imports")):
            with self.subTest(path=path):
                html = self._render(handler, path)
                self.assertIn("Start here", html)
                self.assertIn("Add an artist", html)

    def test_the_banner_is_gone_once_the_work_is_done(self):
        self._add_artist()
        self._add_master(self._add_recording())
        html = self._render(routes.home, "/")
        self.assertNotIn("Start here", html)
        # The element, not the class name. `_console.html` carries its stylesheet
        # inline, so `.startbar` is in the `<style>` block of every page whether or not
        # the banner is drawn — asserting on the bare string would have passed on a
        # console that never rendered the section at all, and failed on one that always
        # did. The `aria-label` only exists on the section itself.
        self.assertNotIn('aria-label="Getting started"', html)

    def test_the_upsell_quotes_the_pricing_table_rather_than_a_literal(self):
        """Every figure in the strip has to come from `plans.TIERS`. Asserted by taking
        the summary out of the table and looking for it, so a number retyped into the
        template would leave this passing only while the two agreed — and the moment a
        tier changed, this fails instead of the customer noticing."""
        html = self._render(routes.home, "/")
        offer = onboarding.upsell(plans.FREE)
        self.assertIn(plans.FREE.summary, html)
        self.assertIn(offer.summary, html)
        self.assertIn(f"${offer.price_usd_month} a", html)
        # A link to where the checkout that works lives, and no checkout of its own.
        self.assertIn('href="/account"', html)
        self.assertNotIn("/billing/checkout", html)

    def test_the_dismiss_control_carries_the_page_it_was_drawn_on(self):
        """Including the query string. Dismissing from `/tracks?sel=…` must not also
        drop the selected track — `routes._safe_back` gets it back verbatim."""
        recording_id = self._add_recording()
        real = routes._conn
        routes._conn = lambda: self.conn
        try:
            html = routes.tracks(_request("/tracks", f"sel={recording_id}"),
                                 self._principal(), sel=recording_id).body.decode()
        finally:
            routes._conn = real
        self.assertIn(f'value="/tracks?sel={recording_id}"', html)
