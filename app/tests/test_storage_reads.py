"""What a read costs, and what a failed one is allowed to claim.

Both halves of one production incident. The console reported 404 on a song that was in
the bucket, under the right tenant, listed correctly by the roster beside it — because a
B2 daily cap had been reached and every signal pointed away from storage.

    Cannot download file, download bandwidth or transaction (Class B) cap exceeded.

B2 refuses a capped read with **403**, not 404. The S3 backend's `exists()` maps 403 to
`False` on purpose — scoped keys with `ReadFiles` but not `ListFiles` answer 403 for HEAD
on a missing key — and `None` from the repository means `NotFound` means 404. So an outage
was rendered as an absence, which is the one failure nobody can act on.

The other half is why the cap was reached at all: a presigned URL is signed against the
wall clock to the second, so every render minted a new URL for an unchanged object and no
browser cache ever hit.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from remixkit.adapters.repo_documents import DocumentRepo


class Doc(BaseModel):
    id: str
    title: str = ""


class _Refusal(Exception):
    """A storage error the vendor classified as something other than a miss.

    Shaped like `genblaze_core.exceptions.StorageError` in the one way the repository
    reads: an `error_code` whose `name` says what happened.
    """

    def __init__(self, name: str = "ACCESS_DENIED"):
        super().__init__(f"storage said {name}")
        self.error_code = type("Code", (), {"name": name})()


class FakeStorage:
    """Enough of the storage port to count reads and choose how they fail."""

    def __init__(self, objects: dict[str, bytes], fail: Exception | None = None):
        self._objects = objects
        self._fail = fail
        self.gets: list[str] = []
        self.exists_calls: list[str] = []

    def get(self, key: str) -> bytes:
        self.gets.append(key)
        if self._fail is not None:
            raise self._fail
        try:
            return self._objects[key]
        except KeyError:
            raise FileNotFoundError(key) from None

    def exists(self, key: str) -> bool:
        self.exists_calls.append(key)
        return key in self._objects

    def list(self, prefix: str) -> list[str]:
        return [k for k in self._objects if k.startswith(prefix)]


def _repo(objects, fail=None):
    storage = FakeStorage(objects, fail)
    return DocumentRepo(storage), storage


KEY = "remixkit/tenants/t1/songs/sng_1.yaml"


def test_a_missing_document_is_still_absent():
    repo, _ = _repo({})
    assert repo.get("t1", "songs", "sng_1", Doc) is None


def test_a_present_document_still_loads():
    repo, _ = _repo({KEY: b"id: sng_1\ntitle: Show Invite\n"})
    assert repo.get("t1", "songs", "sng_1", Doc).title == "Show Invite"


def test_a_capped_bucket_is_not_reported_as_a_missing_song():
    """The incident, in one assertion.

    B2 answers a capped read with 403, which the backend reads as "not there", which the
    service reads as `NotFound`, which the console renders as 404 — a catalogue accusing
    itself for a storage problem. Only the vendor gets to say a thing is absent.
    """
    repo, _ = _repo({KEY: b"id: sng_1\n"}, fail=_Refusal("ACCESS_DENIED"))

    with pytest.raises(_Refusal):
        repo.get("t1", "songs", "sng_1", Doc)


def test_only_a_not_found_classification_means_absent():
    """`NOT_FOUND` is the vendor's word for it. Nothing else is read as absence."""
    repo, _ = _repo({KEY: b"id: sng_1\n"}, fail=_Refusal("NOT_FOUND"))
    assert repo.get("t1", "songs", "sng_1", Doc) is None


@pytest.mark.parametrize("code", ["RATE_LIMIT", "AUTH_FAILURE", "NETWORK", "TIMEOUT"])
def test_no_other_failure_is_quietly_an_absence(code):
    repo, _ = _repo({KEY: b"id: sng_1\n"}, fail=_Refusal(code))
    with pytest.raises(_Refusal):
        repo.get("t1", "songs", "sng_1", Doc)


