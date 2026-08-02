"""One-way sync, and the properties that keep it cheap and safe to re-run."""

from __future__ import annotations

import pytest

from remixkit.tools.sync_from_b2 import sync


class FakeSource:
    """A bucket that counts what it was asked to read."""

    def __init__(self, objects: dict[str, bytes], unreadable: set[str] | None = None):
        self._objects = objects
        self._unreadable = unreadable or set()
        self.gets: list[str] = []
        self.lists: list[str] = []
        self.puts: list[str] = []

    def list(self, prefix):
        self.lists.append(prefix)
        return [k for k in self._objects if k.startswith(prefix)]

    def get(self, key):
        self.gets.append(key)
        if key in self._unreadable:
            raise RuntimeError("cap exceeded")
        return self._objects[key]

    def put(self, key, data, content_type=None):  # pragma: no cover — must never run
        self.puts.append(key)
        raise AssertionError("the source must never be written to")

    def exists(self, key):
        return key in self._objects


class FakeDestination:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key, data, content_type=None):
        self.objects[key] = data
        return key

    def exists(self, key):
        return key in self.objects


DOCS = {
    "remixkit/tenants/t1/artists/art_1.yaml": b"id: art_1\n",
    "remixkit/tenants/t1/songs/sng_1.yaml": b"id: sng_1\n",
    "remixkit/tenants/t1/songs/sng_2.yaml": b"id: sng_2\n",
}
MEDIA = {
    "remixkit/tenants/t1/identities/idn_1/frames/abc.jpeg": b"x" * 5000,
    "remixkit/tenants/t1/masters/sng_1.wav": b"y" * 9000,
}
PREFIX = ["remixkit/tenants/t1"]


def test_documents_only_by_default():
    """The catalogue is what makes the console non-empty; the bytes it points at are two
    orders of magnitude larger and not always needed."""
    source = FakeSource({**DOCS, **MEDIA})
    dest = FakeDestination()

    copied, skipped, _failed, _ = sync(source, dest, PREFIX)

    assert copied == 3
    assert set(dest.objects) == set(DOCS)
    assert not any(k.endswith(".jpeg") for k in source.gets), "media was read anyway"


def test_media_is_opt_in():
    source = FakeSource({**DOCS, **MEDIA})
    dest = FakeDestination()

    copied, _, _failed, total = sync(source, dest, PREFIX, media=True)

    assert copied == 5
    assert total == sum(len(v) for v in {**DOCS, **MEDIA}.values())


def test_a_second_run_costs_almost_nothing():
    """What makes this safe to re-run. B2 bills per read, so a sync after adding one song
    must cost one transaction and not the whole catalogue again."""
    source = FakeSource(dict(DOCS))
    dest = FakeDestination()
    sync(source, dest, PREFIX)
    source.gets.clear()

    source._objects["remixkit/tenants/t1/songs/sng_3.yaml"] = b"id: sng_3\n"
    copied, skipped, _failed, _ = sync(source, dest, PREFIX)

    assert copied == 1 and skipped == 3
    assert source.gets == ["remixkit/tenants/t1/songs/sng_3.yaml"]


def test_a_dry_run_reads_nothing():
    """The point of a dry run against a metered API is that it costs nothing to ask."""
    source = FakeSource(dict(DOCS))
    dest = FakeDestination()

    copied, _, _failed, total = sync(source, dest, PREFIX, dry_run=True)

    assert copied == 3
    assert source.gets == [], "a dry run must not spend a single Class B"
    assert total == 0 and dest.objects == {}


def test_one_unreadable_object_does_not_abort_the_rest():
    """A cap reached mid-sync is the likeliest failure, and the run is resumable — so the
    other objects should already be on disk when it is re-run."""
    source = FakeSource(dict(DOCS), unreadable={"remixkit/tenants/t1/songs/sng_1.yaml"})
    dest = FakeDestination()

    copied, _, _failed, _ = sync(source, dest, PREFIX)

    assert copied == 2
    assert "remixkit/tenants/t1/songs/sng_1.yaml" not in dest.objects


def test_the_source_is_never_written_to():
    """A laptop must not be able to corrupt production. The local copy is a convenience,
    not a replica: nothing here reconciles, and nothing uploads."""
    source = FakeSource({**DOCS, **MEDIA})
    dest = FakeDestination()

    sync(source, dest, PREFIX, media=True, force=True)

    assert source.puts == []


def test_force_recopies_what_is_already_local():
    source = FakeSource(dict(DOCS))
    dest = FakeDestination()
    sync(source, dest, PREFIX)
    source.gets.clear()

    copied, skipped, _failed, _ = sync(source, dest, PREFIX, force=True)

    assert copied == 3 and skipped == 0


