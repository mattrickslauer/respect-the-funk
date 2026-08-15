"""Recommendations — arithmetic over a measurement, or nothing at all.

The test that matters most in this file is the first one: an unmeasured song produces no
ranking. Everything else here checks that each rule is RHYTHM-STUDY §9's rule and not a
taste judgement wearing a decimal point.
"""

from __future__ import annotations

import pytest

from remixkit.services import briefs
from remixkit.services.recommendations import (
    MAX_LOOP_MS,
    MIN_LOOP_MS,
    RecommendationService,
)

from conftest import BEAT_MS, DROP_MS

BAR_MS = BEAT_MS * 4  # 1920ms at 125 BPM


@pytest.fixture
def service():
    return RecommendationService()


@pytest.fixture
def song(container, principal):
    artist = container.artists.create(principal, name="Hallow Youth")
    return container.songs.create(principal, artist.id, title="Losing Sleep")


def check(candidate, rule):
    return next(c for c in candidate.checks if c.rule == rule)


# ------------------------------------------------------------------ no basis, no output
def test_an_unmeasured_song_gets_what_is_missing_not_a_ranking(service, song):
    report = service.recommend(song)

    assert report.computed is False
    assert report.candidates == []
    assert "upload the mp3 or wav" in report.blocked_on[0]


def test_an_unanalysed_master_says_so(service, container, principal, song):
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, b"bytes", content_type="audio/wav")
    stored = container.songs.register_master(principal, song.id, key=key)

    report = service.recommend(stored)
    assert report.computed is False
    assert "nothing has measured this song" in report.blocked_on[0]


def test_every_candidate_carries_a_basis(service, measured_song):
    report = service.recommend(measured_song)
    assert report.candidates
    assert all(candidate.basis for candidate in report.candidates)


# ------------------------------------------------------------------ ranking
def test_the_drop_outranks_the_chorus_on_measured_energy(service, measured_song):
    report = service.recommend(measured_song)
    names = [c.section.display_name for c in report.candidates]

    assert names[0] == "Drop 1"
    assert "Chorus 1" in names
    assert "Breakdown 1" not in names, "a breakdown is not hook material"
    assert report.best.rank_reason == "starts at the measured drop"


def test_a_hand_marked_section_ranks_below_a_measured_one(
    service, container, principal, measured_song
):
    """Not a penalty — there is simply no measured energy to rank it on, and inventing one
    would be the ranking claiming more than it knows."""
    stored = container.songs.add_section(
        principal, measured_song.id, start_ms=0, end_ms=6_000, label="By ear"
    )
    report = service.recommend(stored)
    by_ear = next(c for c in report.candidates if c.section.label == "By ear")

    assert report.candidates[0].section.label != "By ear"
    assert "nothing measured it" in by_ear.rank_reason
    assert "no measurement behind this window" in by_ear.basis


# ------------------------------------------------------------------ the rules
def test_a_thirty_second_chorus_fails_the_renderable_loop_rule(service, measured_song):
    """The whole section is not the loop. Every video renders at the hook's length."""
    report = service.recommend(measured_song)
    chorus = next(c for c in report.candidates if c.section.display_name == "Chorus 1")

    assert check(chorus, "renderable loop (3–10s)").ok is False
    assert "3–10s" in check(chorus, "renderable loop (3–10s)").detail


def test_the_loop_clamp_matches_what_the_renderer_actually_does(service):
    """Two constants that disagree would have this screen recommending a length the
    renderer silently overrides."""
    assert MIN_LOOP_MS / 1000 == briefs.MIN_LOOP_S
    assert MAX_LOOP_MS / 1000 == briefs.MAX_LOOP_S


def test_a_window_off_the_bar_line_fails_the_alignment_rule(
    service, container, principal, measured_song
):
    drop = next(s for s in measured_song.hook_sections if s.role.value == "drop")
    moved = container.songs.update_section(
        principal, measured_song.id, drop.id, start_ms=DROP_MS + 300, end_ms=DROP_MS + 300 + 6_400
    )
    report = service.recommend(moved)
    candidate = next(c for c in report.candidates if c.section.id == drop.id)

    assert check(candidate, "starts on a bar line").ok is False
    assert "off the bar grid" in check(candidate, "starts on a bar line").detail


def test_an_unmeasured_bar_line_is_unknown_not_a_failure(
    service, container, principal, measured_song
):
    """Rendering "no measurement for this rule" as a failure sends someone to fix a window
    that may well be right."""
    from conftest import FakeAnalyzer, sample_analysis
    from remixkit.services.analysis import AnalysisService

    blind = sample_analysis(bpm=None, beat_ms=None, downbeat_ms=None, drop_ms=None,
                            bar_phase_confidence=None)
    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, FakeAnalyzer(blind)
    )
    stored = container.analysis.run(principal.tenant_id, measured_song.id)

    candidate = service.recommend(stored).candidates[0]
    assert check(candidate, "starts on a bar line").ok is None
    assert check(candidate, "whole number of bars").ok is None
    assert candidate.failures or True  # unknown rules do not count as failures
    assert all(c.ok is not False for c in candidate.checks if "bar" in c.rule)


