"""The contact harvesters, offline.

Every HTTP call is mocked and none is made, for `test_podcastindex.py`'s reason: this
suite has to pass in a checkout with no credentials and no network, and a test that
quietly skips is a test that stops covering the code on the machine where nobody notices.

What is worth testing here is not "does urllib work". It is the small number of things
these modules get wrong *silently* if they get them wrong at all, and every one of them
writes a durable row into the only table in this schema holding personal data:

  * **the refusals** — the guessed address, the third-party advertisement, the font
    vendor's address in a CSS comment, the placeholder domain. `018` makes a route hard to
    remove on purpose, so a wrong route is permanent rather than untidy, and every case
    below that asserts *nothing* is written is guarding that;
  * **the published-verbatim check**, because it is what makes "no synthesised addresses"
    a property of the module rather than a claim in its docstring;
  * **the country gate**, because `024` says a route whose country nobody established must
    not be written, and the failure mode of getting that wrong is a person's contact
    details in the wrong jurisdiction;
  * **the boilerplate filter on feed owners**, because `feeds@spreaker.com` written once
    per show is one inbox in the index tens of thousands of times.

The two failing cases in `test_declared_refuses_third_party_on_a_weak_page` and
`test_body_text_addresses_are_not_routes` are transcribed from real addresses
`docs/research/14-counterparty-sources.md` §11 found on real station homepages. They are
regression tests for a defect that was measured, not imagined.
"""

from __future__ import annotations

import unittest
import urllib.robotparser
from unittest import mock

from spindle import contacts, podcastindex


class NormalisingAddresses(unittest.TestCase):

    def test_mailto_payload_is_stripped_of_everything_but_the_address(self) -> None:
        self.assertEqual(contacts.normalise_email("mailto:Music@WREK.ORG?subject=hi"),
                         "Music@wrek.org")

    def test_domain_is_lowercased_and_the_local_part_is_not(self) -> None:
        # RFC 5321 §2.4 makes local parts case-sensitive. Rewriting an address somebody
        # published is not this module's business; the domain is case-insensitive by
        # RFC 1035 and folding it is what makes `route_one_per_value` deduplicate.
        self.assertEqual(contacts.normalise_email("Calendar@KFCF.org"), "Calendar@kfcf.org")

    def test_only_the_first_recipient_of_a_cc_list_is_taken(self) -> None:
        self.assertEqual(contacts.normalise_email("mailto:a@x.org,b@y.org"), "a@x.org")

    def test_an_asset_reference_is_not_an_address(self) -> None:
        self.assertEqual(contacts.normalise_email("logo@2x.png"), "")

    def test_a_placeholder_domain_is_refused(self) -> None:
        # §11 found `email@example.com` on a real station homepage, left in an unedited
        # template. It is published and it reaches nobody.
        self.assertEqual(contacts.normalise_email("email@example.com"), "")

    def test_a_phone_keeps_the_punctuation_the_station_wrote(self) -> None:
        self.assertEqual(contacts.normalise_phone("tel:+1-404-894-2468"), "+1-404-894-2468")

    def test_an_extension_is_not_a_reachable_number(self) -> None:
        self.assertEqual(contacts.normalise_phone("tel:x204"), "")


class Addressees(unittest.TestCase):

    def test_a_label_is_transcribed(self) -> None:
        self.assertEqual(contacts.addressee_from("Music Director:", "md@x.org"),
                         "Music Director")

    def test_an_anchor_that_is_the_address_says_nothing(self) -> None:
        self.assertEqual(contacts.addressee_from("md@x.org", "md@x.org"), "")

    def test_a_paragraph_is_a_parse_that_went_wrong_and_is_dropped(self) -> None:
        self.assertEqual(contacts.addressee_from("x" * 200, "md@x.org"), "")


class SameSite(unittest.TestCase):

    def test_a_subdomain_is_inside_the_site(self) -> None:
        self.assertTrue(contacts.same_site("https://music.wrek.org/x", "https://wrek.org/"))

    def test_a_suffix_that_is_not_a_label_boundary_is_outside(self) -> None:
        self.assertFalse(contacts.same_site("https://notwrek.org/", "https://wrek.org/"))

    def test_www_is_the_same_site(self) -> None:
        self.assertTrue(contacts.same_site("https://www.wrek.org/", "https://wrek.org/"))


