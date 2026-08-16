"""The one audience we may read, and the three ways it can be missing.

`038_audience_profile.sql` allows this module to exist by narrowing `037_creator_scout.sql`
rather than contradicting it: we may not read a stranger's followers, and the roster
artist is not a stranger. Everything here defends the seam between those two sentences.

Three properties are worth more than the rest, and each has a class below.

**An absence must say which absence it is.** Below 100 followers Instagram returns no
breakdown; a revoked token returns none either; a party nobody has fetched has none yet.
Those are three different situations with three different remedies, and if they arrive in
the database as the same nothing, the console shows a shortlist that looks audience-aware
and is not. `demographics_state` is the column that keeps them apart and
`ComposesAnAbsence` proves the composed text says so out loud.

**The threshold is measured, not inferred.** `demographics` decides against a follower
count it already holds rather than against the prose of an error, so no vendor's wording
change can turn "she has 40 followers" into a retry loop. `BelowThreshold` proves it does
not even make the call.

**A share is a share.** Instagram returns absolute counts per bucket and an absolute count
is not comparable across two captures of a growing account, so normalisation happens on
the way in. `Breakdowns` proves the arithmetic and, more importantly, proves that a shape
this parser does not recognise raises instead of yielding an empty audience — a silently
empty breakdown is the claim "she has no listeners anywhere", and it is the wrong one.

Offline throughout — no database, no network, no clock.
"""

from __future__ import annotations

import unittest
import unittest.mock

from spindle import audience
from spindle.audience import (
    NotConfigured, Refused, _breakdown_pairs, _error_code, compose_profile, credentials,
    demographics, geo_rerank,
)


def envelope(breakdown: str, results: list[tuple[str, float]]) -> dict:
    """One `follower_demographics` response, shaped as Meta actually returns it."""
    return {"data": [{
        "name": "follower_demographics",
        "period": "lifetime",
        "total_value": {"breakdowns": [{
            "dimension_keys": [breakdown],
            "results": [{"dimension_values": [value], "value": count}
                        for value, count in results],
        }]},
    }]}


# --------------------------------------------------------------------- the grant

class Credentials(unittest.TestCase):
    def setUp(self) -> None:
        for var in (audience.TOKEN_VAR, audience.USER_VAR):
            self.enterContext(unittest.mock.patch.dict(
                "os.environ", {var: ""}, clear=False))

    def test_refuses_when_neither_is_set(self) -> None:
        with self.assertRaises(NotConfigured) as caught:
            credentials()
        message = str(caught.exception)
        self.assertIn(audience.TOKEN_VAR, message)
        self.assertIn(audience.USER_VAR, message)

    def test_names_only_the_one_that_is_missing(self) -> None:
        with unittest.mock.patch.dict("os.environ", {audience.TOKEN_VAR: "tok"}):
            with self.assertRaises(NotConfigured) as caught:
                credentials()
        self.assertIn(audience.USER_VAR, str(caught.exception))

    def test_says_a_personal_account_cannot_work_at_all(self) -> None:
        """The message has to pre-empt the likeliest wrong theory.

        Somebody whose token fails will try the account they have. Basic Display was shut
        down in December 2024 and a personal account has no path at all, so the error
        that does not say so costs an afternoon.
        """
        with self.assertRaises(NotConfigured) as caught:
            credentials()
        self.assertIn("Professional", str(caught.exception))


# --------------------------------------------------------------------- the threshold

class BelowThreshold(unittest.TestCase):
    def test_does_not_call_instagram_at_all(self) -> None:
        """The whole point of checking `followers_count` first.

        Not "handles the error gracefully" — makes no request. A call that cannot succeed
        still costs rate limit against an account whose limits we do not control, and the
        error it returns would have to be pattern-matched to mean what we already knew.
        """
        def explode(*args, **kwargs):
            raise AssertionError("demographics called Instagram below the threshold")

        with unittest.mock.patch.object(audience, "_get", explode):
            self.assertEqual(demographics("17841400000000000", followers=99), {})

    def test_the_boundary_is_inclusive(self) -> None:
        """100 is enough; Meta's wording is "less than 100" and off-by-one here is a
        silent loss of the entire geographic signal for an account that qualifies."""
        calls: list[str] = []

        def record(path, params, **kwargs):
            calls.append(params["breakdown"])
            return envelope(params["breakdown"], [("US", 1.0)])

        with unittest.mock.patch.object(audience, "_get", record):
            demographics("17841400000000000", followers=100)
        self.assertEqual(sorted(calls), sorted(audience.BREAKDOWNS))


# --------------------------------------------------------------------- the arithmetic

