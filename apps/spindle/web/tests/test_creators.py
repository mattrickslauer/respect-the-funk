"""The scout paste boundary, and what a creator's profile is composed from.

`037_creator_scout.sql` decided that the adapter for UGC creators is a person: Pillar 10
§4's *no scraper, ever* stands, and Pillar 10 §5c's manual sound-page browsing is the
compliant path. That makes the paste box the entry point for an entire counterparty
class, and an entry point a human types into is exactly where a lenient parser does the
most damage — a silently-skipped line is a creator who never gets pitched and nobody ever
finds out.

So every class here asserts the same discipline `harvested.py` established: a field that
cannot be read raises `ScoutInvalid` naming the line, rather than defaulting to the most
confident answer available. `views: ` empty means *not read* and becomes NULL; `views:
lots` is a parse failure and stops the whole paste. The difference between those two is
the entire point.

The composition tests exist for a different reason: `profile_creator` writes the text
that becomes a creator's position in the vector index, and `037`'s header explains why
that text has to come from `sound_usage` rows rather than from prose. If composition ever
starts inventing a clause, a creator's shortlist rank stops being traceable to a row
somebody can check.

Offline throughout — no database, no network, no clock.
"""

from __future__ import annotations

import unittest

from spindle.creators import ScoutInvalid, compose_profile, parse_scout_paste


# --------------------------------------------------------------------- parsing

class Parsing(unittest.TestCase):
    LINE = "@dancewithmia | tiktok | Chris Lake — Turn Off The Lights | 84000 | 21000 | tech house"

    def test_reads_a_complete_line(self) -> None:
        rows = parse_scout_paste(self.LINE)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.handle, "dancewithmia")
        self.assertEqual(row.platform, "tiktok")
        self.assertEqual(row.sound_ref, "Chris Lake — Turn Off The Lights")
        self.assertEqual(row.view_count, 84000)
        self.assertEqual(row.creator_median_views, 21000)
        self.assertEqual(row.asserted_genre, "tech house")

    def test_strips_the_at_sign_because_both_forms_get_pasted(self) -> None:
        #: A scout copying from a profile page gets `@handle`; copying from a URL gets
        #: `handle`. Storing both would make the same creator two parties.
        with_at = parse_scout_paste(self.LINE)[0]
        without = parse_scout_paste(self.LINE.replace("@dancewithmia", "dancewithmia"))[0]
        self.assertEqual(with_at.handle, without.handle)

    def test_handle_is_lowercased_for_the_same_reason(self) -> None:
        row = parse_scout_paste(self.LINE.replace("@dancewithmia", "@DanceWithMia"))[0]
        self.assertEqual(row.handle, "dancewithmia")

    def test_the_three_required_fields_are_enough(self) -> None:
        rows = parse_scout_paste("@mia | tiktok | Some Sound")
        self.assertEqual(len(rows), 1)
        #: Absent is not zero. A view count nobody read must not become a view count of
        #: nought, because `performed_well` would then read as "underperformed".
        self.assertIsNone(rows[0].view_count)
        self.assertIsNone(rows[0].creator_median_views)
        self.assertEqual(rows[0].asserted_genre, "")

    def test_blank_lines_and_comments_are_skipped(self) -> None:
        text = f"# from the Turn Off The Lights sound page, 2026-08-15\n\n{self.LINE}\n\n"
        self.assertEqual(len(parse_scout_paste(text)), 1)

    def test_a_line_missing_the_sound_raises_and_names_the_line(self) -> None:
        with self.assertRaises(ScoutInvalid) as caught:
            parse_scout_paste(f"{self.LINE}\n@someone | tiktok")
        message = str(caught.exception)
        self.assertIn("line 2", message)
        #: The message has to say what a correct line looks like. A scout pasting fifty
        #: rows at 1am should not have to open the source to find the column order.
        self.assertIn("handle", message)

    def test_an_unknown_platform_raises_rather_than_guessing_tiktok(self) -> None:
        with self.assertRaises(ScoutInvalid) as caught:
            parse_scout_paste("@mia | twitter | Some Sound")
        self.assertIn("twitter", str(caught.exception))

    def test_an_unreadable_view_count_raises_rather_than_becoming_zero(self) -> None:
        #: The single most important assertion in this file. `| lots |` becoming `0`
        #: would rank a creator as having flopped on a sound they may have carried.
        with self.assertRaises(ScoutInvalid) as caught:
            parse_scout_paste("@mia | tiktok | Some Sound | lots | 21000")
        self.assertIn("lots", str(caught.exception))

    def test_an_empty_view_count_is_not_read_rather_than_unreadable(self) -> None:
        rows = parse_scout_paste("@mia | tiktok | Some Sound |  | 21000")
        self.assertIsNone(rows[0].view_count)
        self.assertEqual(rows[0].creator_median_views, 21000)

    def test_a_negative_view_count_raises(self) -> None:
        with self.assertRaises(ScoutInvalid):
            parse_scout_paste("@mia | tiktok | Some Sound | -4 | 21000")

    def test_thousands_separators_survive_because_people_paste_them(self) -> None:
        rows = parse_scout_paste("@mia | tiktok | Some Sound | 84,000 | 21000")
        self.assertEqual(rows[0].view_count, 84000)

    def test_an_empty_paste_is_no_rows_and_not_an_error(self) -> None:
        #: Distinct from a malformed paste. Submitting an empty box is a no-op the
        #: console can report plainly; it is not something to raise about.
        self.assertEqual(parse_scout_paste("   \n\n# nothing here\n"), [])

    def test_a_handle_with_no_sound_ref_raises(self) -> None:
        with self.assertRaises(ScoutInvalid):
            parse_scout_paste("@mia | tiktok |    ")

    def test_the_same_creator_may_appear_on_several_lines(self) -> None:
        #: One creator, two sounds, and both are evidence. Deduplication is
        #: `sound_usage_once`'s job in the database, not the parser's.
        rows = parse_scout_paste(
            "@mia | tiktok | Sound A | 84000 | 21000\n"
            "@mia | tiktok | Sound B | 12000 | 21000")
        self.assertEqual([r.sound_ref for r in rows], ["Sound A", "Sound B"])
        self.assertEqual({r.handle for r in rows}, {"mia"})