def test_runs_are_a_separate_prefix_and_opt_in():
    """Generated output is the largest thing in the bucket and the least necessary for the
    console to be usable."""
    objects = {
        **DOCS,
        "remixkit/runs/t1/2026-08-01/r1/assets/a.mp4": b"z" * 5_000_000,
    }
    source = FakeSource(objects)
    dest = FakeDestination()

    sync(source, dest, PREFIX, media=True)
    assert not any(k.startswith("remixkit/runs") for k in dest.objects)

    sync(source, dest, [*PREFIX, "remixkit/runs/t1"], media=True)
    assert any(k.startswith("remixkit/runs") for k in dest.objects)


def test_the_local_copy_is_readable_by_the_repository(tmp_path):
    """End to end: what lands on disk is what the app reads back, keys and all."""
    from remixkit.adapters.repo_documents import DocumentRepo
    from remixkit.adapters.storage_local import LocalStorage

    source = FakeSource(dict(DOCS))
    dest = LocalStorage(tmp_path)

    sync(source, dest, PREFIX)

    from pydantic import BaseModel

    class Doc(BaseModel):
        id: str

    repo = DocumentRepo(dest)
    assert repo.get("t1", "songs", "sng_1", Doc).id == "sng_1"
    assert {d.id for d in repo.list("t1", "songs", Doc)} == {"sng_1", "sng_2"}


def test_it_refuses_without_a_bucket(monkeypatch, capsys):
    """The bucket and SSM path stay in `.env` even when the backend is local, so an empty
    one means the operator is not set up rather than that the sync has nothing to do."""
    from remixkit.settings import get_settings
    from remixkit.tools import sync_from_b2

    settings = get_settings()
    monkeypatch.setattr(settings, "b2_bucket", "", raising=False)
    monkeypatch.setattr("remixkit.bootstrap.load_secrets", lambda *a, **k: 0)
    monkeypatch.setattr("remixkit.settings.get_settings", lambda: settings)

    assert sync_from_b2.main([]) == 2
    assert "No B2 bucket configured" in capsys.readouterr().err


def test_the_documented_flags_exist():
    """The docstring promises these. A tool whose usage line is wrong is worse than none,
    because it is discovered at the moment somebody is already blocked."""
    from remixkit.tools.sync_from_b2 import build_parser

    args = build_parser().parse_args(["--dry-run", "--media", "--runs", "--force"])

    assert args.dry_run and args.media and args.runs and args.force
    assert build_parser().parse_args([]).tenant is None, "tenant defaults to the setting"
    assert build_parser().parse_args(["--tenant", "dev-local"]).tenant == "dev-local"


def test_a_run_that_copied_nothing_says_why():
    """The gap this closes, found by running it against a capped bucket.

    Every one of twenty objects was refused, and the summary read `copied 0 object(s),
    skipped 0 already local` — true, cheerful, and indistinguishable from a tenant that was
    simply empty. The per-object warnings were there to read; the total that would have
    made them impossible to miss was not.
    """
    source = FakeSource(dict(DOCS), unreadable=set(DOCS))
    dest = FakeDestination()

    result = sync(source, dest, PREFIX)

    assert result.copied == 0
    assert result.failed == 3, "a failure nobody totals is a failure nobody notices"
    assert dest.objects == {}


def test_the_exit_code_reports_failure(monkeypatch, capsys):
    """Non-zero, because the likeliest cause is the cap and the likeliest next move is to
    run this again later — which a script wrapping it needs to be able to detect."""
    from remixkit.settings import get_settings
    from remixkit.tools import sync_from_b2

    settings = get_settings()
    monkeypatch.setattr(settings, "b2_bucket", "bucket", raising=False)
    monkeypatch.setattr("remixkit.bootstrap.load_secrets", lambda *a, **k: 0)
    monkeypatch.setattr("remixkit.settings.get_settings", lambda: settings)
    monkeypatch.setattr(sync_from_b2, "sync", lambda *a, **k: sync_from_b2.SyncResult(0, 0, 20, 0))
    monkeypatch.setattr("remixkit.adapters.storage_b2.B2Storage", lambda *a, **k: object())
    monkeypatch.setattr(
        "remixkit.adapters.storage_local.LocalStorage",
        lambda *a, **k: type("Dest", (), {"root": "/tmp/x"})(),
    )

    assert sync_from_b2.main([]) == 1
    out = capsys.readouterr().out
    assert "failed 20" in out
    assert "reset at UTC midnight" in out, "say what to do next"