class ResolvingLinks(unittest.TestCase):

    def test_an_unencoded_space_is_quoted_rather_than_left_to_raise(self) -> None:
        # Measured on ktxk.org, which links to `/management and staff`. `urlopen` raises
        # `InvalidURL` — a `ValueError`, not a `URLError` — on the raw form.
        self.assertEqual(
            contacts.resolve("/management and staff", "https://www.ktxk.org/"),
            "https://www.ktxk.org/management%20and%20staff")

    def test_a_scheme_we_do_not_fetch_resolves_to_nothing(self) -> None:
        for href in ("javascript:void(0)", "mailto:a@b.org", "#top", "ftp://x/y"):
            self.assertEqual(contacts.resolve(href, "https://wrek.org/"), "", href)


class SelectingPages(unittest.TestCase):

    HTML = """
      <a href="/news">News</a>
      <a href="/about">About</a>
      <a href="/contact">Contact</a>
      <a href="https://facebook.com/contact">Facebook</a>
    """

    def test_strong_matches_come_before_weak_ones(self) -> None:
        # Not cosmetic: CONTACT_PAGES is three, and against kfcf.org an untiered pass spent
        # all three fetches on weak matches and never reached the contact page.
        found = contacts.contact_links(self.HTML, "https://wrek.org/")
        self.assertEqual(found, ["https://wrek.org/contact", "https://wrek.org/about"])

    def test_an_offsite_link_is_never_followed(self) -> None:
        self.assertNotIn("https://facebook.com/contact",
                         contacts.contact_links(self.HTML, "https://wrek.org/"))


class PlanningRoutes(unittest.TestCase):

    SITE = "https://wprb.com/"

    def test_a_mailto_on_the_stations_own_domain_is_a_route(self) -> None:
        html = '<a href="mailto:music@wprb.com">Music Director</a>'
        routes = contacts.plan([(self.SITE, html)], site=self.SITE)
        self.assertEqual([(r.channel, r.value, r.addressee) for r in routes],
                         [("email", "music@wprb.com", "Music Director")])

    def test_declared_refuses_third_party_on_a_weak_page(self) -> None:
        # `betrayalpod@gmail.com` is real: §11 found this podcast advertisement in the body
        # of two *unrelated* iHeart station pages. Off-site domain, and `/music` is a weak
        # page — one the site routes editorially rather than as "write to us here".
        html = '<a href="mailto:betrayalpod@gmail.com">Listen</a>'
        self.assertEqual(contacts.plan([(f"{self.SITE}music", html)], site=self.SITE), [])

    def test_an_offsite_address_on_a_contact_page_is_declared(self) -> None:
        # A named staff member on a college station's own staff page, at the licensee's
        # institution. Measured on ktxk.org, whose staff are at texarkanacollege.edu.
        html = '<a href="mailto:steve@texarkanacollege.edu">Steve Mitchell</a>'
        routes = contacts.plan([(f"{self.SITE}staff", html)], site=self.SITE)
        self.assertEqual([r.value for r in routes], ["steve@texarkanacollege.edu"])

    def test_body_text_addresses_are_not_routes(self) -> None:
        # The defect §11 measured and this module originally had: `team@latofonts.com`
        # lifted out of a CSS attribution comment on a station homepage. It is on the page,
        # it matches the pattern, and it is a font vendor.
        html = ("<style>/* Lato by tyPoland, team@latofonts.com */</style>"
                "<p>Write to us at hello@wprb.com</p>")
        self.assertEqual(contacts.plan([(f"{self.SITE}contact", html)], site=self.SITE), [])

    def test_a_textarea_only_counts_on_a_page_the_site_labels_contact(self) -> None:
        html = "<form><textarea></textarea></form>"
        self.assertEqual(contacts.plan([(self.SITE, html)], site=self.SITE), [])
        routes = contacts.plan([(f"{self.SITE}contact", html)], site=self.SITE)
        self.assertEqual([(r.channel, r.value) for r in routes],
                         [("form", f"{self.SITE}contact")])

    def test_a_tel_needs_a_contact_page_because_it_has_no_domain(self) -> None:
        html = '<a href="tel:+1-609-258-3655">Studio</a>'
        self.assertEqual(contacts.plan([(self.SITE, html)], site=self.SITE), [])
        routes = contacts.plan([(f"{self.SITE}contact", html)], site=self.SITE)
        self.assertEqual([r.channel for r in routes], ["phone"])

    def test_two_spellings_of_one_mailbox_produce_one_route(self) -> None:
        html = ('<a href="mailto:Calendar@wprb.com">Calendar</a>'
                '<a href="mailto:calendar@wprb.com">calendar</a>')
        routes = contacts.plan([(f"{self.SITE}contact", html)], site=self.SITE)
        self.assertEqual([r.value for r in routes], ["Calendar@wprb.com"])

    def test_the_first_occurrence_wins_the_addressee(self) -> None:
        pages = [(f"{self.SITE}contact", '<a href="mailto:a@wprb.com">General</a>'),
                 (f"{self.SITE}staff", '<a href="mailto:a@wprb.com">Music Director</a>')]
        routes = contacts.plan(pages, site=self.SITE)
        self.assertEqual([r.addressee for r in routes], ["General"])


