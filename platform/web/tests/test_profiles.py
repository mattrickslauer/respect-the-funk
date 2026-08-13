"""The composer, and specifically the rebuild path nobody was testing.

`profiles.py` exists because three modules were composing embedded text independently and
drifting. It fixed that for the *write* path — `fcc`, `radiobrowser` and `podcastindex`
all call `compose` now. What it did not have was a single test of `from_facts`, which is
the *rebuild* path: `ingest.recompose_profiles` calls it for every counterparty in the
tenant and replaces the text their vectors were built from.

That gap had a cost. `compose` used to default `role="radio station"`, which was correct
for as long as radio stations were the only counterparty and became wrong the moment
`podcastindex` landed — not at ingest, where `profile_text` passes the right value, but on
the next `--recompose-profiles`, which rebuilds from facts and had no role to pass. Every
podcast would have been silently described as a radio station, and its blurb dropped.

Nothing would have failed. The vectors would have re-embedded, the shortlist would have
returned rows, and the only way to notice was to read the source text of two vectors side
by side — which is exactly how the original drift was found, and exactly what this module
was written to stop happening again.

So these tests are not about the default. They are about the rebuild path having any
coverage at all, and the two properties that matter there: a role that is wrong is worse
than a rebuild that refuses, and a rebuild must never invent what kind of thing it is
describing.
"""

from __future__ import annotations

import unittest

from rtf_platform import profiles


class ComposeRequiresARole(unittest.TestCase):

    def test_compose_cannot_be_called_without_a_role(self) -> None:
        """The parameter has no default, so the failure is at the call and not in the
        output. A default that is right for today's callers and wrong for tomorrow's is
        the shape of bug this codebase refuses on principle — see `013_lease_token.sql`
        making the same argument about worker names."""
        with self.assertRaises(TypeError):
            profiles.compose(genres=["jazz"])  # type: ignore[call-arg]

    def test_the_role_reaches_the_text(self) -> None:
        for role in ("radio station", "podcast"):
            with self.subTest(role=role):
                text = profiles.compose(role=role, genres=["jazz"])
                self.assertIn(role, text)

    def test_a_podcast_is_never_described_as_a_radio_station(self) -> None:
        """The regression this file was added for, stated as a property rather than as a
        story about how it happened."""
        text = profiles.compose(role="podcast", genres=["jazz"], station_kind="interview")
        self.assertIn("podcast", text)
        self.assertNotIn("radio station", text)


class RebuildFromFacts(unittest.TestCase):

    #: A party's facts as `recompose_profiles` assembles them: dimension -> value_text.
    RADIO = {"role": "radio station", "genre": "jazz, soul",
             "market": "Providence, RI", "frequency_mhz": "88.1",
             "licensee": "University of Rhode Island"}
    PODCAST = {"role": "podcast", "genre": "music, commentary", "language": "en"}

    def test_a_radio_station_rebuilds_as_one(self) -> None:
        text = profiles.from_facts("WRIU", self.RADIO)
        self.assertIn("radio station", text)
        self.assertIn("jazz", text)

    def test_a_podcast_rebuilds_as_one(self) -> None:
        """The whole point. Before `role` was a fact, this returned text calling the show
        a radio station, and nothing anywhere failed."""
        text = profiles.from_facts("Some Show", self.PODCAST)
        self.assertIn("podcast", text)
        self.assertNotIn("radio station", text)

    def test_a_party_with_no_role_fact_refuses_rather_than_guessing(self) -> None:
        """A guess here is unrecoverable in the way that matters: it produces plausible
        text, embeds cleanly, and is wrong in a direction nobody looks. Refusing names the
        party so an operator can go and fix the data rather than the symptom."""
        facts = {k: v for k, v in self.PODCAST.items() if k != "role"}
        with self.assertRaises(ValueError) as caught:
            profiles.from_facts("Some Show", facts)
        message = str(caught.exception)
        self.assertIn("Some Show", message, "the error must name the party")
        self.assertIn("role", message)

    def test_an_empty_role_is_treated_as_absent(self) -> None:
        """`party_fact.value_text` is `STRING NOT NULL DEFAULT ''`, so a row written with
        no value arrives here as the empty string rather than as a missing key. Both mean
        the same thing and both must refuse — otherwise the guard is bypassed by the one
        shape the schema makes easiest to produce."""
        for empty in ("", "   "):
            with self.subTest(value=repr(empty)):
                with self.assertRaises(ValueError):
                    profiles.from_facts("Some Show", {**self.PODCAST, "role": empty})

    def test_the_name_stays_out_of_the_text(self) -> None:
        """Rule 2 from the module docstring, and the reason shortlisting "Hallow Youth"
        used to return "Halloween Radio". `from_facts` takes the name precisely so that a
        reader is confronted with this; assert it rather than trusting the comment."""
        text = profiles.from_facts("Halloween Radio", {**self.RADIO, "genre": "jazz"})
        self.assertNotIn("Halloween", text)


if __name__ == "__main__":
    unittest.main()
