"""Running a failed kit again.

A failed run used to be terminal in the only sense that matters — the console rendered
the provider's error and offered nothing to do about it. Everything needed to run it
again was already stored (`kit.brief` is what `run()` re-plans from, and redelivery is
already a no-op), so what was missing was a way to say "again" and a decision about what
happens to the attempt that failed.

The interesting assertions here are not "the button posts". They are the three things a
retry could quietly get wrong: charging twice, losing the record of what the failed
attempt cost, and being swallowed by the queue's own deduplication because it carries the
same key as the run it is retrying.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import KitStatus
from remixkit.services.errors import Conflict, NotFound, RightsError


class RecordingQueue:
    """A queue that remembers rather than runs. `can_execute` because the service asks."""

    name = "recording"
    can_execute = True
    unavailable_reason = ""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []

    def enqueue(self, job_type: str, payload: dict, *, dedupe_key: str) -> str:
        self.calls.append((job_type, payload, dedupe_key))
        return dedupe_key


@pytest.fixture
def failed_kit(container, principal, monkeypatch):
    """A kit that ran and failed the way the real one did — at the provider, mid-run.

    Driven through `run()` rather than by writing `status=failed` onto the document,
    because the thing being tested is what a retry does to state the *worker* left
    behind: a cleared ledger, an error string, and possibly some assets that were paid
    for before the step that broke.
    """
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    kit = container.kits.request(principal, song_id=song.id, video_count=1)

    def _explode(request):
        raise RuntimeError(
            "Sora submit failed: Expected entry at `input_reference` to be bytes, an "
            "io.IOBase instance, PathLike or a tuple but received <class 'dict'> instead."
        )

    monkeypatch.setattr(container.kits._generator, "generate", _explode)
    kit = container.kits.run(principal.tenant_id, kit.id)
    assert kit.status is KitStatus.FAILED
    monkeypatch.undo()
    return kit


def test_a_failed_run_can_be_run_again(container, principal, failed_kit):
    """The whole point: same kit, same brief, back on the queue."""
    retried = container.kits.retry(principal, failed_kit.id)

    assert retried.id == failed_kit.id, "a retry is the same kit, not a copy of it"
    assert retried.status is KitStatus.QUEUED
    assert retried.error is None, "the old error must not survive the run that replaces it"
    assert retried.brief == failed_kit.brief, "the brief it was bought with is what re-runs"

    # And it actually generates this time — the failure was the provider's, not the brief's.
    done = container.kits.run(principal.tenant_id, retried.id)
    assert done.status is KitStatus.READY, done.error
    assert done.assets


def test_the_retry_is_a_second_message_not_a_duplicate_of_the_first(container, principal, failed_kit):
    """SQS FIFO holds a `MessageDeduplicationId` for five minutes.

    The kit id alone was the right dedupe key exactly once. A retry issued straight after
    a fast failure — which is what somebody does the moment they have fixed the cause —
    would be accepted by the queue and silently dropped, leaving a row at `queued` with
    nothing coming for it.
    """
    queue = RecordingQueue()
    container.kits._queue = queue

    container.kits.retry(principal, failed_kit.id)
    # This queue records rather than runs, so the worker is driven by hand.
    assert container.kits.run(principal.tenant_id, failed_kit.id).status is KitStatus.READY

    keys = [key for _, _, key in queue.calls]
    assert keys == [f"{failed_kit.id}:2"]
    assert keys[0] != failed_kit.id, "a retry must not reuse the original run's dedupe key"
    # The payload is unchanged: the worker looks the kit up and re-plans from the brief.
    assert queue.calls[0][1] == {"tenant_id": principal.tenant_id, "kit_id": failed_kit.id}


def test_what_the_failed_attempt_cost_is_kept(container, principal, monkeypatch):
    """A run that produced assets and then failed still spent money.

    `run()` overwrites `assets` and re-costs from them, so without a record here a kit
    that burned two video steps before failing and then succeeded on the retry would read
    as though it had only ever cost the successful run. That is the same defect as pricing
    a modality instead of a model: a total that is precise and wrong.
    """
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    assert kit.status is KitStatus.READY and kit.total_cost_cents > 0
    spent = kit.total_cost_cents

    # A ready kit is refused, so this is the one place the test has to write state: the
    # shape being described is "assets were delivered and then the run failed", which the
    # mock provider has no way to produce on demand.
    kit.status = KitStatus.FAILED
    kit.error = "Manifest verification failed — provenance cannot be asserted."
    container.repo.put(principal.tenant_id, "kits", kit.id, kit)

    retried = container.kits.retry(principal, kit.id)

    assert retried.attempt == 2
    assert len(retried.attempts) == 1
    past = retried.attempts[0]
    assert past.number == 1
    assert past.cost_cents == spent
    assert past.asset_count == len(kit.assets)
    assert past.run_id == kit.run_id
    assert "Manifest verification" in past.error

    # The current ledger is empty — nothing is delivered right now — but the kit has not
    # forgotten what it has been charged.
    assert retried.total_cost_cents == 0
    assert retried.abandoned_cost_cents == spent
    assert retried.spent_cents == spent

    done = container.kits.run(principal.tenant_id, retried.id)
    assert done.spent_cents == done.total_cost_cents + spent


def test_the_discarded_attempts_bytes_leave_the_bucket(container, principal, monkeypatch):
    """`run()` would otherwise orphan them, and a bucket-as-database hides that."""
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    keys = [a.key for a in kit.assets if a.key] + [kit.manifest_key]
    assert all(container.storage.exists(key) for key in keys if key)

    kit.status = KitStatus.FAILED
    kit.error = "died on the last step"
    container.repo.put(principal.tenant_id, "kits", kit.id, kit)

    container.kits.retry(principal, kit.id)
    assert not any(container.storage.exists(key) for key in keys if key)


def test_a_kit_already_on_its_way_is_refused(container, principal):
    """A second message for the same work is a second bill for it."""
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    kit = container.kits.request(principal, song_id=song.id, video_count=1)

    with pytest.raises(Conflict, match="already on its way"):
        container.kits.retry(principal, kit.id)


def test_a_kit_that_worked_is_refused(container, principal):
    """Re-running it would charge again to replace assets that may already be published."""
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )

    with pytest.raises(Conflict, match="Generate another"):
        container.kits.retry(principal, kit.id)


def test_consent_withdrawn_since_the_run_blocks_the_retry(container, principal, failed_kit):
    """The gate is re-checked, not inherited from whenever the kit was bought.

    Time passes between a failure and the retry, and a retry generates the artist's face
    again — so it has to answer the rights question again too.
    """
    container.artists.set_consent(principal, failed_kit.artist_id, granted=False)

    with pytest.raises(RightsError, match="likeness consent"):
        container.kits.retry(principal, failed_kit.id)


def test_a_deleted_song_cannot_be_retried(container, principal, failed_kit):
    """`run()` re-plans from the song. Without it the brief's section ids resolve to
    nothing, so the refusal belongs here rather than as a second failed run."""
    container.repo.delete(principal.tenant_id, "songs", failed_kit.song_id)

    with pytest.raises(NotFound):
        container.kits.retry(principal, failed_kit.id)


def test_a_queue_that_cannot_execute_refuses_before_the_row_moves(container, principal, failed_kit):
    """The same rule `request()` applies: never leave a kit at `queued` forever."""

    class DeadQueue(RecordingQueue):
        can_execute = False
        unavailable_reason = "Generation cannot run on the inline queue inside Lambda."

    container.kits._queue = DeadQueue()

    with pytest.raises(Conflict, match="inside Lambda"):
        container.kits.retry(principal, failed_kit.id)

    assert container.kits.get(principal, failed_kit.id).status is KitStatus.FAILED
    assert container.kits.get(principal, failed_kit.id).error, "the refusal must not clear it"


# ------------------------------------------------------------------ HTTP surface
def _failed_kit_over_http(client, container):
    """The same failed kit, reached through the app the console talks to."""
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    client.put(
        f"/api/v1/artists/{artist['id']}/consent",
        json={"granted": True, "signed_by": "A. T."},
    )
    song = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"}
    ).json()
    kit = client.post("/api/v1/kits", json={"song_id": song["id"], "video_count": 1}).json()

    principal = container.auth._principal
    document = container.kits.get(principal, kit["id"])
    document.status = KitStatus.FAILED
    document.error = "Sora submit failed: expected an object, but got a file instead."
    container.repo.put(principal.tenant_id, "kits", document.id, document)
    return artist, song, document


def test_api_retry_is_accepted_not_completed(client, container):
    _, _, kit = _failed_kit_over_http(client, container)

    response = client.post(f"/api/v1/kits/{kit.id}/retry")
    assert response.status_code == 202
    body = response.json()
    assert body["id"] == kit.id, "the id is stable, so a poller keeps watching one document"
    assert body["status"] == "queued"
    assert body["error"] is None


def test_api_retry_refuses_a_ready_kit_with_409(client, container):
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    client.put(
        f"/api/v1/artists/{artist['id']}/consent",
        json={"granted": True, "signed_by": "A. T."},
    )
    song = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"}
    ).json()
    queued = client.post("/api/v1/kits", json={"song_id": song["id"], "video_count": 1}).json()
    principal = container.auth._principal
    container.kits.run(principal.tenant_id, queued["id"])

    response = client.post(f"/api/v1/kits/{queued['id']}/retry")
    assert response.status_code == 409
    assert "Generate another" in response.json()["detail"]


def test_console_row_offers_the_retry_and_swaps_itself(client, container):
    """The failed row carries the button, and the answer is the row — queued, and
    therefore polling again, which is what draws the rest of the run."""
    artist, _, kit = _failed_kit_over_http(client, container)

    page = client.get(f"/console/artists/{artist['id']}").text
    assert f'hx-post="/ui/kits/{kit.id}/retry"' in page

    response = client.post(
        f"/ui/kits/{kit.id}/retry",
        headers={"HX-Request": "true", "HX-Target": f"kit-{kit.id}"},
    )
    assert response.status_code == 200
    assert "<html" not in response.text, "a fragment route must return only its own markup"
    assert 'class="badge badge-queued"' in response.text
    # Queued means the replacement markup carries the poll again.
    assert f'hx-get="/ui/kits/{kit.id}/row"' in response.text
    # The rest of the screen counts kits, so it has to be told.
    assert "rk:kits" in response.headers.get("HX-Trigger", "")


def test_console_kit_page_offers_the_retry_and_reloads(client, container):
    """The kit page does not poll — it never has — so re-rendering it in place would
    show a queued kit that never moved. A reload is the honest answer."""
    _, _, kit = _failed_kit_over_http(client, container)

    page = client.get(f"/console/kits/{kit.id}").text
    assert "The run failed." in page
    assert f'hx-post="/ui/kits/{kit.id}/retry"' in page

    response = client.post(f"/ui/kits/{kit.id}/retry", headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"
    assert container.kits.get(container.auth._principal, kit.id).status is KitStatus.QUEUED


def test_console_layer_gets_the_overlay_back(client, container):
    """A kit opened over a list has no row of its own to swap."""
    _, _, kit = _failed_kit_over_http(client, container)

    response = client.post(
        f"/ui/kits/{kit.id}/retry",
        headers={"HX-Request": "true", "HX-Target": "layer"},
    )
    assert response.status_code == 200
    assert "<dialog" in response.text, "the layer expects a modal, not a bare fragment"
    assert "<html" not in response.text
    assert 'class="badge badge-queued"' in response.text
    assert "The run failed." not in response.text


def test_console_refusal_renders_as_a_component(client, container):
    """Refusals are read and acted on, so they get the same treatment as any panel."""
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    client.put(
        f"/api/v1/artists/{artist['id']}/consent",
        json={"granted": True, "signed_by": "A. T."},
    )
    song = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"}
    ).json()
    kit = client.post("/api/v1/kits", json={"song_id": song["id"], "video_count": 1}).json()

    response = client.post(
        f"/ui/kits/{kit['id']}/retry",
        headers={"HX-Request": "true", "HX-Target": f"kit-{kit['id']}"},
    )
    assert response.status_code == 409
    assert "already on its way" in response.text
    assert "rk:kits" not in response.headers.get("HX-Trigger", ""), (
        "a refusal changed nothing, so nothing should be told to re-read"
    )