class TheGuessGuard(unittest.TestCase):
    """`018`: a pattern guess like `music@<domain>` is the fastest way to earn a spam
    complaint. These are the tests that make refusing it a property of the module."""

    def test_a_synthesised_address_raises_rather_than_being_written(self) -> None:
        # The failure this guard exists for, staged the only way it can happen: something
        # in the extraction path returns an address the page does not contain. Patching
        # `normalise_email` is standing in for the `info@<domain>` fallback a future edit
        # might add — the point is that `plan` refuses it at the exit rather than trusting
        # its own callers.
        page = '<a href="mailto:music@wprb.com">Contact</a>'
        with mock.patch.object(contacts, "normalise_email",
                               return_value="info@wprb.com"):
            with self.assertRaises(contacts.NotPublished):
                contacts.plan([("https://wprb.com/contact", page)],
                              site="https://wprb.com/")

    def test_published_is_the_check_and_it_is_case_folded(self) -> None:
        self.assertTrue(contacts._published("md@kexp.org", "MD@KEXP.ORG"))
        self.assertFalse(contacts._published("info@kexp.org", "MD@KEXP.ORG"))

    def test_an_entity_encoded_address_is_still_published(self) -> None:
        # `&#109;d@kexp.org` is the oldest email obfuscation on the web. The parser decodes
        # it, so the guard has to compare against the decoded page or it fires on a
        # correct read and parks the lead permanently.
        html = "<p>&#109;d@kexp.org</p>"
        self.assertTrue(contacts._published("md@kexp.org", html))

    def test_a_route_with_an_unknown_channel_cannot_be_constructed(self) -> None:
        with self.assertRaises(ValueError):
            contacts.Route(channel="postal", value="x", addressee="",
                           evidence_url="", evidence_text="")


class Robots(unittest.TestCase):

    def test_a_disallow_is_honoured_for_our_real_user_agent(self) -> None:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(["User-agent: *", "Disallow: /contact"])
        self.assertFalse(contacts.allowed(parser, "https://x.org/contact"))
        self.assertTrue(contacts.allowed(parser, "https://x.org/about"))


