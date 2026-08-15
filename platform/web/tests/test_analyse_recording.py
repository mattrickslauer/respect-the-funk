"""The agent that listens to a master, end to end against a real file.

    python -m unittest discover platform/web/tests

The fetch half downloads over HTTP, hashes the stream, shells out to ffmpeg and ffprobe,
and measures a tempo. Faking all of that would leave the test asserting the shape of a
dict nobody produced, so instead: a real WAV is synthesised at a known tempo, served by a
real HTTP server on localhost, and the agent is pointed at it through a fake bucket whose
`presign_get` returns that URL. Everything except S3 itself is the production path.

That is what makes the two checks below mean something:

  * `test_a_replaced_object_is_caught_by_its_hash` serves bytes that are not the ones the
    row recorded. This is the moment an `asserted` content hash becomes a `measured` one,
    and it is the only place in the system where a swapped or corrupted object can be
    caught by anything other than a human noticing the track sounds wrong.
  * `test_the_tempo_measured_is_the_tempo_generated` asserts against the signal that was
    generated rather than against somebody's opinion of a record's BPM, which is the same
    reasoning `test_audio.py` gives for using click trains.

`ffmpeg`/`numpy` gate the whole module. Neither is in the web Lambda's bundle, and
`requirements-worker.txt` exists to say that this agent is drained by a worker.
"""

from __future__ import annotations

import hashlib
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

HAVE_DB = bool(os.environ.get("DATABASE_URL"))
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
try:
    import numpy  # noqa: F401

    HAVE_NUMPY = True
except ImportError:  # pragma: no cover - the Lambda path
    HAVE_NUMPY = False

RUNNABLE = HAVE_DB and HAVE_FFMPEG and HAVE_NUMPY

BUCKET = "rtf-masters-test"
TRUE_BPM = 125.0


def _click_wav(path: str, bpm: float = TRUE_BPM, seconds: float = 20.0) -> None:
    """A WAV of clicks at a known tempo, written with ffmpeg's own synthesiser.

    Generated rather than committed: a binary fixture in the repository is a thing
    nobody can review, and the tempo has to be known exactly for the assertion to be
    about the measurement rather than about the fixture.
    """
    period = 60.0 / bpm
    # A click train as a sum of short sine bursts, expressed as an ffmpeg filter: a
    # 40 Hz square wave gated to short attacks reads to an onset detector as a beat.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i",
         f"sine=frequency=1000:sample_rate=44100:duration={seconds}",
         "-af", (f"tremolo=f={1.0 / period}:d=1,"
                 f"aeval=val(0)*val(0)*val(0)*val(0)*val(0)|"
                 f"val(0)*val(0)*val(0)*val(0)*val(0)"),
         "-ac", "2", "-ar", "44100", path],
        check=True, capture_output=True, timeout=120)


class _Serve(http.server.BaseHTTPRequestHandler):
    """Serves one blob, so the agent's urllib download is a real network read."""

    payload = b""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args: object) -> None:
        """Silence. The test runner's output is the product here."""


class _FakeBucket:
    """`presign_get` points at the local server; nothing else is exercised by fetch."""

    name = "fake"

    def __init__(self, bucket: str, url: str) -> None:
        self.bucket = bucket
        self.url = url

    def presign_put(self, key: str, *, content_type: str, expires_in: int = 600) -> str:
        return self.url

    def presign_get(self, key: str, *, expires_in: int = 900) -> str:
        return self.url

    def head(self, key: str):  # noqa: ANN201 - matches the port
        from spindle import storage

        return storage.Head(bytes=len(_Serve.payload), mime="audio/wav", etag="e")

    def delete(self, key: str) -> None:
        return None


