"""JSON API, v1.

Mounted at `/api/v1`. The UI does not go through it — templates call the same services
directly — so this surface exists for scripts, the worker, and anything that comes
later, and it can change shape without dragging the console with it.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Response, UploadFile, status

from remixkit.api.schemas import (
    ApprovalIn,
    ArtistIn,
    ArtistPatch,
    ConsentIn,
    HookIn,
    IdentityIn,
    KitIn,
    MeasurementIn,
    SongIn,
    UploadUrlIn,
)
from remixkit.deps import (
    Artists,
    CurrentPrincipal,
    Delivery,
    Identities,
    Kits,
    Songs,
    Verify,
    get_container,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ---------------------------------------------------------------- artists
@router.get("/artists")
def list_artists(principal: CurrentPrincipal, artists: Artists):
    return artists.list(principal)


@router.post("/artists", status_code=status.HTTP_201_CREATED)
def create_artist(body: ArtistIn, principal: CurrentPrincipal, artists: Artists):
    return artists.create(principal, name=body.name, bio=body.bio, links=body.links)


@router.get("/artists/{artist_id}")
def get_artist(artist_id: str, principal: CurrentPrincipal, artists: Artists):
    return artists.get(principal, artist_id)


@router.patch("/artists/{artist_id}")
def update_artist(artist_id: str, body: ArtistPatch, principal: CurrentPrincipal, artists: Artists):
    return artists.update(principal, artist_id, name=body.name, bio=body.bio, links=body.links)


@router.delete("/artists/{artist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_artist(artist_id: str, principal: CurrentPrincipal, artists: Artists):
    artists.delete(principal, artist_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/artists/{artist_id}/consent")
def set_consent(artist_id: str, body: ConsentIn, principal: CurrentPrincipal, artists: Artists):
    return artists.set_consent(
        principal, artist_id, granted=body.granted, signed_by=body.signed_by, notes=body.notes
    )


@router.put("/artists/{artist_id}/approval")
def approve_artist(artist_id: str, body: ApprovalIn, principal: CurrentPrincipal, artists: Artists):
    return artists.set_approval(principal, artist_id, body.state)


# ---------------------------------------------------------------- identities
@router.get("/artists/{artist_id}/identities")
def list_identities(artist_id: str, principal: CurrentPrincipal, identities: Identities):
    return identities.list_for_artist(principal, artist_id)


@router.post("/artists/{artist_id}/identities", status_code=status.HTTP_201_CREATED)
def create_identity(
    artist_id: str, body: IdentityIn, principal: CurrentPrincipal, identities: Identities
):
    return identities.create_version(
        principal,
        artist_id,
        structural_features=body.structural_features,
        wardrobe=body.wardrobe,
        negatives=body.negatives,
    )


@router.put("/identities/{identity_id}/approval")
def approve_identity(
    identity_id: str, body: ApprovalIn, principal: CurrentPrincipal, identities: Identities
):
    return identities.set_approval(principal, identity_id, body.state)


# ---------------------------------------------------------------- songs
@router.get("/artists/{artist_id}/songs")
def list_songs(artist_id: str, principal: CurrentPrincipal, songs: Songs):
    return songs.list_for_artist(principal, artist_id)


@router.post("/artists/{artist_id}/songs", status_code=status.HTTP_201_CREATED)
def create_song(artist_id: str, body: SongIn, principal: CurrentPrincipal, songs: Songs):
    return songs.create(
        principal,
        artist_id,
        title=body.title,
        bpm=body.bpm,
        bpm_method=body.bpm_method,
        isrc=body.isrc,
        spotify_url=body.spotify_url,
    )


@router.get("/songs/{song_id}")
def get_song(song_id: str, principal: CurrentPrincipal, songs: Songs):
    return songs.get(principal, song_id)


@router.patch("/songs/{song_id}/measurement")
def set_measurement(song_id: str, body: MeasurementIn, principal: CurrentPrincipal, songs: Songs):
    return songs.set_measurement(
        principal, song_id, bpm=body.bpm, bpm_method=body.bpm_method, drop_ms=body.drop_ms
    )


@router.patch("/songs/{song_id}/hook")
def set_hook(song_id: str, body: HookIn, principal: CurrentPrincipal, songs: Songs):
    return songs.set_hook(principal, song_id, start_ms=body.start_ms, end_ms=body.end_ms)


@router.post("/songs/{song_id}/master-upload-url")
def master_upload_url(song_id: str, body: UploadUrlIn, principal: CurrentPrincipal, songs: Songs):
    """Presigned PUT. The master never transits this process (§2b rule 2)."""
    return songs.master_upload_url(principal, song_id, content_type=body.content_type)


# ---------------------------------------------------------------- kits
@router.get("/kits")
def list_kits(principal: CurrentPrincipal, kits: Kits, artist_id: str | None = None):
    return kits.list(principal, artist_id=artist_id)


@router.post("/kits", status_code=status.HTTP_202_ACCEPTED)
def request_kit(body: KitIn, principal: CurrentPrincipal, kits: Kits):
    """202, not 201 — the work is queued, not done (§2b rule 3)."""
    return kits.request(
        principal,
        song_id=body.song_id,
        name=body.name,
        video_count=body.video_count,
        hook_lines=body.hook_lines,
        tts_text=body.tts_text,
        budget_cents=body.budget_cents,
    )


@router.get("/kits/{kit_id}")
def get_kit(kit_id: str, principal: CurrentPrincipal, kits: Kits):
    return kits.get(principal, kit_id)


@router.put("/kits/{kit_id}/approval")
def approve_kit(kit_id: str, body: ApprovalIn, principal: CurrentPrincipal, kits: Kits):
    return kits.set_approval(principal, kit_id, body.state)


@router.get("/kits/{kit_id}/assets/{asset_id}/download")
def download_asset(kit_id: str, asset_id: str, principal: CurrentPrincipal, delivery: Delivery):
    """The delivered copy — with the run's manifest embedded in the file.

    `X-RemixKit-Provenance` says which happened, so a caller never has to assume an
    asset is disclosed when it could not be.
    """
    result = delivery.asset(principal, kit_id, asset_id)
    headers = {
        "Content-Disposition": f'attachment; filename="{_ascii(result.filename)}"',
        "X-RemixKit-Provenance": "embedded" if result.manifest_embedded else "absent",
    }
    if result.note:
        headers["X-RemixKit-Provenance-Note"] = _ascii(result.note)
    return Response(content=result.data, media_type=result.media_type, headers=headers)


def _ascii(value: str) -> str:
    """HTTP headers are latin-1. Our own copy uses em dashes, so anything reaching a
    header gets flattened rather than raising on the way out."""
    return value.encode("ascii", "replace").decode("ascii")


# ---------------------------------------------------------------- worker + verify
@router.post("/internal/worker/run-kit")
def worker_run_kit(payload: dict):
    """The queue consumer's entry point.

    Unauthenticated by design *today* because nothing here is authenticated. In the
    deployed shape this route is not exposed: Batch invokes `worker.py` directly and
    the Lambda's Function URL does not route `/internal/*`. When auth lands, this is
    the first route that gets a real guard.
    """
    container = get_container()
    kit = container.kits.run(payload["tenant_id"], payload["kit_id"])
    return {"kit_id": kit.id, "status": kit.status, "cost_cents": kit.total_cost_cents}


@router.post("/verify")
async def verify_asset(verify: Verify, file: UploadFile = File(...)):
    """Upload an asset or a manifest → provenance report."""
    data = await file.read()
    return verify.verify_bytes(data, file.filename or "").to_dict()
