"""Tempo measurement, and the abstention that keeps a guess out of the shortlist.

    python -m unittest discover apps/spindle/web/tests

Offline and synthetic throughout. A click train at a known tempo is a stronger test than
a real record, because with a real record you are asserting against somebody's opinion of
what the BPM is; here the ground truth is the thing that generated the signal.

`numpy` is imported by `spindle.audio` lazily and is **not** in `requirements.txt`,
so these skip where it is absent — which is the deployed Lambda, and deliberately so.
The agent that uses this module is drained by a worker, not served by the function.

The test that matters most is `test_a_dotted_quarter_alias_abstains`. Every other case
here checks that a number is right; that one checks that a *wrong* number is never
emitted as a right one, which is the failure mode this module was rewritten to close
after its first run handed an electro single "downtempo, trip hop, chillout".
"""

from __future__ import annotations

import math
import unittest

try:
    import numpy as np

    HAVE_NUMPY = True
except ImportError:  # pragma: no cover - the Lambda path
    HAVE_NUMPY = False

from spindle import audio


def _clicks(bpm: float, seconds: float = 20.0, *, jitter: float = 0.0,
            accent_every: int = 0) -> "np.ndarray":
    """A click train at `bpm`: short decaying bursts on the beat, silence between.

    `accent_every` makes every nth click louder, which is how a real arrangement implies
    a slower pulse on top of a faster one — the structure that creates harmonic rivals.
    """
    rng = np.random.default_rng(0)
    x = np.zeros(int(audio.SR * seconds), dtype=np.float32)
    period = 60.0 / bpm
    click = (np.exp(-np.linspace(0, 12, 400)) *
             np.sin(np.linspace(0, 180, 400))).astype(np.float32)
    n = 0
    t = 0.0
    while t < seconds - 0.1:
        i = int(t * audio.SR)
        gain = 1.0
        if accent_every and n % accent_every == 0:
            gain = 2.5
        x[i:i + len(click)] += click * gain
        n += 1
        t += period * (1.0 + (rng.normal(0, jitter) if jitter else 0.0))
    return x


@unittest.skipUnless(HAVE_NUMPY, "numpy absent — this module never runs in the Lambda")
class Tempo(unittest.TestCase):

    def _bpm(self, x) -> tuple[float, float, list[float]]:
        return audio.tempo(audio.onset_envelope(x))

    def test_a_clean_click_train_measures_its_own_tempo(self):
        for truth in (100.0, 125.0, 140.0):
            with self.subTest(bpm=truth):
                bpm, confidence, _ = self._bpm(_clicks(truth))
                self.assertAlmostEqual(bpm, truth, delta=2.0)
                self.assertGreater(confidence, 0.15)

    def test_a_tempo_outside_the_window_folds_by_octave(self):
        """200 BPM has no comb in a 70–180 sweep; its half at 100 does, and folding is
        what makes the reported answer single-valued rather than absent."""
        bpm, _, _ = self._bpm(_clicks(200.0))
        self.assertAlmostEqual(bpm, 100.0, delta=2.0)

    def test_a_real_signal_clears_the_floor_a_noise_signal_cannot(self):
        """The two ends of the confidence scale, asserted together so the gap between
        them stays a gap. Measured `best/mean`: noise 1.80, clicks 16.7–19.0."""
        rng = np.random.default_rng(2)
        noise = rng.normal(0, 1, audio.SR * 20).astype(np.float32)
        _, quiet, _ = self._bpm(noise)
        _, loud, _ = self._bpm(_clicks(125.0))
        self.assertLess(quiet, 0.15)
        self.assertGreater(loud, 0.5)

    def test_confidence_is_low_when_there_is_no_pulse(self):
        """White noise has no periodic structure, so no comb should stand out. If this
        ever passes the floor, `style_terms` starts inventing a vocabulary from noise."""
        rng = np.random.default_rng(1)
        noise = rng.normal(0, 1, audio.SR * 20).astype(np.float32)
        _, confidence, _ = self._bpm(noise)
        self.assertLess(confidence, 0.15)

    # -------------------------------------------------------------- the abstention

    def test_an_accented_pulse_still_reports_the_beat_not_the_bar(self):
        """126 accented every third beat implies a 84 BPM pulse on top of the 126 grid.
        The comb should still report the beat: measured, 84 scores 0.597 of 126's, which
        is not a tie and should not be treated as one."""
        bpm, _, _ = self._bpm(_clicks(126.0, accent_every=3))
        self.assertAlmostEqual(bpm, 126.0, delta=2.0)

    def test_a_harmonic_rival_is_reported_when_the_scores_actually_tie(self):
        """The abstention path, exercised where the ambiguity is real rather than
        assumed. A plain click train ties its own half-tempo almost exactly — 5.297 at 70
        against 5.282 at 140 — which is precisely the situation `_ALIAS_MARGIN` exists
        for. The octave tie-break resolves the *reported* tempo upward; the rival is
        still reported, because a tie is a fact about the method and hiding it would put
        this module back where it started.

        `Denial (Radio Edit)` measured 84.0 with a rival at 126.0 on the first real run,
        and `style_terms` refusing that case is the reason this test exists.
        """
        bpm, confidence, rivals = self._bpm(_clicks(140.0))
        self.assertAlmostEqual(bpm, 140.0, delta=2.0,
                               msg="the octave tie-break did not prefer the faster grid")
        self.assertTrue(rivals, "a near-exact octave tie reported no rival")
        pair = sorted([bpm] + rivals)
        self.assertTrue(
            any(math.isclose(pair[-1] / pair[0], r, rel_tol=0.06)
                for r in (1.5, 2.0, 3.0)),
            f"rivals {pair} are not harmonically related, so this is a different bug")
        self.assertEqual(audio.style_terms(bpm, confidence, rivals), [],
                         "an ambiguous tempo produced style terms anyway")