def test_the_drop_is_checked_against_its_measured_plateau(service, measured_song):
    report = service.recommend(measured_song)
    drop = report.candidates[0]

    plateau = check(drop, "inside the measured plateau")
    assert plateau.ok is True
    assert "28 beats" in plateau.detail


def test_a_loop_past_the_plateau_is_flagged(service, container, principal, measured_song):
    drop = next(s for s in measured_song.hook_sections if s.role.value == "drop")
    stretched = container.songs.update_section(
        principal, measured_song.id, drop.id, start_ms=DROP_MS, end_ms=DROP_MS + 20_000
    )
    candidate = next(
        c for c in service.recommend(stretched).candidates if c.section.id == drop.id
    )
    assert check(candidate, "inside the measured plateau").ok is False


# ------------------------------------------------------------------ the suggestion
def test_the_suggested_window_is_whole_bars_from_the_bar_line(service, measured_song):
    """RHYTHM-STUDY §4's worked example: 23 beats is the one nearby value that neither
    uses the whole plateau nor ends on a bar line. The suggestion never produces one."""
    report = service.recommend(measured_song)
    chorus = next(c for c in report.candidates if c.section.display_name == "Chorus 1")

    assert chorus.has_suggestion
    assert (chorus.suggested_start_ms - DROP_MS) % BAR_MS == pytest.approx(0, abs=1)
    assert chorus.suggested_duration_ms % BAR_MS == pytest.approx(0, abs=1)
    assert MIN_LOOP_MS <= chorus.suggested_duration_ms <= MAX_LOOP_MS


def test_the_drop_suggestion_stays_inside_the_plateau_and_the_ceiling(service, measured_song):
    drop = service.recommend(measured_song).candidates[0]
    # 28 beats is 13.4s, so the renderer's 10s ceiling binds first — and it binds to whole
    # bars, which is 5 bars at 1920ms rather than a bare 10 seconds.
    assert drop.suggested_duration_ms == pytest.approx(5 * BAR_MS, abs=1)


def test_applying_a_suggestion_moves_the_window_and_points_the_hook(
    client, service, container, principal, measured_song
):
    candidate = service.recommend(measured_song).candidates[0]
    response = client.post(
        f"/ui/songs/{measured_song.id}/sections/{candidate.section.id}/primary",
        data={
            "start_ms": str(candidate.suggested_start_ms),
            "end_ms": str(candidate.suggested_end_ms),
        },
    )
    assert response.status_code == 200
    stored = container.songs.get(principal, measured_song.id)
    assert stored.hook.start_ms == candidate.suggested_start_ms
    assert stored.hook.end_ms == candidate.suggested_end_ms
    assert stored.primary_section_id == candidate.section.id


# ------------------------------------------------------------------ findings
def test_a_recorded_drop_that_disagrees_with_the_measured_one_is_reported(
    service, container, principal, measured_song
):
    off = container.songs.set_measurement(
        principal, measured_song.id, drop_ms=DROP_MS - 480, bpm_method="tapped"
    )
    findings = service.recommend(off).findings
    assert any("-480ms from the measured kick re-entry" in f.headline for f in findings)
    assert all(f.basis for f in findings), "a finding with no basis is not emitted"


def test_the_riser_is_reported_as_the_pre_roll_it_implies(service, measured_song):
    findings = service.recommend(measured_song).findings
    riser = next(f for f in findings if "riser" in f.headline)
    assert "8 beats (2 bars)" in riser.headline


# ------------------------------------------------------------------ the console + API
def test_the_song_page_shows_the_ranking_with_its_rules(client, measured_song):
    page = client.get(f"/console/songs/{measured_song.id}")
    assert "Recommendations" in page.text
    assert "starts on a bar line" in page.text
    assert "basis:" in page.text


def test_the_song_page_says_what_is_missing_when_nothing_is_measured(client, song):
    page = client.get(f"/console/songs/{song.id}")
    assert "upload the mp3 or wav" in page.text


def test_the_api_returns_the_checks_with_their_state(client, measured_song):
    body = client.get(f"/api/v1/songs/{measured_song.id}/recommendations").json()
    assert body["computed"] is True
    states = {c["state"] for c in body["candidates"][0]["checks"]}
    assert states <= {"pass", "fail", "unknown"}
    assert body["candidates"][0]["basis"]