@unittest.skipUnless(RUNNABLE, "needs DATABASE_URL, ffmpeg and numpy")
class AnalyseRecording(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.mkdtemp(prefix="rtf-analyse-")
        cls.wav = os.path.join(cls.tmpdir, "master.wav")
        _click_wav(cls.wav)
        with open(cls.wav, "rb") as handle:
            cls.audio_bytes = handle.read()

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Serve)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/master.wav"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        os.environ["PLATFORM_MASTERS_BUCKET"] = BUCKET
        # Unset for every test in this class, and restored in tearDown.
        #
        # This is not tidiness. `.env` now exports PLATFORM_CLASSIFIER_FUNCTION so a
        # worker can reach the deployed classifier, and a developer who sources it
        # before running the suite was silently making these tests **invoke the
        # production Lambda** — which then 404s trying to download a synthetic fixture
        # from the real bucket. Two of the three errors in the first run after that
        # variable was added were exactly this.
        #
        # A test that reaches production is a test that costs money, depends on a
        # deploy, and fails for reasons that have nothing to do with the code under
        # test. Tests that want a classifier stub `agents._classify` instead.
        self._saved_classifier = os.environ.pop("PLATFORM_CLASSIFIER_FUNCTION", None)
        _Serve.payload = self.audio_bytes
        self.hash = hashlib.sha256(self.audio_bytes).hexdigest()

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-analyse-{self.tenant[:8]}", "analyse test"))
            cur.execute("""INSERT INTO party (tenant_id, slug, name)
                           VALUES (%s, 'act', 'Test Act') RETURNING id""", (self.tenant,))
            self.party = str(cur.fetchone()["id"])
            cur.execute("""INSERT INTO recording (tenant_id, slug, title)
                           VALUES (%s, 'a-track', 'A Track') RETURNING id""",
                        (self.tenant,))
            self.recording = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO party_credit (tenant_id, party_id, subject_kind,
                       subject_id, role)
                   VALUES (%s, %s, 'recording', %s, 'main_artist')""",
                (self.tenant, self.party, self.recording))
        self.store = _FakeBucket(BUCKET, self.url)

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""DELETE FROM fact_basis WHERE subject_id IN (
                             SELECT id FROM party_fact WHERE tenant_id = %s)""",
                        (self.tenant,))
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()
        os.environ.pop("PLATFORM_MASTERS_BUCKET", None)
        if self._saved_classifier is not None:
            os.environ["PLATFORM_CLASSIFIER_FUNCTION"] = self._saved_classifier

    # ------------------------------------------------------------------ fixtures

    def _stored_asset(self, content_hash: str | None = None) -> str:
        from spindle import assets

        row, _ = assets.claim(
            self.conn, self.tenant, self.recording, kind="master",
            content_hash=content_hash or self.hash, mime="audio/wav",
            size=len(self.audio_bytes), uploaded_by="operator")
        assets.confirm(self.conn, self.store, self.tenant, str(row["id"]))
        return str(row["id"])

    def _lead(self, asset_id: str) -> dict:
        return {"id": str(uuid.uuid4()), "tenant_id": self.tenant, "target": asset_id,
                "recording_id": self.recording, "party_id": self.party}

    def _run(self, asset_id: str):
        from spindle import agents, spend, storage

        # The agent resolves its own adapter from settings; point that at the fake.
        storage._ADAPTERS[(BUCKET, "us-east-1")] = self.store
        lead = self._lead(asset_id)
        # A real gate, though this agent never charges it — the download is S3 egress,
        # not a metered provider call, and putting a token cost in `agent_run` for it
        # would be a number that means nothing.
        gate = spend.Gate.open(self.conn, self.tenant)
        prepared = agents._fetch_analyse_recording(self.conn, lead, gate)
        outcome = agents._write_analyse_recording(self.conn, lead, gate, prepared)
        return prepared, outcome

    # -------------------------------------------------------------------- the run

    def test_the_tempo_measured_is_the_tempo_generated(self) -> None:
        """Ground truth is the signal that made the file, not an opinion about a
        record. The same reasoning `test_audio.py` gives for click trains."""
        prepared, _ = self._run(self._stored_asset())
        self.assertAlmostEqual(prepared["facts"]["measured"]["bpm"], TRUE_BPM, delta=3.0)

    def test_the_measurement_names_the_asset_it_listened_to(self) -> None:
        """The whole reason `measure_file` has no default `basis`. A BPM off a master
        and a BPM off a 30-second preview are the same number and different claims."""
        asset_id = self._stored_asset()
        prepared, _ = self._run(asset_id)
        self.assertEqual(prepared["facts"]["basis"], f"recording_asset:{asset_id}")

    def test_the_asset_learns_its_own_shape(self) -> None:
        """`sample_rate` was NULL because nothing had opened the file. It is now the
        file's own rate — 44100 — and not the 22050 this module resamples to, which
        would be recording our own settings as a measurement of the record."""
        from spindle import assets

        asset_id = self._stored_asset()
        self._run(asset_id)
        row = assets.get(self.conn, self.tenant, asset_id)
        self.assertEqual(row["sample_rate"], 44100)
        self.assertEqual(row["duration_ms"], 20000)

    def test_facts_land_with_a_basis_edge_naming_the_asset(self) -> None:
        """Migration 016 declined to add `party_fact.asset_id` on the grounds that
        `fact_basis` already does this and already renders. This is that claim, run."""
        asset_id = self._stored_asset()
        _, outcome = self._run(asset_id)
        self.assertGreaterEqual(outcome.facts, 3)

        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT f.dimension, f.provenance, b.basis_kind, b.basis_id
                     FROM party_fact f
                     JOIN fact_basis b ON b.subject_kind = 'fact' AND b.subject_id = f.id
                    WHERE f.tenant_id = %s AND f.recording_id = %s AND f.status = 'live'
                    ORDER BY f.dimension""",
                (self.tenant, self.recording))
            rows = cur.fetchall()

        self.assertTrue(rows, "no fact carried a basis edge")
        for row in rows:
            self.assertEqual(row["basis_kind"], "recording_asset")
            self.assertEqual(str(row["basis_id"]), asset_id)

        by_dimension = {r["dimension"]: r["provenance"] for r in rows}
        self.assertEqual(by_dimension["bpm"], "measured")
        self.assertEqual(by_dimension["bpm_confidence"], "inferred",
                         "a confidence is not a measurement of the record")

    def test_running_twice_supersedes_rather_than_duplicates(self) -> None:
        """`SCOPE-RESET §2a` rule 1. A remeasured track does not erase what the previous
        measurement said — "why did we think it was 84 BPM in March" has a right
        answer, and the answer is the superseded row."""
        asset_id = self._stored_asset()
        self._run(asset_id)
        self._run(asset_id)

        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT status, count(*) AS n FROM party_fact
                    WHERE tenant_id = %s AND recording_id = %s AND dimension = 'bpm'
                    GROUP BY status""", (self.tenant, self.recording))
            counts = {r["status"]: r["n"] for r in cur.fetchall()}

        self.assertEqual(counts.get("live"), 1, "two live BPMs for one recording")
        self.assertEqual(counts.get("superseded"), 1, "the earlier reading was lost")

    # ------------------------------------------------------------------ refusals

    def test_a_replaced_object_is_caught_by_its_hash(self) -> None:
        """The check `assets.confirm` deliberately cannot make. Confirm verifies size,
        because that is what `head_object` returns for free; the digest needs the bytes,
        and this agent is already reading them.

        Serving different bytes than the row recorded is exactly what a swapped or
        corrupted object looks like, and it must not be measured as though it were the
        master — every fact derived from it would be about a different file.
        """
        from spindle import agents

        asset_id = self._stored_asset()
        _Serve.payload = self.audio_bytes + b"tampered"

        with self.assertRaises(agents.fleet.LeadFailed) as caught:
            self._run(asset_id)
        self.assertTrue(caught.exception.permanent,
                        "a wrong object will still be wrong on the next attempt")
        self.assertIn("hashes to", str(caught.exception))

    def test_a_pending_asset_is_not_permanent_failure(self) -> None:
        """Queued before the upload was confirmed. The operator may still finish it, so
        the lead backs off rather than parking — which is the difference between "try
        later" and "a human needs to look at this"."""
        from spindle import agents, assets

        row, _ = assets.claim(self.conn, self.tenant, self.recording, kind="master",
                              content_hash=self.hash, mime="audio/wav",
                              size=len(self.audio_bytes), uploaded_by="operator")
        with self.assertRaises(agents.fleet.LeadFailed) as caught:
            self._run(str(row["id"]))
        self.assertFalse(caught.exception.permanent)

    def test_a_deleted_asset_parks_the_lead(self) -> None:
        from spindle import agents

        with self.assertRaises(agents.fleet.LeadFailed) as caught:
            self._run(str(uuid.uuid4()))
        self.assertTrue(caught.exception.permanent)

    # ------------------------------------------------------------- the classifier

    def test_no_classifier_means_no_genre_and_the_run_says_so(self) -> None:
        """`NO FALLBACKS`. With no classifier deployed the agent writes no genre at all
        rather than promoting the tempo-derived style terms into the genre dimension —
        "125 BPM records are often house" and "this record is house" are different
        claims, and the first must not quietly stand in for the second.

        The summary says which happened, because a track with no genre otherwise cannot
        tell an operator whether it was unclassifiable or whether nothing was deployed.
        """
        os.environ.pop("PLATFORM_CLASSIFIER_FUNCTION", None)
        _, outcome = self._run(self._stored_asset())

        with self.conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM party_fact
                            WHERE tenant_id = %s AND dimension IN ('genre', 'style')""",
                        (self.tenant,))
            self.assertEqual(cur.fetchone()["n"], 0)
        self.assertIn("no classifier configured", outcome.summary)

    def test_a_classified_master_writes_genre_and_style_as_inferred(self) -> None:
        """A genre is a model's opinion about a record; a BPM is a property of the
        waveform. Writing the first as `measured` would be the provenance laundering
        `SCOPE-RESET §2a` exists to stop, so both classifier facts are `inferred`.

        The classifier itself is stubbed — it is validated separately and thoroughly by
        `platform/classifier/tests/validate.py` against six reference tracks, which is
        where a genre model's correctness belongs. What is under test here is the wiring.
        """
        from spindle import agents

        stub = {"parent": "Electronic", "style": "Electronic---House",
                "confidence": 0.71, "model": "genre_discogs400-discogs-effnet-1",
                "confident": "style", "styles": []}
        original = agents._classify
        agents._classify = lambda cfg, asset: stub
        try:
            _, outcome = self._run(self._stored_asset())
        finally:
            agents._classify = original

        with self.conn.cursor() as cur:
            cur.execute("""SELECT dimension, provenance, value_text, source
                             FROM party_fact
                            WHERE tenant_id = %s AND dimension IN ('genre', 'style')
                              AND status = 'live' ORDER BY dimension""", (self.tenant,))
            rows = {r["dimension"]: r for r in cur.fetchall()}

        self.assertEqual(rows["genre"]["value_text"], "Electronic")
        self.assertEqual(rows["style"]["value_text"], "Electronic---House")
        for dimension in ("genre", "style"):
            self.assertEqual(rows[dimension]["provenance"], "inferred")
            self.assertIn("discogs", rows[dimension]["source"])
        self.assertIn("Electronic---House", outcome.summary)

    def test_a_genre_below_the_style_floor_writes_only_the_parent(self) -> None:
        """The Dr. Dre case, at the wiring layer: the handler reports a parent and an
        empty style when it is unsure which sub-genre, and the agent must write one row
        rather than two — an empty `style` fact would assert that the model said the
        style was nothing."""
        from spindle import agents

        stub = {"parent": "Hip Hop", "style": "", "confidence": 0.215,
                "model": "genre_discogs400-discogs-effnet-1", "confident": "parent",
                "styles": []}
        original = agents._classify
        agents._classify = lambda cfg, asset: stub
        try:
            self._run(self._stored_asset())
        finally:
            agents._classify = original

        with self.conn.cursor() as cur:
            cur.execute("""SELECT dimension FROM party_fact
                            WHERE tenant_id = %s AND dimension IN ('genre', 'style')
                              AND status = 'live'""", (self.tenant,))
            found = {r["dimension"] for r in cur.fetchall()}
        self.assertEqual(found, {"genre"}, "an unsure style was written anyway")

    # -------------------------------------------------------------- the handoff

    def test_confirming_an_upload_queues_the_analysis(self) -> None:
        """Uploading a file is the only action an operator takes; the analysis follows
        from the row. Same move `outreach.advance` makes when a thread closes."""
        from spindle import assets

        asset_id = self._stored_asset()
        self.assertTrue(assets.queue_analysis(self.conn, self.tenant, self.recording,
                                              asset_id))

        with self.conn.cursor() as cur:
            cur.execute("""SELECT kind, scope_kind, party_id, recording_id, target
                             FROM lead
                            WHERE tenant_id = %s AND kind = 'analyse_recording'""",
                        (self.tenant,))
            lead = cur.fetchone()

        self.assertIsNotNone(lead, "no lead was queued")
        self.assertEqual(lead["scope_kind"], "recording")
        self.assertEqual(str(lead["party_id"]), self.party,
                         "lead_scope_shape requires a party on a recording lead")
        self.assertEqual(lead["target"], asset_id)

    def test_queueing_twice_for_one_asset_makes_one_lead(self) -> None:
        """`UNIQUE (tenant_id, target_hash)` over the asset id. Re-confirming an upload
        does not queue a second analysis — and a remaster is a different asset, so it
        does. One analysis per distinct file."""
        from spindle import assets

        asset_id = self._stored_asset()
        first = assets.queue_analysis(self.conn, self.tenant, self.recording, asset_id)
        second = assets.queue_analysis(self.conn, self.tenant, self.recording, asset_id)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_a_recording_with_no_credit_queues_nothing(self) -> None:
        """`lead_scope_shape` requires a party on a recording-scoped lead, and the party
        is whoever is credited. A recording with no credit has no owner to bill the work
        to, so nothing is queued rather than an owner being invented."""
        from spindle import assets

        with self.conn.cursor() as cur:
            cur.execute("""DELETE FROM party_credit
                            WHERE tenant_id = %s AND subject_id = %s""",
                        (self.tenant, self.recording))
        self.assertFalse(assets.queue_analysis(self.conn, self.tenant, self.recording,
                                               self._stored_asset()))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class Reanalysing(AnalyseRecording):
    """`--reanalyse`, the hand-crank for a pipeline that got better.

    `--retry-failed` does not cover this: these leads did not fail, they succeeded
    against a worse pipeline. And `queue_analysis` is idempotent on the asset id
    *deliberately* — re-confirming an upload must not queue a second analysis — so
    nothing re-queues one either. Without this flag the only way to re-read a master
    after deploying a classifier is to delete rows by hand.
    """

    def test_a_completed_lead_returns_to_the_frontier(self) -> None:
        from spindle import assets, ingest

        asset_id = self._stored_asset()
        assets.queue_analysis(self.conn, self.tenant, self.recording, asset_id)
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE lead SET state = 'done', attempts = 3
                            WHERE tenant_id = %s AND kind = 'analyse_recording'""",
                        (self.tenant,))

        self.assertEqual(ingest.reanalyse(self.conn, self.tenant), 1)
        with self.conn.cursor() as cur:
            cur.execute("""SELECT state, attempts FROM lead
                            WHERE tenant_id = %s AND kind = 'analyse_recording'""",
                        (self.tenant,))
            row = cur.fetchone()
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempts"], 0,
                         "carrying strikes forward would park it after one hiccup")

    def test_a_slug_narrows_it_to_one_recording(self) -> None:
        """After deploying a classifier you want every master; while debugging one
        track you emphatically do not."""
        from spindle import assets, ingest

        assets.queue_analysis(self.conn, self.tenant, self.recording,
                              self._stored_asset())
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE lead SET state = 'done'
                            WHERE tenant_id = %s AND kind = 'analyse_recording'""",
                        (self.tenant,))
            cur.execute("SELECT slug FROM recording WHERE tenant_id = %s AND id = %s",
                        (self.tenant, self.recording))
            slug = cur.fetchone()["slug"]

        self.assertEqual(ingest.reanalyse(self.conn, self.tenant, "not-a-slug"), 0)
        self.assertEqual(ingest.reanalyse(self.conn, self.tenant, slug), 1)


class SearchTerms(unittest.TestCase):
    """Turning stored facts into things you would type into a playlist search.

    Pure, so these always run. They cover the step where the chain nearly broke: the
    classifier can be perfect and the labels still never reach Deezer if nothing
    converts `Electronic---Tropical House` into `Tropical House`.
    """

    def test_a_discogs_label_keeps_the_style_and_drops_the_parent(self) -> None:
        """"Electronic" describes a third of Deezer and would return the same playlists
        for a techno record and an ambient one. The parent is stored as its own `genre`
        fact; what a curator actually curates is the style."""
        from spindle import agents

        self.assertEqual(agents._search_terms("Electronic---Tropical House"),
                         ["Tropical House"])

    def test_a_comma_joined_list_becomes_its_members(self) -> None:
        from spindle import agents

        self.assertEqual(agents._search_terms("house, melodic house, progressive house"),
                         ["house", "melodic house", "progressive house"])

    def test_a_bare_platform_label_survives_unchanged(self) -> None:
        from spindle import agents

        self.assertEqual(agents._search_terms("Dance"), ["Dance"])

    def test_empty_and_whitespace_yield_nothing(self) -> None:
        """A term of `" "` would search Deezer's playlists for a space. `sources.py`
        already learned this one the hard way."""
        from spindle import agents

        self.assertEqual(agents._search_terms(""), [])
        self.assertEqual(agents._search_terms(" , , "), [])


class ClassifierTerms(unittest.TestCase):
    """Which of the model's labels become search terms.

    The classifier is multi-label — 400 independent sigmoids, not a softmax — so a tight
    cluster of related styles is the model saying a record is all of them, not the model
    being unsure. Taking the argmax alone throws away good queries on a 0.02 margin.
    """

    #: Measured on Hallow Youth's "Losing Sleep", which is what motivated the ratio.
    LOSING_SLEEP = {
        "model": "genre_discogs400-discogs-effnet-1",
        "styles": [
            {"label": "Electronic---Tropical House", "p": 0.3099},
            {"label": "Electronic---House", "p": 0.2900},
            {"label": "Electronic---Deep House", "p": 0.2880},
            {"label": "Electronic---Progressive House", "p": 0.2860},
            {"label": "Electronic---Electro House", "p": 0.1680},
        ],
    }

    def test_a_tight_cluster_keeps_the_whole_cluster(self) -> None:
        from spindle import agents

        self.assertEqual(
            agents._classifier_terms(self.LOSING_SLEEP),
            ["Tropical House", "House", "Deep House", "Progressive House"])

    def test_the_straggler_below_the_ratio_is_dropped(self) -> None:
        from spindle import agents

        self.assertNotIn("Electro House", agents._classifier_terms(self.LOSING_SLEEP))

    def test_a_confident_single_answer_yields_one_term(self) -> None:
        """Metallica measured 0.844 against a distant field. A ratio rule must not turn
        a decisive answer into four hedged ones."""
        from spindle import agents

        decisive = {"model": "m", "styles": [
            {"label": "Rock---Heavy Metal", "p": 0.844},
            {"label": "Rock---Thrash", "p": 0.120},
        ]}
        self.assertEqual(agents._classifier_terms(decisive), ["Heavy Metal"])

    def test_a_barely_recognised_track_contributes_nothing_confident(self) -> None:
        """The absolute floor under the ratio. Without it, five labels at 0.02 would
        pass the ratio test against each other and produce four confident-looking terms
        that are all noise."""
        from spindle import agents

        noise = {"model": "m", "styles": [{"label": f"X---S{i}", "p": 0.02}
                                          for i in range(5)]}
        self.assertEqual(agents._classifier_terms(noise), [])

    def test_no_classifier_yields_no_terms(self) -> None:
        from spindle import agents

        self.assertEqual(agents._classifier_terms(None), [])
        self.assertEqual(agents._classifier_terms({"model": "m", "styles": []}), [])


if __name__ == "__main__":
    unittest.main()
