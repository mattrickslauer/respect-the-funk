"""The Podcast Index adapter, offline.

Every HTTP call is mocked and none is made. That is not only politeness to a
non-commercial service answering for free: this suite has to pass in a checkout with no
credentials, and a test that quietly skips when a key is absent is a test that stops
covering the code on exactly the machine where nobody notices.

What is worth testing here is not "does urllib work". It is the three things this adapter
gets wrong silently if it gets them wrong at all:

  * **the block arithmetic**, because a gap in the tiling loses feeds and reports success;
  * **the failure paths**, because the whole argument for this module raising rather than
    returning empty is that an outage must not look like a corpus with no podcasts in it;
  * **the provenance-bearing readers** — `show_kind`, `musical`, `pitchable` — because
    they are the inferences, and an inference that quietly widens is how a shortlist fills
    with talk radio.
"""

from __future__ import annotations

import hashlib
import io
import json
import unittest
import urllib.error
from typing import Any
from unittest import mock

from rtf_platform import podcastindex, profiles

CREDS = {podcastindex.KEY_VAR: "TESTKEY0000000000000",
         podcastindex.SECRET_VAR: "testsecret##0000"}


class _Response:
    """The little that `urllib.request.urlopen`'s context manager has to provide."""

    def __init__(self, payload: Any) -> None:
        if isinstance(payload, bytes):
            self._body = payload
        elif isinstance(payload, str):
            self._body = payload.encode()
        else:
            self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(code: int, body: str = "nope") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.podcastindex.org/x", code, "err", {},
                                  io.BytesIO(body.encode()))


def _answering(payload: Any):
    """Patch the module's `urlopen` to answer with `payload`, capturing the request."""
    captured: list[Any] = []

    def fake(request, timeout=None):          # noqa: ANN001 - mirrors urlopen's shape
        captured.append(request)
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload)

    return mock.patch.object(podcastindex.urllib.request, "urlopen", fake), captured


def _feed(feed_id: int = 100, **over: Any) -> dict[str, Any]:
    """A feed record shaped like the API's, with the fields this module reads."""
    base: dict[str, Any] = {
        "id": feed_id,
        "title": "Deep House Weekly",
        "url": "https://example.com/feed.xml",
        "link": "https://example.com",
        "description": "<p>New deep house and garage every Friday.</p>",
        "author": "Example Media",
        "ownerName": "Jo Presenter",
        "language": "en",
        "categories": {"55": "Music", "77": "Commentary"},
        "medium": "podcast",
        "dead": 0,
        "episodeCount": 212,
        "lastHttpStatus": 200,
        "itunesId": 12345,
    }
    base.update(over)
    return base


# ------------------------------------------------------------------------- credentials

class Credentials(unittest.TestCase):

    def test_both_present_is_the_only_configured_state(self) -> None:
        with mock.patch.dict("os.environ", CREDS, clear=False):
            self.assertEqual(podcastindex.credentials(),
                             (CREDS[podcastindex.KEY_VAR],
                              CREDS[podcastindex.SECRET_VAR]))

    def test_a_key_without_a_secret_raises_rather_than_half_working(self) -> None:
        """A key alone cannot sign a request, so accepting it would turn a missing
        credential into a 401 two layers away from its cause."""
        with mock.patch.dict("os.environ",
                             {podcastindex.KEY_VAR: "k", podcastindex.SECRET_VAR: ""}):
            with self.assertRaises(podcastindex.NotConfigured) as caught:
                podcastindex.credentials()
        self.assertIn(podcastindex.SECRET_VAR, str(caught.exception))

    def test_the_message_is_actionable(self) -> None:
        """Naming both variables and where to get them is the whole difference between
        an error somebody can fix and one they have to go source-diving for."""
        with mock.patch.dict("os.environ",
                             {podcastindex.KEY_VAR: "", podcastindex.SECRET_VAR: ""}):
            with self.assertRaises(podcastindex.NotConfigured) as caught:
                podcastindex.credentials()
        message = str(caught.exception)
        self.assertIn(podcastindex.KEY_VAR, message)
        self.assertIn(podcastindex.SECRET_VAR, message)
        self.assertIn("api.podcastindex.org", message)

    def test_whitespace_is_not_a_credential(self) -> None:
        with mock.patch.dict("os.environ",
                             {podcastindex.KEY_VAR: "  ",
                              podcastindex.SECRET_VAR: "  "}):
            with self.assertRaises(podcastindex.NotConfigured):
                podcastindex.credentials()


