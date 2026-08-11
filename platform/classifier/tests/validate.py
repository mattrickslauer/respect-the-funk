"""Reference-track validation. Runs inside the image; the build is not done until it passes.

    ./tests/validate.sh

Six records spanning six Discogs parent genres. The point is not that a classifier gets
six tracks right — it is that a classifier which has silently broken *cannot* get them
right, because they do not share a genre for it to collapse into.

That failure is not hypothetical. `docs/2026-08-10-masters-and-classification.md` §3a
records an earlier front end, hand-written in numpy against the same effnet weights,
which returned:

    Metallica — Master of Puppets   ->  Electronic---Hardcore    0.129
    John Coltrane — Giant Steps     ->  Electronic---Speedcore   0.147
    Bob Marley — Jamming            ->  Electronic---Electro     0.130
    Daft Punk — Around The World    ->  Electronic---New Beat    0.267

Everything collapsed to Electronic, and the one "correct" answer was a coincidence.
A single-genre test set would have passed that. This one cannot.

Assertions are on the **parent** genre, not the style. The model is meaningfully better
at "this is Hip Hop" than at "this is G-Funk specifically" — `dre-still-dre` picks
`Horrorcore` at 0.215, which is why `handler.STYLE_FLOOR` exists and why the parent is
what gets asserted here. Pinning a test to a sub-genre would make it fail on a model
update that was an improvement.
"""

from __future__ import annotations

import glob
import os
import sys
import unittest

sys.path.insert(0, os.environ.get("LAMBDA_TASK_ROOT", "/var/task"))

import handler  # noqa: E402

REFS = os.environ.get("REFS_DIR", "/refs")

#: slug -> the Discogs parent genre the record obviously belongs to. "Obviously" is the
#: bar on purpose: no judgement calls, nothing that a reasonable person would argue
#: about, so a failure here is the classifier being wrong rather than the taxonomy being
#: debatable.
EXPECTED = {
    "daft-punk-around-the-world":  "Electronic",
    "metallica-master-of-puppets": "Rock",
    "coltrane-giant-steps":        "Jazz",
    "marley-jamming":              "Reggae",
    "dre-still-dre":               "Hip Hop",
    "johnny-cash-folsom":          "Folk, World, & Country",
}


class ReferenceTracks(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = {}
        found = sorted(glob.glob(f"{REFS}/*.mp3"))
        if not found:
            raise unittest.SkipTest(f"no reference audio in {REFS} — run ./fetch_refs.sh")
        for path in found:
            slug = os.path.basename(path)[:-4]
            cls.results[slug] = handler.classify_file(path)

    def test_every_reference_track_is_present(self) -> None:
        """A missing file would otherwise silently shrink the test set — and a test set
        that quietly drops the one track a change broke is worse than no test set."""
        self.assertEqual(set(self.results), set(EXPECTED))

    def test_every_reference_track_lands_in_its_parent_genre(self) -> None:
        for slug, want in EXPECTED.items():
            with self.subTest(track=slug):
                got = self.results[slug]
                self.assertEqual(got["parent"], want,
                                 f"{slug}: top was {got['styles'][0]}")

    def test_the_predictions_are_not_all_the_same_genre(self) -> None:
        """The specific shape of the numpy failure: confident labels, all Electronic.
        Asserted separately from the per-track checks because it is the *pattern* that
        identifies a broken front end, and because this is the assertion that would
        still fail if somebody 'fixed' the tracks one at a time."""
        parents = {r["parent"] for r in self.results.values()}
        self.assertEqual(len(parents), len(EXPECTED),
                         f"predictions collapsed into {parents} — this is what a "
                         f"mismatched mel front end looks like")

    def test_a_confident_track_reports_a_style_and_not_only_a_genre(self) -> None:
        """The three-tier report has to actually reach its top tier, or `STYLE_FLOOR` is
        just suppressing everything and the parent genres above are all it can ever say."""
        confident = [s for s, r in self.results.items() if r["confident"] == "style"]
        self.assertTrue(confident, "no track cleared STYLE_FLOOR")

    def test_the_style_floor_suppresses_a_style_it_is_unsure_of(self) -> None:
        """Dr. Dre — Still D.R.E. is Hip Hop and is not Horrorcore. The model gets the
        genre right and the style wrong at 0.215, and reporting the parent alone is the
        true subset of what it knows.

        Skipped rather than asserted-around if a model update ever makes it confident:
        the behaviour under test is the floor, not this particular track's score.
        """
        got = self.results["dre-still-dre"]
        if got["confidence"] >= handler.STYLE_FLOOR:
            self.skipTest(f"the model is now confident about this track "
                          f"({got['confidence']}) — the floor is untested by it")
        self.assertEqual(got["style"], "", "a sub-floor style was reported anyway")
        self.assertEqual(got["parent"], "Hip Hop")

    def test_silence_is_refused_rather_than_labelled(self) -> None:
        """A 400-way sigmoid always has an argmax. Without a floor, a silent or
        unrecognisable file gets a genre with the same confidence a real record does.

        The fixture is generated by `fetch_refs.sh` on the host, not here: ffmpeg is
        deliberately absent from the runtime image, and an earlier version of this test
        made it itself and skipped when it could not. A skipped floor test reads as a
        pass and leaves the floor untested, which is the failure mode this whole file
        exists to prevent.
        """
        path = f"{REFS}/_silence.wav"
        if not os.path.exists(path):
            self.fail(f"{path} is missing — run ./fetch_refs.sh on a host with ffmpeg")
        got = handler.classify_file(path)
        self.assertEqual(got["confident"], "none",
                         f"silence was classified as {got['styles'][0]}")

    def test_a_file_too_short_to_classify_raises(self) -> None:
        """Below the patch window there is nothing to average, and Essentia would
        otherwise hand the head an empty embedding array."""
        path = f"{REFS}/_tooshort.wav"
        if not os.path.exists(path):
            self.fail(f"{path} is missing — run ./fetch_refs.sh on a host with ffmpeg")
        with self.assertRaises(ValueError):
            handler.classify_file(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