class FeedOwners(unittest.TestCase):
    """`<itunes:owner><itunes:email>` — the only element in any of our sources whose sole
    purpose is to say where to write."""

    FEED = ("<rss><channel><itunes:owner>"
            "<itunes:name>Mary Anne Hobbs</itunes:name>"
            "<itunes:email>mary@impressions.fm</itunes:email>"
            "</itunes:owner></channel></rss>")

    def test_the_declared_owner_is_read_with_its_name(self) -> None:
        self.assertEqual(podcastindex.owner(self.FEED),
                         ("mary@impressions.fm", "Mary Anne Hobbs"))

    def test_any_namespace_prefix_is_accepted(self) -> None:
        self.assertEqual(podcastindex.owner(self.FEED.replace("itunes:", "it:"))[0],
                         "mary@impressions.fm")

    def test_cdata_and_entities_are_unwrapped(self) -> None:
        feed = self.FEED.replace("mary@impressions.fm",
                                 "<![CDATA[mary@impressions.fm]]>")
        self.assertEqual(podcastindex.owner(feed)[0], "mary@impressions.fm")

    def test_a_hosting_platform_address_is_not_a_route(self) -> None:
        # Two of forty in §4a's sample. One inbox, reached by every show on the platform.
        for address in ("feeds@spreaker.com", "podcasts@feeds.megaphone.fm"):
            feed = self.FEED.replace("mary@impressions.fm", address)
            self.assertEqual(podcastindex.owner(feed), ("", ""), address)

    def test_a_feed_with_no_owner_element_is_a_real_answer(self) -> None:
        self.assertEqual(podcastindex.owner("<rss><channel/></rss>"), ("", ""))

    def test_an_owner_block_with_no_email_yields_nothing(self) -> None:
        feed = "<rss><channel><itunes:owner><itunes:name>X</itunes:name>" \
               "</itunes:owner></channel></rss>"
        self.assertEqual(podcastindex.owner(feed), ("", ""))


class _Cursor:
    """The little of a `psycopg` cursor `country_for` uses, with scripted answers.

    A fake rather than a cluster because `conftest.py` refuses to let this suite touch
    production, and because what is under test is a *decision* — "is there evidence of a
    country" — not a query plan. The rows are keyed by which SELECT ran, so a test that
    changes the order of the two lookups fails rather than passing by accident.
    """

    def __init__(self, identifier: bool, fact: str | None) -> None:
        self._identifier, self._fact, self._last = identifier, fact, ""

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._last = "identifier" if "party_identifier" in sql else "fact"

    def fetchone(self) -> dict | None:
        if self._last == "identifier":
            return {"?column?": 1} if self._identifier else None
        return {"value_text": self._fact} if self._fact is not None else None


class _Conn:
    def __init__(self, identifier: bool = False, fact: str | None = None) -> None:
        self._cursor = _Cursor(identifier, fact)

    def cursor(self) -> _Cursor:
        return self._cursor


class TheCountryGate(unittest.TestCase):
    """`024_regional_by_row.sql`: a route whose country nobody established must not be
    silently domiciled anywhere. `country_for` is where that becomes a refusal."""

    def test_an_fcc_licence_means_the_united_states(self) -> None:
        # There is no such thing as a non-US FCC broadcast licence, so this is a measured
        # fact about the source rather than an inference about the party.
        self.assertEqual(contacts.country_for(_Conn(identifier=True), "t", "p"), "US")

    def test_a_country_code_fact_is_read_when_there_is_no_licence(self) -> None:
        self.assertEqual(contacts.country_for(_Conn(fact="de"), "t", "p"), "DE")

    def test_no_evidence_raises_and_names_what_would_fix_it(self) -> None:
        from spindle import fleet

        with self.assertRaises(fleet.LeadFailed) as caught:
            contacts.country_for(_Conn(), "t", "p")
        self.assertTrue(caught.exception.permanent)
        # The message has to say what to run, not merely that something is missing.
        self.assertIn("refresh_stream", str(caught.exception))

    def test_a_malformed_country_is_refused_rather_than_written(self) -> None:
        from spindle import fleet

        for value in ("USA", "", "1S"):
            with self.assertRaises(fleet.LeadFailed, msg=value):
                contacts.country_for(_Conn(fact=value), "t", "p")


class Registered(unittest.TestCase):

    def test_both_stages_are_reachable_by_the_fleet(self) -> None:
        # `fleet.work_once` dispatches on `agents.REGISTRY` and nothing else, so a stage
        # with a manifest row and no registry entry renders as "declared, not running" —
        # the drift `027_enable_podcasts.sql` was written to close.
        from spindle import agents, fleet

        for kind in ("harvest_contacts", "harvest_feed_contacts", "refresh_stream"):
            agent = agents.REGISTRY[kind]
            self.assertIsInstance(agent, fleet.NetworkAgent, kind)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
