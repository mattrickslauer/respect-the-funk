"""Upload a master, measure it, keep what it found — and whose claim wins when they clash.

The interesting cases here are not the happy path. They are the three places where a
measurement meets something a person already recorded: a tempo they typed, a section they
marked, and the hook they chose. In all three the person wins, and the measurement is kept
next to theirs rather than thrown away.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import AnalysisStatus, Provenance
from remixkit.services.analysis import AnalysisService
from remixkit.services.errors import AnalysisUnavailable, Conflict

from conftest import DROP_MS, FakeAnalyzer, sample_analysis


@pytest.fixture
def song(container, principal):
    artist = container.artists.create(principal, name="Hallow Youth")
    return container.songs.create(principal, artist.id, title="Losing Sleep")


def upload(container, principal, song, *, data: bytes = b"RIFF fake wav bytes"):
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, data, content_type="audio/wav")
    return container.songs.register_master(principal, song.id, key=key, content_type="audio/wav")


# ------------------------------------------------------------------ the upload
def test_a_master_is_presigned_not_proxied(client, container, principal, song):
    """§2b rule 2 — the bytes go browser→bucket. The app only mints the URL."""
    response = client.post(
        f"/api/v1/songs/{song.id}/master-upload-url", json={"content_type": "audio/mpeg"}
    )
    body = response.json()
    assert body["method"] == "PUT"
    assert body["key"].endswith(f"{song.id}.mp3")
    # Nothing is recorded yet: an upload that is presigned and abandoned must not leave the
    # song claiming a master it does not have.
    assert container.songs.get(principal, song.id).master_key is None


def test_a_video_file_is_not_a_master(client, song):
    response = client.post(
        f"/api/v1/songs/{song.id}/master-upload-url", json={"content_type": "video/mp4"}
    )
    assert response.status_code == 409
    assert "WAV, MP3" in response.text


def test_registering_a_key_with_nothing_at_it_is_refused(client, principal, song):
    response = client.put(
        f"/api/v1/songs/{song.id}/master",
        json={"key": f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"},
    )
    assert response.status_code == 409
    assert "did not complete" in response.text


def test_a_key_belonging_to_another_song_is_refused(client, container, principal, song):
    """The key arrives from a browser, so it is checked rather than trusted."""
    other = "remixkit/masters/somebody-else/sng_theirs.wav"
    container.storage.put(other, b"not yours", content_type="audio/wav")

    response = client.put(f"/api/v1/songs/{song.id}/master", json={"key": other})
    assert response.status_code == 409
    assert "does not belong" in response.text


# ------------------------------------------------------------------ requesting
def test_analysis_needs_a_master(container, principal, song):
    with pytest.raises(Conflict) as exc:
        container.analysis.request(principal, song.id)
    assert "no master" in str(exc.value).lower()


def test_a_process_without_an_analyser_refuses_and_names_the_missing_piece(
    container, principal, song
):
    upload(container, principal, song)
    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, FakeAnalyzer(available=False)
    )
    with pytest.raises(AnalysisUnavailable) as exc:
        container.analysis.request(principal, song.id)
    assert "numpy" in str(exc.value)


def test_requesting_marks_the_song_queued_and_enqueues_a_job(container, principal, song):
    upload(container, principal, song)
    queued = container.analysis.request(principal, song.id)

    assert queued.analysis.status is AnalysisStatus.QUEUED
    assert container.enqueued[-1] == {"tenant_id": principal.tenant_id, "song_id": song.id}


def test_the_api_answers_202_because_the_work_is_queued(client, container, principal, song):
    upload(container, principal, song)
    response = client.post(f"/api/v1/songs/{song.id}/analysis")
    assert response.status_code == 202


def test_the_api_answers_503_when_this_process_cannot_measure(
    client, container, principal, song
):
    upload(container, principal, song)
    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, FakeAnalyzer(available=False)
    )
    assert client.post(f"/api/v1/songs/{song.id}/analysis").status_code == 503


# ------------------------------------------------------------------ running
def test_a_measurement_lands_with_its_method_and_its_bytes(container, principal, song):
    upload(container, principal, song, data=b"the master")
    measured = container.analysis.run(principal.tenant_id, song.id)

    assert measured.analysis.status is AnalysisStatus.DONE
    assert measured.analysis.method.startswith("fake:")
    assert measured.analysis.source_sha256, "which exact bytes were measured"
    assert measured.bpm == 125.0
    # The tempo carries the analyser and its method, not a bare number.
    assert measured.bpm_method.startswith("fake: ")
    assert len(measured.sections) == 4
    assert all(s.source is Provenance.MEASURED for s in measured.sections)


def test_the_analyser_is_told_where_a_person_said_the_drop_is(container, principal, song):
    """A track has more than one drop and only a person knows which the release is about."""
    upload(container, principal, song)
    container.songs.set_measurement(principal, song.id, drop_ms=124_000, bpm_method="tapped")
    analyzer = FakeAnalyzer()
    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, analyzer
    )
    container.analysis.run(principal.tenant_id, song.id)

    assert analyzer.calls[-1]["drop_ms"] == 124_000


def test_a_human_tempo_is_kept_and_the_disagreement_is_recorded(container, principal, song):
    upload(container, principal, song)
    container.songs.set_measurement(principal, song.id, bpm=124.0, bpm_method="distributor")
    measured = container.analysis.run(principal.tenant_id, song.id)

    assert measured.bpm == 124.0, "a person's number is not silently replaced"
    assert measured.bpm_method == "distributor"
    assert any("125.00" in w and "124.00" in w for w in measured.analysis.warnings)


def test_hand_marked_sections_survive_a_re_run(container, principal, song):
    upload(container, principal, song)
    container.songs.add_section(
        principal, song.id, start_ms=1_000, end_ms=7_000, label="By ear"
    )
    measured = container.analysis.run(principal.tenant_id, song.id)

    by_hand = [s for s in measured.sections if s.source is Provenance.MANUAL]
    assert [s.label for s in by_hand] == ["By ear"]
    assert len(measured.sections) == 5


def test_analysis_does_not_choose_the_hook(container, principal, song):
    """Analysis proposes; a person disposes. Repointing the hook silently would change
    what every future kit renders without anybody deciding to."""
    upload(container, principal, song)
    measured = container.analysis.run(principal.tenant_id, song.id)

    assert measured.primary_section_id is None
    assert measured.hook.duration_ms == 0


def test_a_chosen_hook_survives_a_re_run_by_overlap(container, principal, song):
    upload(container, principal, song)
    measured = container.analysis.run(principal.tenant_id, song.id)
    drop = next(s for s in measured.sections if s.role.value == "drop")
    container.songs.set_primary_section(principal, song.id, drop.id)

    again = container.analysis.run(principal.tenant_id, song.id)
    assert again.primary_section_id != drop.id, "the measured section was replaced"
    assert again.primary_section.start_ms == DROP_MS, "...by the one covering the same moment"


def test_a_measured_drop_fills_in_an_unrecorded_one(container, principal, song):
    upload(container, principal, song)
    measured = container.analysis.run(principal.tenant_id, song.id)
    assert measured.drop_ms == DROP_MS


def test_a_failure_is_recorded_on_the_song_not_raised_at_the_queue(container, principal, song):
    upload(container, principal, song)
    container.analysis = AnalysisService(
        container.songs,
        container.storage,
        container.queue,
        FakeAnalyzer(raises=RuntimeError("ffmpeg exploded")),
    )
    failed = container.analysis.run(principal.tenant_id, song.id)

    assert failed.analysis.status is AnalysisStatus.FAILED
    assert "ffmpeg exploded" in failed.analysis.error
    assert failed.sections == [], "a failed measurement writes no sections"


def test_a_section_with_no_duration_is_dropped(container, principal, song):
    upload(container, principal, song)
    empty = sample_analysis()
    empty.sections[0] = type(empty.sections[0])(
        role=empty.sections[0].role, start_ms=5_000, end_ms=5_000
    )
    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, FakeAnalyzer(empty)
    )
    measured = container.analysis.run(principal.tenant_id, song.id)
    assert len(measured.sections) == 3


# ------------------------------------------------------------------ the console
def test_the_analysis_panel_polls_while_it_is_running(client, container, principal, song):
    upload(container, principal, song)
    container.analysis.request(principal, song.id)

    panel = client.get(f"/ui/songs/{song.id}/analysis")
    assert "every 3s" in panel.text
    assert "hx-trigger" not in panel.headers.get("HX-Trigger", "").lower()
    assert "HX-Trigger" not in panel.headers


def test_a_settled_analysis_wakes_the_sections_panel(client, container, principal, song):
    upload(container, principal, song)
    container.analysis.run(principal.tenant_id, song.id)

    panel = client.get(f"/ui/songs/{song.id}/analysis")
    assert panel.headers["HX-Trigger"] == "rk:analysis-done"
    assert "125.00 BPM" in panel.text
    assert "fake:" in panel.text, "the method is shown, not hidden behind the number"


def test_completing_an_upload_records_the_master_and_queues_the_measurement(
    client, container, principal, song
):
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, b"bytes", content_type="audio/wav")

    response = client.post(
        f"/ui/songs/{song.id}/master", data={"key": key, "content_type": "audio/wav"}
    )
    assert response.status_code == 200
    assert container.songs.get(principal, song.id).master_key == key
    assert container.enqueued[-1]["song_id"] == song.id


def test_an_upload_still_completes_where_nothing_can_measure_it(
    client, container, principal, song
):
    """The upload was real. Failing it because this process lacks numpy would lose it."""
    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, FakeAnalyzer(available=False)
    )
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, b"bytes", content_type="audio/wav")

    response = client.post(f"/ui/songs/{song.id}/master", data={"key": key})
    assert response.status_code == 200
    assert container.songs.get(principal, song.id).master_key == key
    assert "numpy" in response.text


def test_local_storage_accepts_the_presigned_put(client, container, principal, song):
    """On a filesystem `presign_put` degrades to `/files/…`, and without this route the
    console's upload succeeds in the browser and writes nothing."""
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    assert client.put(f"/files/{key}", content=b"wav bytes").status_code == 200
    assert container.storage.get(key) == b"wav bytes"