class Breakdowns(unittest.TestCase):
    def test_counts_become_shares_that_sum_to_one(self) -> None:
        pairs = _breakdown_pairs(
            envelope("country", [("US", 60), ("DE", 30), ("GB", 10)]), "country")
        self.assertEqual([value for value, _ in pairs], ["US", "DE", "GB"])
        self.assertAlmostEqual(sum(share for _, share in pairs), 1.0)
        self.assertAlmostEqual(dict(pairs)["US"], 0.6)

    def test_largest_share_first_regardless_of_the_order_sent(self) -> None:
        """The rerank reads the top N, so the sort is load-bearing rather than cosmetic."""
        pairs = _breakdown_pairs(
            envelope("city", [("Berlin", 5), ("Chicago", 80), ("Lisbon", 15)]), "city")
        self.assertEqual([value for value, _ in pairs], ["Chicago", "Lisbon", "Berlin"])

    def test_a_response_with_no_data_array_raises(self) -> None:
        """An unrecognised shape must not become an empty audience.

        `{}` here would be written as "she has no followers in any city", which is a claim
        about the world rather than a gap in our reading of it.
        """
        with self.assertRaises(Refused):
            _breakdown_pairs({"nonsense": True}, "city")

    def test_zero_total_yields_no_segments_rather_than_dividing_by_zero(self) -> None:
        self.assertEqual(_breakdown_pairs(envelope("age", [("25-34", 0)]), "age"), [])

    def test_ignores_malformed_result_entries_without_dropping_good_ones(self) -> None:
        body = envelope("country", [("US", 75)])
        body["data"][0]["total_value"]["breakdowns"][0]["results"].append(
            {"dimension_values": [], "value": 25})
        pairs = _breakdown_pairs(body, "country")
        self.assertEqual(pairs, [("US", 1.0)])


# --------------------------------------------------------------------- error codes

class ErrorCodes(unittest.TestCase):
    def test_reads_metas_code_out_of_the_body(self) -> None:
        """190 arrives under an HTTP 400, so the status alone cannot tell a revoked grant
        from a malformed request."""
        self.assertEqual(
            _error_code('{"error":{"message":"expired","code":190}}'), 190)

    def test_a_body_that_is_not_json_is_not_an_error_to_report(self) -> None:
        self.assertIsNone(_error_code("<html>502</html>"))

    def test_a_json_body_with_no_error_object(self) -> None:
        self.assertIsNone(_error_code('{"data":[]}'))


# --------------------------------------------------------------------- composition

class ComposesAnAudience(unittest.TestCase):
    SNAPSHOT = {
        "follower_count": 4210,
        "demographics_state": "present",
        "unavailable_detail": "",
        "segments": {
            "country": [("US", 0.62), ("DE", 0.20), ("GB", 0.18)],
            "city": [("Chicago, Illinois", 0.22), ("Berlin", 0.11)],
            "age": [("25-34", 0.48), ("18-24", 0.31)],
            "gender": [("F", 0.57), ("M", 0.43)],
        },
        "media": [{"caption": "new one out friday, 124bpm and mean about it"}],
    }

    def test_states_where_the_audience_is(self) -> None:
        text = compose_profile("Amanda Kurt", self.SNAPSHOT)
        self.assertIn("Chicago, Illinois (22%)", text)
        self.assertIn("US (62%)", text)

    def test_carries_the_artists_own_words(self) -> None:
        """Captions are the half of the signal that needs no follower threshold, and the
        half a vector search over counterparties actually matches on."""
        text = compose_profile("Amanda Kurt", self.SNAPSHOT)
        self.assertIn("124bpm", text)

    def test_names_the_artist(self) -> None:
        self.assertIn("Amanda Kurt", compose_profile("Amanda Kurt", self.SNAPSHOT))


class ComposesAnAbsence(unittest.TestCase):
    SNAPSHOT = {
        "follower_count": 40,
        "demographics_state": "below_threshold",
        "unavailable_detail": (
            "the account has 40 followers and Instagram returns no "
            "`follower_demographics` below 100"),
        "segments": {},
        "media": [],
    }

    def test_says_the_breakdown_is_unavailable_and_why(self) -> None:
        text = compose_profile("Amanda Kurt", self.SNAPSHOT)
        self.assertIn("unavailable", text)
        self.assertIn("40 followers", text)

    def test_makes_no_geographic_claim(self) -> None:
        """The property that matters. A profile with no demographics must not read as a
        profile whose audience happens to be everywhere — this text is embedded, and an
        embedding of an invented geography ranks counterparties on a fiction.
        """
        text = compose_profile("Amanda Kurt", self.SNAPSHOT)
        self.assertNotIn("concentrates", text)
        self.assertIn("we do not know", text)


