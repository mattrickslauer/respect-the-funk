"""The Radio Browser refresh, offline.

`031_stream_refresh.sql` argues that 1,651 counterparties share one embedded document and
are therefore indexed but not rankable, and that the fields which would split them were in
a payload `index_streams` already fetched and did not store. This suite covers the three
readers that decide what gets written, because each of them fails in a way nothing else
would notice:

  * **`country_code`** decides where a person's contact details are physically stored, via
    `contacts.country_for`. `024_regional_by_row.sql` spends a section on the alpha-2 trap
    — FCC `AL` is Alabama and ISO `AL` is Albania — and the protection is that this reads
    `countrycode` and never `state`. A test that proves it reads the right field is
    cheap insurance against somebody helpfully "fixing" a blank country from the state.
  * **`market`** must refuse a country-only value. Writing `Broadcasts to The United
    States Of America.` into thousands of documents is `profiles.py` rule 1 being violated
    by the module that exists to repair it, and the mistake would look like an improvement
    in a coverage count.
  * **`by_uuid`** must report a delisted station as *absent*, not as a station with empty
    fields, because the caller writes the second one into the database as though it were a
    reading.
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

from spindle import streams


class _Response:
    def __init__(self, payload: Any) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, *_: int) -> bytes:
        return self._body


class CountryCode(unittest.TestCase):

    def test_the_iso_field_is_the_one_read(self) -> None:
        self.assertEqual(
            streams.country_code({"countrycode": "de", "state": "AL"}), "DE")

    def test_a_state_code_can_never_reach_the_column(self) -> None:
        # The alpha-2 trap. A station in Alabama with no `countrycode` yields nothing
        # rather than 'AL', which ISO would read as Albania.
        self.assertEqual(streams.country_code({"countrycode": "", "state": "Alabama"}), "")

    def test_anything_not_two_letters_is_refused(self) -> None:
        for value in ("USA", "U", "1S", "  "):
            self.assertEqual(streams.country_code({"countrycode": value}), "", value)


class Market(unittest.TestCase):

    def test_a_state_and_country_make_a_market(self) -> None:
        self.assertEqual(
            streams.market({"state": "Nevada", "country": "The United States Of America"}),
            "Nevada, The United States Of America")

    def test_a_country_alone_is_not_a_market(self) -> None:
        # 40 of 40 sampled stations carry a country and 13 carry a state. A country-only
        # market would put one identical sentence in thousands of documents.
        self.assertEqual(
            streams.market({"state": "", "country": "The United States Of America"}), "")


class Language(unittest.TestCase):

    def test_case_and_whitespace_are_normalised_into_one_bucket(self) -> None:
        self.assertEqual(streams.language({"language": "  English "}), "english")

    def test_an_unstated_language_is_not_invented(self) -> None:
        self.assertEqual(streams.language({}), "")


class ByUuid(unittest.TestCase):

    UUIDS = ["7ac99cd5-c41a-4222-a8b1-7850a00040e8",
             "81c1b6ca-ffa2-4aa4-a900-1b4a9dea5ab5"]

    def test_the_result_is_keyed_so_a_delisted_station_is_absent(self) -> None:
        payload = [{"stationuuid": self.UUIDS[0], "name": "PaganRadio.org",
                    "countrycode": "US"}]
        with mock.patch.object(streams.urllib.request, "urlopen",
                               return_value=_Response(payload)):
            found = streams.by_uuid(self.UUIDS)
        self.assertIn(self.UUIDS[0], found)
        # Not present with empty fields — absent. The writer counts it as `gone` and
        # writes nothing, which is a different act from writing a blank country.
        self.assertNotIn(self.UUIDS[1], found)

    def test_no_uuids_makes_no_call(self) -> None:
        with mock.patch.object(streams.urllib.request, "urlopen") as opened:
            self.assertEqual(streams.by_uuid([]), {})
        opened.assert_not_called()

    def test_every_uuid_is_sent_in_the_query(self) -> None:
        seen: list[str] = []

        def capture(request: Any, timeout: int = 0) -> _Response:
            seen.append(request.full_url)
            return _Response([])

        with mock.patch.object(streams.urllib.request, "urlopen", side_effect=capture):
            streams.by_uuid(self.UUIDS)
        self.assertEqual(len(seen), 1)
        for uuid in self.UUIDS:
            self.assertIn(uuid, seen[0])


class Buckets(unittest.TestCase):

    def test_the_buckets_are_the_sixteen_hex_digits(self) -> None:
        # The seeder in `ingest.refresh_streams` filters what it reads out of the index
        # against this tuple, so a bucket that is not a hex digit is never queued.
        self.assertEqual(len(streams.BUCKETS), 16)
        self.assertEqual(set(streams.BUCKETS), set("0123456789abcdef"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