def test_a_document_costs_one_read_not_two():
    """`exists()`-then-`get()` billed twice per document and asked the wrong question with
    the first. The cap this drains is measured in transactions, so halving them is the
    other half of not reaching it."""
    repo, storage = _repo({KEY: b"id: sng_1\n"})

    repo.get("t1", "songs", "sng_1", Doc)

    assert len(storage.gets) == 1
    assert storage.exists_calls == [], "a HEAD before every read is a read nobody needed"


def test_a_bucket_that_will_not_answer_does_not_render_as_an_empty_roster():
    """The listing used to wrap the read in `except Exception`, so a bucket refusing every
    object looked exactly like a label with no songs."""
    repo, _ = _repo(
        {KEY: b"id: sng_1\n"}, fail=_Refusal("ACCESS_DENIED")
    )
    with pytest.raises(_Refusal):
        repo.list("t1", "songs", Doc)


def test_a_malformed_document_is_still_only_one_broken_row():
    """The tolerance that was there for a reason keeps working — narrowed to parsing."""
    repo, _ = _repo(
        {
            KEY: b"id: sng_1\ntitle: Fine\n",
            "remixkit/tenants/t1/songs/sng_2.yaml": b"{{{ not yaml",
        }
    )
    assert [d.id for d in repo.list("t1", "songs", Doc)] == ["sng_1"]


# ---------------------------------------------------------------- presigned URLs
def test_a_presigned_url_is_stable_so_the_browser_can_cache_it():
    """SigV4 signs the clock to the second, so signing twice returns two URLs for one
    unchanged object — and a browser caches by URL, so every render re-downloaded every
    frame and clip in full. That is what drained the cap."""
    b2 = pytest.importorskip("remixkit.adapters.storage_b2")

    signed = []

    class Backend:
        def presigned_get_url(self, key, expires_in):
            signed.append(key)
            return f"https://b2/{key}?sig={len(signed)}"

        def put(self, key, data, content_type=None, extra_args=None):
            return key

        def delete(self, key):
            return None

    storage = object.__new__(b2.B2Storage)
    storage._backend = Backend()
    storage._presigned = {}

    first = storage.presign_get("frames/a.jpeg")
    assert storage.presign_get("frames/a.jpeg") == first
    assert storage.presign_get("frames/a.jpeg") == first
    assert signed == ["frames/a.jpeg"], "one signature serves every render in the window"


def test_replacing_an_object_re_signs_it():
    """Stability rests on keys being immutable in practice. Where they are not, the write
    is what says so — otherwise a re-uploaded master serves stale bytes from cache."""
    b2 = pytest.importorskip("remixkit.adapters.storage_b2")

    n = [0]

    class Backend:
        def presigned_get_url(self, key, expires_in):
            n[0] += 1
            return f"https://b2/{key}?sig={n[0]}"

        def put(self, key, data, content_type=None, extra_args=None):
            return key

        def delete(self, key):
            return None

    storage = object.__new__(b2.B2Storage)
    storage._backend = Backend()
    storage._presigned = {}

    before = storage.presign_get("masters/m.wav")
    storage.put("masters/m.wav", b"new bytes")
    assert storage.presign_get("masters/m.wav") != before

    storage.delete("masters/m.wav")
    assert n[0] == 2


# -------------------------------------------------------------- Cache-Control on upload
def _recording_backend():
    """A backend that remembers the ExtraArgs each `put` was given."""
    calls: list[dict] = []

    class Backend:
        def put(self, key, data, content_type=None, extra_args=None):
            calls.append({"key": key, "extra_args": extra_args or {}})
            return key

        def presigned_get_url(self, key, expires_in):
            return f"https://b2/{key}?sig=1"

        def delete(self, key):
            return None

    return Backend(), calls


