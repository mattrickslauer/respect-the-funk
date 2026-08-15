"""The vocabularies, and the one invariant that ties them to the schema.

`source_manifest` is seeded in SQL and `Platform` is declared in Python, and nothing
in either file mentions the other. That gap is a real bug that already happened:
`deezer` was seeded as a source while `Platform` had never heard of it, so the
console's own form rejected the platform the database was advertising.

Parsing the migration is deliberate. Reading the live cluster would make this test
need a database and a network, and it would pass or fail depending on which cluster
somebody happened to point at.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from spindle.domain import ARTIST_STATUSES, ArtistType, Platform, ProfileMode

SCHEMA = Path(__file__).resolve().parents[2] / "schema" / "005_party_first.sql"


def seeded_platforms() -> set[str]:
    """The platform of every `source_manifest` row the migration inserts.

    Each seed row starts `('name', 'adapter', ARRAY[...`, which is specific enough
    to match without pulling in the ARRAY literals that follow.
    """
    sql = SCHEMA.read_text()
    insert = sql.split("INSERT INTO source_manifest", 1)[1]
    return set(re.findall(r"\(\s*'([a-z_]+)',\s*'[a-z_]+',\s*ARRAY", insert))


class SchemaAgreesWithVocabulary(unittest.TestCase):
    def test_migration_seeds_some_sources(self) -> None:
        # Guards the parser above: a regex that silently matches nothing would make
        # every other assertion here vacuously true.
        self.assertGreaterEqual(len(seeded_platforms()), 5, seeded_platforms())

    def test_every_seeded_source_is_a_known_platform(self) -> None:
        unknown = seeded_platforms() - {p.value for p in Platform}
        self.assertEqual(unknown, set(),
                         f"seeded in 005 but missing from Platform: {sorted(unknown)}")


class Vocabularies(unittest.TestCase):
    def test_platform_values_are_slugs(self) -> None:
        for p in Platform:
            self.assertRegex(p.value, r"^[a-z][a-z0-9_]*$")
            self.assertTrue(p.label.strip(), f"{p.value} has no label")

    def test_platform_parse_is_forgiving_about_case_and_space(self) -> None:
        self.assertIs(Platform.parse("  Spotify "), Platform.SPOTIFY)
        self.assertIsNone(Platform.parse("myspace"))

    def test_profile_modes_are_the_three_the_schema_allows(self) -> None:
        self.assertEqual({m.value for m in ProfileMode},
                         {"owned", "unowned", "absent"})

    def test_absent_is_a_mode_not_a_missing_row(self) -> None:
        # The distinction the whole optionality story rests on: an artist with no
        # TikTok account is still worth searching TikTok for.
        self.assertIsNotNone(ProfileMode.parse("absent"))

    def test_artist_statuses_and_types_are_disjoint_vocabularies(self) -> None:
        # A status accidentally offered as a type would silently reclassify an act.
        self.assertEqual(set(ARTIST_STATUSES) & ArtistType.values(), set())


if __name__ == "__main__":
    unittest.main()