# --------------------------------------------------------------------- the rerank

class GeoRerank(unittest.TestCase):
    """The half of `038` that changes who actually gets emailed."""

    SEGMENTS = {"country": [("US", 0.62), ("DE", 0.20)]}

    def candidates(self):
        return [
            {"id": "a", "name": "Berlin station", "country": "DE", "distance": 0.30},
            {"id": "b", "name": "Chicago station", "country": "US", "distance": 0.34},
            {"id": "c", "name": "Tokyo station", "country": "JP", "distance": 0.32},
        ]

    def test_reorders_neighbours_that_were_already_close(self):
        """The behaviour the feature exists for, asserted as an ordering rather than as
        arithmetic — the same choice `lessons.rerank`'s tests make, because the inversion
        (a lift *reduces* distance) is the one thing easy to get backwards.

        Berlin is the better raw match and the US is where 62% of the audience is, so the
        Chicago station overtakes it. One place, on a two-hundredths gap.
        """
        close = [
            {"id": "a", "name": "Berlin station", "country": "DE", "distance": 0.30},
            {"id": "b", "name": "Chicago station", "country": "US", "distance": 0.31},
        ]
        self.assertEqual([row["id"] for row in geo_rerank(close, self.SEGMENTS)],
                         ["b", "a"])

    def test_does_not_lift_a_poor_match_over_a_good_one(self):
        """The deliberate ceiling, and the reason `AUDIENCE_WEIGHT` is a fraction of the
        distances involved.

        Same three stations, but Chicago is 0.04 behind rather than 0.01. A 62% country is
        worth 0.0496 and still does not close it, so the vector match keeps the lead. This
        test exists because the first version of it asserted the opposite and was wrong:
        an audience signal that could overturn any distance would not be a rerank, it
        would be a filter wearing a rerank's clothes.
        """
        ranked = geo_rerank(self.candidates(), self.SEGMENTS)
        self.assertEqual([row["id"] for row in ranked], ["a", "b", "c"])

    def test_says_why_it_moved(self):
        ranked = geo_rerank(self.candidates(), self.SEGMENTS)
        chicago = next(row for row in ranked if row["id"] == "b")
        applied = [entry for entry in chicago["applied"] if entry["kind"] == "audience"]
        self.assertEqual(len(applied), 1)
        self.assertIn("62%", applied[0]["text"])
        self.assertIn("US", applied[0]["text"])

    def test_a_country_with_no_share_is_not_moved(self):
        ranked = geo_rerank(self.candidates(), self.SEGMENTS)
        tokyo = next(row for row in ranked if row["id"] == "c")
        self.assertEqual(tokyo["adjusted"], 0.32)
        self.assertEqual(tokyo["applied"], [])

    def test_no_segments_leaves_the_order_untouched(self):
        """The contract when demographics are unavailable. Ranking must fall back to the
        vector distance alone, not to an invented neutral geography."""
        ranked = geo_rerank(self.candidates(), {})
        self.assertEqual([row["id"] for row in ranked], ["a", "c", "b"])
        self.assertTrue(all(not row.get("applied") for row in ranked))

    def test_composes_with_an_earlier_lesson_rerank(self):
        """`adjusted` is read when present, and `applied` is appended to rather than
        replaced — a candidate moved by both a lesson and its geography must be able to
        say both, or the console's answer to "why is this third" omits one."""
        candidates = self.candidates()
        candidates[0]["adjusted"] = 0.10
        candidates[0]["applied"] = [{"kind": "lesson", "text": "replies fast",
                                     "shift": 0.20}]
        ranked = geo_rerank(candidates, self.SEGMENTS)
        berlin = next(row for row in ranked if row["id"] == "a")
        self.assertLess(berlin["adjusted"], 0.10)
        kinds = sorted(entry["kind"] for entry in berlin["applied"])
        self.assertEqual(kinds, ["audience", "lesson"])

    def test_a_lift_never_produces_a_negative_distance(self):
        ranked = geo_rerank(
            [{"id": "z", "country": "US", "distance": 0.001}], self.SEGMENTS)
        self.assertGreaterEqual(ranked[0]["adjusted"], 0.0)

    def test_country_matching_is_case_insensitive(self):
        ranked = geo_rerank(
            [{"id": "z", "country": "us", "distance": 0.30}], self.SEGMENTS)
        self.assertLess(ranked[0]["adjusted"], 0.30)


if __name__ == "__main__":
    unittest.main()