class Signing(unittest.TestCase):

    def test_the_signature_is_sha1_of_key_secret_time(self) -> None:
        expected = hashlib.sha1(b"KEYSECRET1700000000").hexdigest()
        self.assertEqual(podcastindex.signature("KEY", "SECRET", 1700000000), expected)

    def test_the_signature_is_pure_in_its_arguments(self) -> None:
        """`headers` takes the timestamp rather than reading the clock, so the value is
        reproducible — which is what makes this assertable at all."""
        first = podcastindex.signature("k", "s", 1)
        self.assertEqual(first, podcastindex.signature("k", "s", 1))
        self.assertNotEqual(first, podcastindex.signature("k", "s", 2))

    def test_every_header_the_service_requires_is_sent(self) -> None:
        sent = podcastindex.headers("KEY", "SECRET", 1700000000)
        self.assertEqual(sent["X-Auth-Key"], "KEY")
        self.assertEqual(sent["X-Auth-Date"], "1700000000")
        self.assertEqual(sent["Authorization"],
                         podcastindex.signature("KEY", "SECRET", 1700000000))
        self.assertIn("respect-the-funk", sent["User-Agent"])
        # The secret authenticates; it is never transmitted.
        self.assertNotIn("SECRET", json.dumps(sent))


# ------------------------------------------------------------------------ failure paths

class Failing(unittest.TestCase):

    def _call(self, payload: Any):
        patch, captured = _answering(payload)
        with mock.patch.dict("os.environ", CREDS, clear=False), patch:
            return podcastindex._get("/recent/newfeeds", {"max": 1}), captured

    def test_a_401_is_permanent_and_blames_the_clock_too(self) -> None:
        """The three-minute skew window means a perfectly good key produces a 401 on a
        machine with a wrong clock. A message that only says "check the key" costs an
        hour, so both causes are named."""
        with self.assertRaises(podcastindex.Refused) as caught:
            self._call(_http_error(401))
        self.assertTrue(caught.exception.permanent)
        message = str(caught.exception)
        self.assertIn("clock", message)
        self.assertIn(podcastindex.KEY_VAR, message)

    def test_a_429_is_transient_so_the_lead_goes_back_to_the_frontier(self) -> None:
        with self.assertRaises(podcastindex.Refused) as caught:
            self._call(_http_error(429))
        self.assertFalse(caught.exception.permanent)

    def test_a_500_is_transient_and_a_404_is_not(self) -> None:
        with self.assertRaises(podcastindex.Refused) as caught:
            self._call(_http_error(503))
        self.assertFalse(caught.exception.permanent)
        with self.assertRaises(podcastindex.Refused) as caught:
            self._call(_http_error(404))
        self.assertTrue(caught.exception.permanent)

    def test_a_non_json_body_raises_rather_than_becoming_nothing(self) -> None:
        with self.assertRaises(podcastindex.Refused) as caught:
            self._call(b"<html>maintenance</html>")
        self.assertIn("not JSON", str(caught.exception))

    def test_a_status_false_envelope_is_a_failure_even_at_http_200(self) -> None:
        """The API answers 200 with `status: "false"` for a query it could not serve.
        Reading the body's own verdict is the difference between an error and an empty
        corpus that looks like a working one."""
        with self.assertRaises(podcastindex.Refused) as caught:
            self._call({"status": "false", "description": "no such feed"})
        self.assertIn("no such feed", str(caught.exception))

    def test_a_body_with_no_status_is_not_assumed_good(self) -> None:
        with self.assertRaises(podcastindex.Refused):
            self._call({"feeds": [_feed()]})

    def test_a_missing_credential_raises_before_any_socket_is_opened(self) -> None:
        opened: list[Any] = []

        def fake(request, timeout=None):       # noqa: ANN001
            opened.append(request)
            return _Response({"status": "true", "feeds": []})

        with mock.patch.dict("os.environ",
                             {podcastindex.KEY_VAR: "",
                              podcastindex.SECRET_VAR: ""}), \
                mock.patch.object(podcastindex.urllib.request, "urlopen", fake):
            with self.assertRaises(podcastindex.NotConfigured):
                podcastindex._get("/recent/newfeeds")
        self.assertEqual(opened, [], "a call was attempted without credentials")

    def test_a_page_with_no_feeds_key_is_not_an_empty_page(self) -> None:
        patch, _ = _answering({"status": "true", "count": 0})
        with mock.patch.dict("os.environ", CREDS, clear=False), patch:
            with self.assertRaises(podcastindex.Refused):
                podcastindex.block(0)

    def test_an_empty_index_ceiling_raises_rather_than_returning_zero(self) -> None:
        """A zero ceiling would seed no leads and report success — the shape of failure
        this codebase keeps calling the worst kind."""
        patch, _ = _answering({"status": "true", "feeds": []})
        with mock.patch.dict("os.environ", CREDS, clear=False), patch:
            with self.assertRaises(podcastindex.Refused):
                podcastindex.newest_feed_id()


# ------------------------------------------------------------------------ block paging