def test_the_put_route_refuses_a_key_outside_this_tenants_masters(client):
    assert client.put("/files/remixkit/tenants/other/artists/a.yaml", content=b"x").status_code == 403


def test_the_worker_routes_a_message_by_its_job_type(container, principal, song, monkeypatch):
    """One queue, two jobs. A payload-shape guess is the kind of dispatch that works until
    two job types share a field."""
    import worker

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(worker, "analyse_one", lambda t, s: seen.append(("song", s)) or 0)
    monkeypatch.setattr(worker, "run_one", lambda t, k: seen.append(("kit", k)) or 0)

    worker._handle("analyze-song", {"tenant_id": "t", "song_id": "sng_1"})
    worker._handle("run-kit", {"tenant_id": "t", "kit_id": "kit_1"})
    assert seen == [("song", "sng_1"), ("kit", "kit_1")]


def test_switching_analysis_off_yields_a_refusal_not_a_stub(settings):
    """`RK_ANALYSIS_BACKEND=none` is for a process that should not do DSP at all."""
    from remixkit.deps import _build_analyzer

    analyzer = _build_analyzer(settings.model_copy(update={"analysis_backend": "none"}))
    assert analyzer.available is False
    with pytest.raises(AnalysisUnavailable):
        analyzer.analyze(b"whatever")


def test_a_process_without_numpy_falls_back_with_the_reason(settings, monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "numpy" else real(name)
    )
    from remixkit.deps import _build_analyzer

    analyzer = _build_analyzer(settings)
    assert analyzer.available is False
    assert "numpy" in analyzer.unavailable_reason


def test_the_deployment_screen_says_when_measurement_is_off(client, container):
    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, FakeAnalyzer(available=False)
    )
    assert any("Audio analysis is OFF" in w for w in container.describe()["warnings"])