# ----------------------------------------------------------------- performance

class Performance(unittest.TestCase):
    """Pillar 10 §7: rank by *"used an adjacent artist's sound and beat their own
    median"*, not by demographics. That judgement needs both numbers, and must abstain
    rather than guess when it has one or neither.
    """

    def test_beating_the_median_is_performing_well(self) -> None:
        row = parse_scout_paste("@mia | tiktok | S | 84000 | 21000")[0]
        self.assertIs(row.performed_well, True)

    def test_missing_the_median_is_not_performing_well(self) -> None:
        row = parse_scout_paste("@mia | tiktok | S | 8000 | 21000")[0]
        self.assertIs(row.performed_well, False)

    def test_without_both_numbers_it_abstains_rather_than_deciding(self) -> None:
        #: `None` is a third answer and the console must render it as one. A creator
        #: whose numbers nobody read has not underperformed.
        self.assertIsNone(parse_scout_paste("@mia | tiktok | S")[0].performed_well)
        self.assertIsNone(parse_scout_paste("@mia | tiktok | S | 84000")[0].performed_well)


# ------------------------------------------------------------------ composing

class Composing(unittest.TestCase):
    MEASURED = {"sound_ref": "Turn Off The Lights", "asserted_genre": "",
                "recording_id": "r-1", "genres": ["tech house"], "bpm": 124.0,
                "view_count": 84000, "creator_median_views": 21000}
    ASSERTED = {"sound_ref": "Some Other Track", "asserted_genre": "disco house",
                "recording_id": None, "genres": [], "bpm": None,
                "view_count": None, "creator_median_views": None}

    def test_composes_from_measured_audio_facts(self) -> None:
        body = compose_profile("dancewithmia", [self.MEASURED])
        self.assertIn("dancewithmia", body)
        self.assertIn("tech house", body)
        self.assertIn("124", body)

    def test_measured_and_asserted_genres_are_not_merged_into_one_clause(self) -> None:
        #: `SCOPE-RESET.md` §2a rule 1, and Pillar 10 §7's standing warning: never render
        #: an estimated value next to a measured one without distinction. The composed
        #: text is read by a human in `party_document` as well as embedded, so the
        #: distinction has to survive into the prose.
        body = compose_profile("mia", [self.MEASURED, self.ASSERTED])
        measured_at = body.index("tech house")
        asserted_at = body.index("disco house")
        self.assertNotEqual(measured_at, asserted_at)
        self.assertIn("measured", body.lower())
        self.assertIn("reported", body.lower())

    def test_a_creator_with_no_sounds_composes_nothing(self) -> None:
        #: `profile_party` refuses to embed a name alone — *"embedding it would put the
        #: artist somewhere arbitrary in the vector space rather than nowhere. Nowhere is
        #: better."* A creator with no sound usage is the same case.
        self.assertEqual(compose_profile("mia", []), "")

    def test_a_creator_with_only_a_sound_name_still_composes(self) -> None:
        #: The weakest legitimate case: we know they posted over something, and the name
        #: of the sound is itself signal in the embedding space.
        body = compose_profile("mia", [{**self.ASSERTED, "asserted_genre": ""}])
        self.assertIn("Some Other Track", body)

    def test_the_performance_clause_appears_only_with_both_numbers(self) -> None:
        with_both = compose_profile("mia", [self.MEASURED])
        without = compose_profile("mia", [self.ASSERTED])
        self.assertIn("median", with_both.lower())
        self.assertNotIn("median", without.lower())

    def test_composition_invents_no_audience_claim(self) -> None:
        #: Pillar 10 §1: there is no honest source for creator audience demographics, and
        #: `037` stores none. If a clause about who *follows* this creator ever appears
        #: here it came from nowhere, which is the failure this asserts against.
        body = compose_profile("mia", [self.MEASURED, self.ASSERTED]).lower()
        for invented in ("audience is", "followers are", "aged", "% female", "% male"):
            self.assertNotIn(invented, body)


if __name__ == "__main__":
    unittest.main()