class Blocks(unittest.TestCase):

    def test_the_block_is_half_open_and_drops_what_it_does_not_own(self) -> None:
        """`feedid` is documented as "the ID to start from" without saying whether that
        is inclusive, and the endpoint over-returns because `max` is twice `BLOCK`.
        Filtering on the rows makes both facts irrelevant."""
        rows = [_feed(i) for i in (999, 1000, 1200, 1499, 1500, 4000)]
        patch, captured = _answering({"status": "true", "feeds": rows})
        with mock.patch.dict("os.environ", CREDS, clear=False), patch:
            got = podcastindex.block(1000, span=500)
        self.assertEqual([f["id"] for f in got], [1000, 1200, 1499])
        self.assertIn("feedid=1000", captured[0].full_url)

    def test_the_request_asks_for_more_rows_than_a_block_can_hold(self) -> None:
        """This is the truncation guarantee, not an oversight: a block narrower than the
        API's row cap cannot come back clipped, so a full response is proof of
        completeness rather than a number to wonder about."""
        self.assertLess(podcastindex.BLOCK, podcastindex.PAGE)
        patch, captured = _answering({"status": "true", "feeds": []})
        with mock.patch.dict("os.environ", CREDS, clear=False), patch:
            podcastindex.block(0)
        self.assertIn(f"max={podcastindex.PAGE}", captured[0].full_url)

    def test_a_row_without_an_integer_id_is_dropped_not_guessed(self) -> None:
        rows = [_feed(10), {"id": "twelve", "title": "x"}, {"title": "no id"}]
        patch, _ = _answering({"status": "true", "feeds": rows})
        with mock.patch.dict("os.environ", CREDS, clear=False), patch:
            got = podcastindex.block(0)
        self.assertEqual([f["id"] for f in got], [10])

    def test_a_negative_block_is_a_programming_error(self) -> None:
        with mock.patch.dict("os.environ", CREDS, clear=False):
            with self.assertRaises(ValueError):
                podcastindex.block(-1)
            with self.assertRaises(ValueError):
                podcastindex.block(0, span=0)

    def test_the_blocks_tile_the_id_space_with_no_gap_and_no_overlap(self) -> None:
        """The property the whole paging design rests on. Every feed ID up to the
        ceiling must fall in exactly one block, or the stage loses shows and says
        nothing."""
        ceiling = 7321
        starts = podcastindex.blocks_to(ceiling, span=500)
        covered: list[int] = []
        for first in starts:
            covered.extend(range(first, first + 500))
        for feed_id in range(0, ceiling + 1):
            self.assertEqual(covered.count(feed_id), 1,
                             f"feed id {feed_id} is covered {covered.count(feed_id)} "
                             "times, not once")

    def test_the_last_block_reaches_the_ceiling(self) -> None:
        starts = podcastindex.blocks_to(1000, span=500)
        self.assertEqual(starts, [0, 500, 1000])

    def test_a_start_offset_does_not_shift_the_tiling(self) -> None:
        self.assertEqual(podcastindex.blocks_to(2000, start=500, span=500),
                         [500, 1000, 1500, 2000])

    def test_a_negative_ceiling_raises(self) -> None:
        with self.assertRaises(ValueError):
            podcastindex.blocks_to(-1)

    def test_the_newest_feed_id_is_read_from_the_index(self) -> None:
        patch, captured = _answering({"status": True, "feeds": [_feed(4_812_003)]})
        with mock.patch.dict("os.environ", CREDS, clear=False), patch:
            self.assertEqual(podcastindex.newest_feed_id(), 4_812_003)
        self.assertIn("max=1", captured[0].full_url)


# ------------------------------------------------------------------------------ reading

