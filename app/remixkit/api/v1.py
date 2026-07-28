"""JSON API, v1.

Mounted at `/api/v1`. The UI does not go through it — templates call the same services
directly — so this surface exists for scripts, the worker, and anything that comes
later, and it can change shape without dragging the console with it.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from remixkit.api.schemas import (
    ApprovalIn,
    ArtistIn,
    ArtistPatch,
    ConsentIn,
    HookIn,
    IdentityIn,
    KitIn,
    MasterIn,
    MeasurementIn,
    RequestCodeIn,
    SectionIn,
    SectionPatch,
    SongIn,
    UploadUrlIn,
    VerifyCodeIn,
)
from remixkit.deps import (
    Accounts,
    Analysis,
    Artists,
    CurrentPrincipal,
    Delivery,
    Identities,
    Kits,
    Recommendations,
    Songs,
    Verify,
    get_container,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ---------------------------------------------------------------- auth
# The only routes here that do not take a `CurrentPrincipal` — necessarily, since their
# whole job is to produce one. Everything else in this file is unreachable without the
# token these two hand out (when `RK_AUTH_BACKEND=otp`).
@router.post("/auth/request-code", tags=["auth"])
def request_code(body: RequestCodeIn, accounts: Accounts):
    """Email a sign-in code to an allowlisted address."""
    sent = accounts.request_code(body.email)
    payload = {
        "email": sent.email,
        "expires_at": sent.expires_at,
        "delivered": sent.delivered,
    }
    if sent.dev_code:
        # Only ever set by a dev process with no mailer configured — see
        # `services.accounts.CodeSent.dev_code` for why both conditions are required.
        payload["dev_code"] = sent.dev_code
    return payload


@router.post("/auth/verify-code", tags=["auth"])
def verify_code(body: VerifyCodeIn, accounts: Accounts):
    """Exchange a code for a bearer token. Send it as `Authorization: Bearer <token>`."""
    account, token = accounts.verify_code(body.email, body.code)
    return {
        "token": token,
        "token_type": "bearer",
        "expires_in": get_container().settings.session_ttl_s,
        "account": account,
    }


@router.get("/auth/me", tags=["auth"])
def whoami(principal: CurrentPrincipal):
    """The caller, as the rest of the API sees them.

    Useful beyond debugging: it is the cheapest way for a script to check whether its
    token is still good before starting a long job.
    """
    return {
        "tenant_id": principal.tenant_id,
        "subject": principal.subject,
        "display_name": principal.display_name,
        "scopes": sorted(principal.scopes),
        "authenticated": principal.authenticated,
    }


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


@router.put("/songs/{song_id}/master")
def register_master(song_id: str, body: MasterIn, principal: CurrentPrincipal, songs: Songs):
    """Record a master after the bytes have landed. Refuses a key with nothing at it."""
    return songs.register_master(
        principal, song_id, key=body.key, content_type=body.content_type
    )


@router.get("/songs/{song_id}/sheet", response_class=Response)
def song_sheet(song_id: str, principal: CurrentPrincipal, songs: Songs):
    """The arrangement as text — the one endpoint meant to be read by a language model.

    `text/plain` rather than JSON on purpose. Everything else on this router returns the
    document so a caller can pick fields out of it; this returns the *whole measurement in
    one string*, because the consumer is something assembling a prompt and the useful unit
    there is a paragraph, not a tree it has to re-render into prose itself — which is
    exactly the step where a claim the analyser never made gets introduced.

    404 rather than an empty body when the song has not been measured. A prompt built
    around a blank sheet is a prompt about nothing, and it should fail where it is
    assembled rather than three services downstream.
    """
    song = songs.get(principal, song_id)
    sheet = song.analysis.sheet if song.analysis else None
    if not sheet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This song has not been measured. Upload a master and run the analysis.",
        )
    return Response(content=sheet, media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------- sections
@router.get("/songs/{song_id}/sections")
def list_sections(song_id: str, principal: CurrentPrincipal, songs: Songs):
    song = songs.get(principal, song_id)
    return {
        "primary_section_id": song.primary_section_id,
        "sections": song.ordered_sections,
    }


@router.post("/songs/{song_id}/sections", status_code=status.HTTP_201_CREATED)
def add_section(song_id: str, body: SectionIn, principal: CurrentPrincipal, songs: Songs):
    return songs.add_section(
        principal,
        song_id,
        start_ms=body.start_ms,
        end_ms=body.end_ms,
        role=body.role,
        label=body.label,
        notes=body.notes,
        make_primary=body.make_primary,
    )


@router.patch("/songs/{song_id}/sections/{section_id}")
def update_section(
    song_id: str, section_id: str, body: SectionPatch, principal: CurrentPrincipal, songs: Songs
):
    return songs.update_section(
        principal,
        song_id,
        section_id,
        start_ms=body.start_ms,
        end_ms=body.end_ms,
        role=body.role,
        label=body.label,
        notes=body.notes,
    )


@router.delete("/songs/{song_id}/sections/{section_id}")
def delete_section(song_id: str, section_id: str, principal: CurrentPrincipal, songs: Songs):
    return songs.remove_section(principal, song_id, section_id)


@router.put("/songs/{song_id}/sections/{section_id}/primary")
def set_primary_section(
    song_id: str, section_id: str, principal: CurrentPrincipal, songs: Songs
):
    """Which hook a kit cuts to when its brief names none."""
    return songs.set_primary_section(principal, song_id, section_id)


# ---------------------------------------------------------------- analysis
@router.post("/songs/{song_id}/analysis", status_code=status.HTTP_202_ACCEPTED)
def analyse_song(song_id: str, principal: CurrentPrincipal, analysis: Analysis):
    """202 — measuring a master is a queued job, not a request (§2b rule 3).

    503 with the missing piece named if this process has no analyser; the song is not
    marked as anything, because nothing was attempted.
    """
    song = analysis.request(principal, song_id)
    return {"song_id": song.id, "analysis": song.analysis}


@router.get("/songs/{song_id}/recommendations")
def song_recommendations(
    song_id: str, principal: CurrentPrincipal, songs: Songs, recommendations: Recommendations
):
    """Ranked hook windows with the rule each one passes or fails, and the basis for it.

    `blocked_on` is non-empty and `candidates` empty when the song has not been measured —
    which is the honest answer, not an empty list dressed up as "no good hooks".
    """
    song = songs.get(principal, song_id)
    report = recommendations.recommend(song)
    return {
        "computed": report.computed,
        "blocked_on": report.blocked_on,
        "findings": report.findings,
        "candidates": [
            {
                "section": candidate.section,
                "basis": candidate.basis,
                "rank_reason": candidate.rank_reason,
                "ready": candidate.ready,
                "checks": [
                    {"rule": c.rule, "state": c.state, "detail": c.detail}
                    for c in candidate.checks
                ],
                "suggested_start_ms": candidate.suggested_start_ms,
                "suggested_end_ms": candidate.suggested_end_ms,
            }
            for candidate in report.candidates
        ],
    }


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
        section_ids=body.section_ids,
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