@unittest.skipUnless(HAVE_NUMPY, "numpy absent")
class StyleTerms(unittest.TestCase):
    """`style_terms` is the only thing here whose output reaches a search query, so its
    refusals matter more than its answers."""

    def test_a_confident_unambiguous_tempo_produces_terms(self):
        self.assertIn("house", audio.style_terms(126.0, 0.9, []))

    def test_low_confidence_produces_nothing(self):
        self.assertEqual(audio.style_terms(126.0, 0.05, []), [])

    def test_a_rival_produces_nothing_however_confident(self):
        """Confidence and ambiguity are different failures. A comb can be *certain* about
        a grid and still not know whether that grid is the beat or every third beat."""
        self.assertEqual(audio.style_terms(126.0, 1.0, [84.0]), [])

    def test_terms_are_capped(self):
        self.assertLessEqual(len(audio.style_terms(128.0, 1.0, [], limit=4)), 4)

    def test_every_band_carries_at_least_one_term(self):
        """A band with no vocabulary is a tempo range that silently yields nothing, which
        would read as abstention and is actually an empty table row."""
        for lo, hi, terms in audio._BANDS:
            with self.subTest(band=(lo, hi)):
                self.assertTrue(terms)

    def test_the_bands_cover_the_search_window_without_a_hole(self):
        """Sorted by start, each band must begin before the previous one ends. A gap is a
        tempo that measures cleanly and maps to nothing, which is indistinguishable from
        an abstention to every caller."""
        bands = sorted(audio._BANDS)
        self.assertLessEqual(bands[0][0], audio.BPM_LO)
        self.assertGreaterEqual(bands[-1][1], audio.BPM_HI)
        for (_, prev_hi, _), (nxt_lo, _, _) in zip(bands, bands[1:]):
            self.assertLessEqual(nxt_lo, prev_hi, "a tempo band gap leaves a dead range")


class ProvenanceShape(unittest.TestCase):
    """No numpy needed: the contract `measure` promises its callers.

    `harvested.py` exists because callers used to write `.get("provenance", "measured")`
    and turn an absent label into the highest-trust one. The shape below is what stops a
    caller doing that here — a style term is never reachable from the `measured` key.
    """

    def test_the_measured_and_inferred_keys_do_not_overlap(self):
        import inspect

        # `_facts`, not `measure`: both entry points delegate the dict to it, so this is
        # where the split has to hold. Reading `measure` would now read a one-line
        # delegation and pass vacuously.
        source = inspect.getsource(audio._facts)
        measured_block = source.split('"measured"')[1].split('"inferred"')[0]
        self.assertNotIn("style_terms", measured_block)
        self.assertNotIn("bpm_confidence", measured_block)

    def test_measuring_a_file_demands_to_be_told_what_it_listened_to(self):
        """`basis` has no default on `measure_file`. A measurement that forgets its
        source is the thing this whole design is arranged to prevent — 125 BPM off a
        master and 125 BPM off a thirty-second excerpt are the same number and different
        claims."""
        import inspect

        signature = inspect.signature(audio.measure_file)
        self.assertIs(signature.parameters["basis"].default, inspect.Parameter.empty)

    def test_the_preview_path_still_labels_itself(self):
        import inspect

        self.assertIn('basis="deezer_preview_30s"', inspect.getsource(audio.measure))


if __name__ == "__main__":
    unittest.main()