class Reading(unittest.TestCase):

    def test_categories_are_lowercased_and_deduplicated_in_order(self) -> None:
        feed = _feed(categories={"55": "Music", "77": "Commentary", "78": "music"})
        self.assertEqual(podcastindex.categories(feed), ["music", "commentary"])

    def test_a_feed_with_no_categories_gets_no_guess(self) -> None:
        self.assertEqual(podcastindex.categories(_feed(categories=None)), [])
        self.assertEqual(podcastindex.categories(_feed(categories={})), [])

    def test_music_is_the_parent_not_a_leaf(self) -> None:
        """Apple's "Music Commentary" arrives flattened as Music + Commentary, so the
        parent is what identifies a music show. A leaf-only match would miss every
        refined show and a substring match would catch "Music" inside nothing useful."""
        self.assertTrue(podcastindex.musical(["music", "commentary"]))
        self.assertFalse(podcastindex.musical(["comedy", "commentary"]))
        self.assertFalse(podcastindex.musical(["music history"]))

    def test_show_kind_is_refined_by_the_leaf(self) -> None:
        self.assertEqual(podcastindex.show_kind(["music", "commentary"]),
                         "music_commentary")
        self.assertEqual(podcastindex.show_kind(["music", "interviews"]),
                         "music_interviews")

    def test_a_bare_music_show_is_named_as_one(self) -> None:
        """The most valuable of the four kinds: a show filed under Music and nothing
        else is likelier to actually play records than one that has told the store it is
        commentary."""
        self.assertEqual(podcastindex.show_kind(["music"]), "music_show")

    def test_a_non_music_show_is_other_rather_than_a_music_kind(self) -> None:
        self.assertEqual(podcastindex.show_kind(["news", "politics"]), "other")
        self.assertEqual(podcastindex.show_kind([]), "other")

    def test_the_host_prefers_the_owner_over_the_author(self) -> None:
        self.assertEqual(podcastindex.host(_feed()), "Jo Presenter")
        self.assertEqual(podcastindex.host(_feed(ownerName="")), "Example Media")
        self.assertEqual(podcastindex.host(_feed(ownerName="", author="")), "")

    def test_a_dead_or_empty_feed_is_not_alive(self) -> None:
        self.assertTrue(podcastindex.alive(_feed()))
        self.assertFalse(podcastindex.alive(_feed(dead=1)))
        self.assertFalse(podcastindex.alive(_feed(episodeCount=0)))
        self.assertFalse(podcastindex.alive(_feed(lastHttpStatus=404)))

    def test_a_feed_never_crawled_is_unknown_not_bad(self) -> None:
        """Treating a missing crawl status as a failure would drop every feed added in
        the last few hours, which is the half of the index that grows."""
        self.assertTrue(podcastindex.alive(_feed(lastHttpStatus=None)))

    def test_the_music_medium_is_excluded_because_it_is_the_record(self) -> None:
        """`medium = music` means the feed *is* an album published over RSS. Its owner is
        an artist — a peer, not a counterparty — and mailing them a promo is mailing a
        stranger their own competition."""
        self.assertTrue(podcastindex.pitchable(_feed()))
        self.assertFalse(podcastindex.pitchable(_feed(medium="music")))

    def test_a_talk_show_is_not_pitchable(self) -> None:
        self.assertFalse(
            podcastindex.pitchable(_feed(categories={"1": "News", "2": "Politics"})))

    def test_the_blurb_loses_its_markup_and_keeps_its_words(self) -> None:
        said = podcastindex.blurb(_feed())
        self.assertNotIn("<", said)
        self.assertIn("deep house", said.lower())

    def test_the_blurb_is_capped(self) -> None:
        long = podcastindex.blurb(_feed(description="word " * 5000))
        self.assertLessEqual(len(long), podcastindex.BLURB_CHARS)


# ------------------------------------------------------------------- the embedded text

class ProfileText(unittest.TestCase):

    def test_the_shows_name_is_not_in_the_vector(self) -> None:
        """`profiles.py` rule 2, which this module inherits rather than re-litigates:
        putting the name in the embedded text is how an artist called Deep House gets
        shortlisted to a show called Deep House Weekly for reasons unrelated to what
        either of them plays."""
        text = podcastindex.profile_text(_feed())
        self.assertNotIn("Deep House Weekly", text)

    def test_the_genres_and_the_blurb_both_reach_the_text(self) -> None:
        text = podcastindex.profile_text(_feed())
        self.assertIn("music", text.lower())
        self.assertIn("every Friday", text)

    def test_it_is_composed_by_the_one_composer(self) -> None:
        """Not a fourth composer: `profiles.py` exists because three modules were each
        writing their own and half of every document was boilerplate every other
        document also had. The structured half must come from `compose`, and it must be
        a podcast rather than a radio station."""
        text = podcastindex.profile_text(_feed())
        self.assertIn("podcast", text)
        self.assertNotIn("radio station", text)
        self.assertTrue(
            text.startswith(profiles.compose(genres=["music", "commentary"],
                                             station_kind="music_commentary",
                                             language="en", role="podcast")))

    def test_a_show_with_no_categories_says_so_rather_than_guessing(self) -> None:
        """`profiles.UNKNOWN` is deliberately vocabulary no genre query is near, so an
        undocumented show sinks rather than floating on noise."""
        text = podcastindex.profile_text(
            _feed(categories={}, description="", language=""))
        self.assertEqual(text, f"{profiles.UNKNOWN} A podcast.")

    def test_no_two_shows_share_their_whole_text(self) -> None:
        """The failure `profiles.py` was written about, checked on this source: if the
        composed text were dominated by a constant tail, two different shows would embed
        to nearly the same vector."""
        one = podcastindex.profile_text(_feed(description="Grime and UK drill."))
        two = podcastindex.profile_text(
            _feed(categories={"1": "Music", "2": "Interviews"},
                  description="Long-form interviews with jazz players."))
        self.assertNotEqual(one, two)


if __name__ == "__main__":
    unittest.main()