def test_uploads_carry_a_cache_control_because_b2_sets_none_itself():
    """The other half of the stable-URL fix, and the half that was missing.

    A stable URL is only a cache *key*; it is not permission to cache. Per B2's own
    Cache-Control documentation, buckets created on or after 2021-09-08 send no
    `Cache-Control` header at all — so the browser was handed a repeatable name for a
    response it had never been told it could keep.
    """
    b2 = pytest.importorskip("remixkit.adapters.storage_b2")

    backend, calls = _recording_backend()
    storage = object.__new__(b2.B2Storage)
    storage._backend = backend
    storage._presigned = {}

    storage.put("tenants/t/frames/a.jpeg", b"bytes", content_type="image/jpeg")

    assert calls[0]["extra_args"]["CacheControl"] == "private, max-age=3600, immutable"


def test_the_cache_directive_suppresses_revalidation_not_just_the_download():
    """`immutable` is the clause that protects the Class B cap.

    A fresh-but-revalidating browser still sends a conditional request, and on B2 that
    request is itself a billable Class B transaction against the same daily cap this file
    exists to document. `immutable` is what stops it being sent.
    """
    b2 = pytest.importorskip("remixkit.adapters.storage_b2")

    directive = b2._cache_control_for("tenants/t/kits/k/clip.mp4")

    assert "immutable" in directive
    assert "private" in directive, "tenant media must not be held by shared caches"
    assert "no-store" not in directive


def test_documents_are_never_cached_because_they_are_overwritten_in_place():
    """`repo_documents` is last-write-wins, so an artist edited twice in an hour must not
    be served from the first version. Media keys rotate their URL on write; a document
    key does not, so this one is settled by the directive instead."""
    b2 = pytest.importorskip("remixkit.adapters.storage_b2")

    backend, calls = _recording_backend()
    storage = object.__new__(b2.B2Storage)
    storage._backend = backend
    storage._presigned = {}

    storage.put("tenants/t/artists/a1.yaml", b"id: a1", content_type="application/yaml")

    assert calls[0]["extra_args"]["CacheControl"] == "no-cache"


# ------------------------------------------------------ the connector's own vocabulary
def test_the_backend_still_tells_a_miss_from_a_refusal():
    """`_is_missing` trusts the connector's classification, so this pins the contract it
    trusts. If a future release stops classifying 404 as `NOT_FOUND`, every genuinely
    missing document starts raising instead of 404-ing, and the failure is this test
    rather than a console that will not render."""
    pytest.importorskip("genblaze_s3")
    from botocore.exceptions import ClientError
    from genblaze_s3.backend import classify_botocore_error

    def classify(code, status):
        exc = ClientError(
            {"Error": {"Code": code, "Message": "x"},
             "ResponseMetadata": {"HTTPStatusCode": status}},
            "GetObject",
        )
        return classify_botocore_error(exc, operation="get", key="k").error_code.name

    assert classify("NoSuchKey", 404) == "NOT_FOUND"
    assert classify("404", 404) == "NOT_FOUND"
    # What a capped bucket answers. Absence is the one thing this must not be called.
    assert classify("AccessDenied", 403) == "ACCESS_DENIED"
    assert classify("403", 403) == "ACCESS_DENIED"


def test_storage_refusing_to_answer_is_a_503_that_quotes_the_vendor(client, monkeypatch):
    """Not a 500 and emphatically not a 404. The bucket is refusing right now — a
    condition with a clock on it — and B2's own sentence is the only actionable part."""
    from genblaze_core.exceptions import StorageError

    def refuse(*_args, **_kwargs):
        raise StorageError(
            "Storage get for 'k' failed: Cannot download file, download bandwidth or "
            "transaction (Class B) cap exceeded."
        )

    monkeypatch.setattr("remixkit.adapters.repo_documents.DocumentRepo.list", refuse)

    response = client.get("/console/catalogue", follow_redirects=False)

    assert response.status_code == 503
    assert "cap exceeded" in response.json()["detail"]
