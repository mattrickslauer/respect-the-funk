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

from spindle import profiles


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


# --------------------------------------------------------------------------- free text

class TheProseSlot(unittest.TestCase):
    """The slot exists so that no caller appends after `compose`.

    `podcastindex` was the fourth module to compose its own embedded text — it called
    `compose` for the structured half and then glued the publisher's blurb on the end,
    because there was nowhere to put it. That is the drift this package was created to
    stop, and the tests below are less about the string that comes out than about the
    rules the string has to obey now that free text is admitted at all.

    Each of these is an adversarial case: prose that is boilerplate, prose that is a
    name, prose that is far too long, and prose that is nothing. A blurb is the richest
    signal any source here produces and it arrives from the least trustworthy place in
    the pipeline — a marketing department.
    """

    #: What a decent podcast description looks like once the adapter has stripped markup.
    BLURB = "New deep house and garage every Friday."

    #: Eighty sentences of show notes — the verbose publisher the cap exists for.
    LONG = " ".join(f"Sentence {i} about soul and funk records." for i in range(80))

    def test_prose_reaches_the_text_and_the_genres_still_lead(self) -> None:
        """The point of the slot. The structured half is unchanged and comes first — a
        composed prefix that shifts when a blurb is present would make two rows
        undiffable, which is how the original drift stayed hidden."""
        without = profiles.compose(role="podcast", genres=["music"], names=[])
        with_prose = profiles.compose(role="podcast", genres=["music"],
                                      prose=self.BLURB, names=[])
        self.assertTrue(with_prose.startswith(without))
        self.assertIn("deep house", with_prose)

    def test_a_sentence_carrying_a_link_is_dropped_whole(self) -> None:
        """Rule 1, arriving by a different door. "Subscribe at patreon.com/…" is not
        boilerplate this codebase writes, but it is boilerplate tens of thousands of
        feeds share, and half of it — the link stripped out, the wreckage kept — is
        worse than none of it."""
        text = profiles.compose(
            role="podcast", genres=["music"], names=[],
            prose="Grime and UK drill. Subscribe at patreon.com/theshow for bonus mixes.")
        self.assertIn("Grime and UK drill.", text)
        self.assertNotIn("Subscribe", text)
        self.assertNotIn("patreon", text)

    def test_every_shape_of_contact_detail_takes_its_sentence_with_it(self) -> None:
        for noise in ("Visit https://example.com/join today.",
                      "Find us at www.example.com.",
                      "Write to sales@example.com for rates.",
                      "Follow @theshow on every platform.",
                      "More at example.co.uk.",
                      "Merch at example.shop/tees."):
            with self.subTest(noise=noise):
                text = profiles.compose(role="podcast", genres=["music"], names=[],
                                        prose=f"Soul and funk. {noise}")
                self.assertIn("Soul and funk.", text)
                self.assertNotIn("example", text)

    def test_a_blurb_that_is_only_boilerplate_leaves_nothing_behind(self) -> None:
        """Not a stub, not a fragment, not a trailing space — nothing. The composed text
        must be byte-identical to the one this show would have got with no blurb."""
        text = profiles.compose(
            role="podcast", genres=["music"], names=[],
            prose="Subscribe at https://example.com/join. Follow us @theshow.")
        self.assertEqual(text, profiles.compose(role="podcast", genres=["music"],
                                                names=[]))

    def test_an_all_boilerplate_blurb_with_no_genres_still_sinks(self) -> None:
        """Rule 3 survives contact with the slot. The row that knows nothing about itself
        gets `UNKNOWN` and ranks last, rather than floating on a call to action."""
        text = profiles.compose(role="podcast", names=[], prose="Follow us @theshow.")
        self.assertEqual(text, f"{profiles.UNKNOWN} A podcast.")

    def test_prose_replaces_unknown_rather_than_following_it(self) -> None:
        """A show with no categories but a paragraph about what it plays *does* have
        documented programming. Saying "Programming not documented." in front of that
        paragraph would be a false sentence, and — being present on every such row — an
        invariant one, which is rules 3 and 1 broken by the same clause."""
        text = profiles.compose(role="podcast", names=[],
                                prose="Long-form interviews with jazz players.")
        self.assertNotIn(profiles.UNKNOWN, text)
        self.assertTrue(text.startswith("Long-form interviews with jazz players."))

    # ----------------------------------------------------------- rule 2, inside prose

    def test_a_multi_token_name_is_excised_from_the_prose(self) -> None:
        """The failure rule 2 was written about, reintroduced through the one input that
        arrives with names already in it. A blurb opening "Deep House Weekly is…" puts
        the show's name in the vector as surely as composing it in would."""
        text = profiles.compose(
            role="podcast", genres=["music"], names=["Deep House Weekly", "Jo Presenter"],
            prose="Deep House Weekly is an hour with Jo Presenter. Expect bassline.")
        self.assertNotIn("Deep House Weekly", text)
        self.assertNotIn("Jo Presenter", text)
        self.assertIn("Expect bassline.", text)

    def test_excision_is_case_insensitive(self) -> None:
        text = profiles.compose(role="podcast", genres=["music"],
                                names=["Deep House Weekly"],
                                prose="DEEP HOUSE WEEKLY brings you bassline.")
        self.assertNotIn("DEEP HOUSE WEEKLY", text)
        self.assertIn("bassline", text)

    def test_a_single_token_name_is_left_alone(self) -> None:
        """Deliberate, and the docstring argues it: shows are called "Jazz", "Motown" and
        "Dubstep", and excising a one-word name would delete the genre word from every
        sentence that used it — destroying the best signal in the document in the name of
        protecting it. One token in a paragraph is not the condition under which rule 2's
        failure happened; a whole document that was only a name is."""
        text = profiles.compose(role="podcast", genres=["music"], names=["Jazz"],
                                prose="Jazz is a show about jazz.")
        self.assertIn("jazz", text.lower())

    def test_a_sentence_that_was_only_a_name_is_dropped(self) -> None:
        """What survives excision has to still be text. "Deep House Weekly." reduces to a
        full stop, and a lone punctuation mark on every excised row is exactly the shared
        token rule 1 forbids — small, but rule 1 has no size exemption."""
        text = profiles.compose(role="podcast", genres=["music"],
                                names=["Deep House Weekly"],
                                prose="Deep House Weekly. Bassline and garage.")
        self.assertEqual(text, profiles.compose(role="podcast", genres=["music"],
                                                names=[], prose="Bassline and garage."))

    def test_prose_without_names_raises_rather_than_embedding_a_name(self) -> None:
        """No silent fallback. A caller who has not thought about whether their paragraph
        opens with a person's name finds out here, not in a shortlist six weeks later."""
        with self.assertRaises(ValueError) as caught:
            profiles.compose(role="podcast", genres=["music"], prose=self.BLURB)
        self.assertIn("names", str(caught.exception))

    def test_an_empty_names_list_is_an_answer_and_not_an_omission(self) -> None:
        """`names=[]` says "this source has nothing to subtract". It has to be typed,
        which is the whole difference between it and a default."""
        text = profiles.compose(role="podcast", genres=["music"], prose=self.BLURB,
                                names=[])
        self.assertIn("deep house", text)

    # --------------------------------------------------------------- the length cap

    def test_a_verbose_show_cannot_dominate_its_own_vector(self) -> None:
        """The trade the cap buys. Show notes run to thousands of words of episode lists;
        uncapped, the genre sentence is noise in its own document."""
        text = profiles.compose(
            role="podcast", genres=["music"], names=[],
            prose=self.LONG)
        head = profiles.compose(role="podcast", genres=["music"], names=[])
        self.assertLessEqual(len(text) - len(head) - 1, profiles.PROSE_CHARS)

    def test_the_cut_lands_on_a_sentence_boundary(self) -> None:
        """Whole sentences only. These models embed sentences, and a clause severed
        mid-phrase embeds as a fragment."""
        text = profiles.compose(
            role="podcast", genres=["music"], names=[],
            prose=self.LONG)
        self.assertTrue(text.endswith("records."))

    def test_truncation_is_not_marked(self) -> None:
        """No ellipsis. A "…" on every truncated row is a token thousands of documents
        would share, which is rule 1 reintroduced by the mechanism meant to serve it."""
        text = profiles.compose(
            role="podcast", genres=["music"], names=[],
            prose=self.LONG)
        self.assertNotIn("…", text)
        self.assertNotIn("...", text)

    def test_a_run_on_paragraph_is_cut_at_a_word_boundary(self) -> None:
        """A publisher who wrote six hundred characters without a full stop. There is no
        sentence boundary to cut on, and the choice is between dropping a whole good
        description and severing a word; neither is good and the second is less bad."""
        text = profiles.compose(role="podcast", genres=["music"], names=[],
                                prose="funk " * 200)
        head = profiles.compose(role="podcast", genres=["music"], names=[])
        said = text[len(head) + 1:]
        self.assertLessEqual(len(said), profiles.PROSE_CHARS)
        self.assertTrue(said.endswith("funk"), "a word was severed")

    def test_a_short_blurb_is_kept_whole_rather_than_floored(self) -> None:
        """There is deliberately no minimum length. A floor set anywhere useful would
        drop "Jazz radio." — three words that are the best signal that show has — and the
        boilerplate a floor would catch is too short to dilute anything."""
        text = profiles.compose(role="podcast", genres=["music"], names=[],
                                prose="Jazz radio.")
        self.assertIn("Jazz radio.", text)

    # ------------------------------------------------------------- the rebuild path

    def test_the_rebuild_path_carries_the_blurb_too(self) -> None:
        """The bug this file was created about, in its second form. `compose` gaining a
        slot that `from_facts` does not fill means `--recompose-profiles` rebuilds every
        podcast without its blurb — silently, successfully, and losing the best signal
        the source has."""
        text = profiles.from_facts("Deep House Weekly", {
            "role": "podcast", "genre": "music",
            "description": "New deep house and garage every Friday."})
        self.assertIn("deep house and garage", text)

    def test_the_rebuild_path_excises_the_name_and_the_host(self) -> None:
        """`from_facts` takes the name so that rule 2 is enforced in both directions: it
        is never added to the text, and it is taken out of the text the publisher
        wrote."""
        text = profiles.from_facts("Deep House Weekly", {
            "role": "podcast", "genre": "music", "host": "Jo Presenter",
            "description": "Deep House Weekly is hosted by Jo Presenter. Expect garage."})
        self.assertNotIn("Deep House Weekly", text)
        self.assertNotIn("Jo Presenter", text)
        self.assertIn("Expect garage.", text)

    def test_a_party_with_no_description_fact_composes_as_before(self) -> None:
        """A radio station has no blurb and must be unaffected by any of this."""
        text = profiles.from_facts("WRIU", {"role": "radio station", "genre": "jazz"})
        self.assertEqual(text, profiles.compose(role="radio station", genres=["jazz"],
                                                names=[]))


if __name__ == "__main__":
    unittest.main()
