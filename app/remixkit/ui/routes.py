"""The console.

Server-rendered Jinja + htmx, in the same FastAPI app — BUILD-SPEC §2's frontend
decision and web/README's stated precedent, which resolves open decision §13.5.

"Componentized" here means every fragment under `templates/components/` renders
standalone and is addressable by a route that returns *just that fragment*. htmx swaps
them in place. So the same `_kit_row.html` is used by the full page render, by the
create-kit response, and by the status poll — one definition, three call sites. If this
is later replaced by a React or Next.js front end, these routes are already the
component boundaries, and `api/v1.py` is already the JSON to drive them.

Every handler resolves a `Principal` even though nothing is authenticated, so the
tenant it reads is the tenant a real login would supply.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from remixkit.deps import (
    Accounts,
    Artists,
    CurrentPrincipal,
    Identities,
    Kits,
    MaybePrincipal,
    Songs,
    Verify,
    get_container,
)
from remixkit.domain.models import ApprovalState
from remixkit.services.errors import ServiceError

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def asset_url(asset) -> str:
    """A URL the browser can fetch *now*, minted at render time.

    The kit document stores the URL Genblaze reported when the asset was written, and
    on B2 that is a **presigned** URL with an expiry. Persisting it means the console
    looks correct immediately after a kit finishes and then quietly fills with broken
    tiles once the signature lapses — the kind of bug that only shows up in a demo,
    long after the code that caused it.

    So the durable thing is the key, and the URL is derived from it on every render.
    On local storage this is the `/files/...` route and costs nothing.
    """
    container = get_container()
    if asset.key:
        try:
            return container.storage.presign_get(asset.key, expires_in=3600)
        except Exception:
            log.warning("could not presign %s", asset.key)
    return asset.url or ""


templates.env.globals["asset_url"] = asset_url
router = APIRouter(tags=["console"])
log = logging.getLogger(__name__)


def _viewer(request: Request) -> dict | None:
    """Who the layout should say is signed in, read from the session cookie.

    Deliberately not the route's `Principal`: adding a parameter to twenty handlers so
    the topbar can render a name would put auth into every signature for a decoration.
    This reads the same signed token the provider validates — `read_session` verifies
    the HMAC and the expiry and does no I/O — so the menu cannot show a name that the
    next request would reject.
    """
    container = get_container()
    token = request.cookies.get(container.settings.session_cookie, "")
    return container.accounts.read_session(token) if token else None


def _render(request: Request, template: str, **ctx) -> HTMLResponse:
    container = get_container()
    return templates.TemplateResponse(
        request,
        template,
        {
            "env": container.describe(),
            "ApprovalState": ApprovalState,
            "viewer": _viewer(request),
            **ctx,
        },
    )


def _error_fragment(request: Request, exc: ServiceError, target: str = "") -> HTMLResponse:
    """Service refusals render as a component, not a stack trace.

    The rights refusal in particular is a thing the label needs to read and act on, so
    it gets the same visual treatment as any other panel — and a 422 status so htmx
    and scripts agree about what happened.
    """
    response = _render(request, "components/_error.html", message=str(exc), target=target)
    response.status_code = exc.status_code
    return response


# ---------------------------------------------------------------- pages
@router.get("/", response_class=HTMLResponse)
def roster(request: Request, principal: CurrentPrincipal, artists: Artists, kits: Kits):
    return _render(
        request,
        "pages/roster.html",
        artists=artists.list(principal),
        kits=kits.list(principal)[:5],
    )


@router.get("/artists/{artist_id}", response_class=HTMLResponse)
def artist_detail(
    request: Request,
    artist_id: str,
    principal: CurrentPrincipal,
    artists: Artists,
    identities: Identities,
    songs: Songs,
    kits: Kits,
):
    try:
        artist = artists.get(principal, artist_id)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _render(
        request,
        "pages/artist.html",
        artist=artist,
        identity=identities.current(principal, artist_id),
        identities=identities.list_for_artist(principal, artist_id),
        songs=songs.list_for_artist(principal, artist_id),
        kits=kits.list(principal, artist_id=artist_id),
    )


@router.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request):
    return _render(request, "pages/verify.html", report=None)


# ---------------------------------------------------------------- sign in
LOGIN_PATH = "/auth/login"


def _safe_next(raw: str) -> str:
    """Only same-origin paths survive.

    `?next=` is attacker-controllable — it arrives in a link someone can send. Without
    this, `?next=https://evil.example` turns the label's own login page into an open
    redirect, which is the classic way a phishing link gets a legitimate domain in front
    of it. A value must start with a single `/` to be used; everything else becomes the
    roster.
    """
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


@router.get(LOGIN_PATH, response_class=HTMLResponse)
def login_page(request: Request, principal: MaybePrincipal, next: str = "/"):
    """The one page that renders without a session — and redirects if there is one."""
    destination = _safe_next(next)
    if principal is not None and principal.authenticated:
        return RedirectResponse(destination, status_code=303)
    return _render(request, "pages/login.html", next=destination, email="", message=None)


@router.post("/ui/auth/request-code", response_class=HTMLResponse)
def ui_request_code(
    request: Request,
    accounts: Accounts,
    email: str = Form(...),
    next: str = Form("/"),
):
    try:
        sent = accounts.request_code(email)
    except ServiceError as exc:
        # The address form comes back with the message on it, rather than the generic
        # error fragment — which would swap the card's only form out and leave a
        # mistyped address needing a page reload to fix.
        response = _render(
            request,
            "components/_email_form.html",
            email=email.strip(),
            next=_safe_next(next),
            message=str(exc),
        )
        response.status_code = exc.status_code
        return response
    return _render(
        request,
        "components/_otp_form.html",
        email=sent.email,
        next=_safe_next(next),
        delivered=sent.delivered,
        dev_code=sent.dev_code,
        message=None,
    )


@router.post("/ui/auth/verify-code")
def ui_verify_code(
    request: Request,
    accounts: Accounts,
    email: str = Form(...),
    code: str = Form(...),
    next: str = Form("/"),
):
    try:
        account, token = accounts.verify_code(email, code)
    except ServiceError as exc:
        # The form comes back rather than a bare error, so a mistyped digit costs one
        # retype rather than a whole round trip through the email.
        response = _render(
            request,
            "components/_otp_form.html",
            email=email.strip().lower(),
            next=_safe_next(next),
            delivered=True,
            dev_code=None,
            message=str(exc),
        )
        response.status_code = exc.status_code
        return response

    # 204 + HX-Redirect rather than a 303: htmx issued this request and would otherwise
    # swap the roster page into the login card. The browser does the navigation.
    response = Response(status_code=204, headers={"HX-Redirect": _safe_next(next)})
    _set_session_cookie(response, token)
    log.info("signed in: %s", account.email)
    return response


@router.get("/auth/logout")
def logout(request: Request):
    response = RedirectResponse(LOGIN_PATH, status_code=303)
    container = get_container()
    # Sessions are stateless, so this is the only revocation there is: drop the browser's
    # copy. The token stays valid until it expires, which is why the TTL is a week and
    # not a year — see services/accounts.py.
    response.delete_cookie(
        container.settings.session_cookie, path="/", samesite="lax", httponly=True
    )
    return response


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_container().settings
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=settings.session_ttl_s,
        path="/",
        # httpOnly: console JavaScript never needs the token, so an XSS should not be
        # able to read it. SameSite=Lax: the console is all same-origin GETs and htmx
        # POSTs, so nothing legitimate is cross-site, and Lax blocks the CSRF shape
        # where another origin POSTs a form at a signed-in browser.
        httponly=True,
        secure=settings.session_cookie_secure and settings.env != "dev",
        samesite="lax",
    )


# ---------------------------------------------------------------- artist fragments
@router.post("/ui/artists", response_class=HTMLResponse)
def ui_create_artist(
    request: Request,
    principal: CurrentPrincipal,
    artists: Artists,
    name: str = Form(...),
    bio: str = Form(""),
    spotify: str = Form(""),
    instagram: str = Form(""),
):
    try:
        artists.create(
            principal, name=name, bio=bio, links={"spotify": spotify, "instagram": instagram}
        )
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(request, "components/_roster.html", artists=artists.list(principal))


@router.delete("/ui/artists/{artist_id}", response_class=HTMLResponse)
def ui_delete_artist(request: Request, artist_id: str, principal: CurrentPrincipal, artists: Artists):
    try:
        artists.delete(principal, artist_id)
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(request, "components/_roster.html", artists=artists.list(principal))


@router.post("/ui/artists/{artist_id}/consent", response_class=HTMLResponse)
def ui_set_consent(
    request: Request,
    artist_id: str,
    principal: CurrentPrincipal,
    artists: Artists,
    granted: str = Form(""),
    signed_by: str = Form(""),
    notes: str = Form(""),
):
    try:
        artist = artists.set_consent(
            principal,
            artist_id,
            granted=granted.lower() in ("1", "true", "on", "yes"),
            signed_by=signed_by,
            notes=notes,
        )
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(request, "components/_consent.html", artist=artist)


@router.post("/ui/artists/{artist_id}/identity", response_class=HTMLResponse)
def ui_save_identity(
    request: Request,
    artist_id: str,
    principal: CurrentPrincipal,
    identities: Identities,
    structural_features: str = Form(""),
    wardrobe: str = Form(""),
    negatives: str = Form(""),
):
    def split(raw: str) -> list[str]:
        return [p.strip() for p in raw.split(",") if p.strip()]

    try:
        identity = identities.create_version(
            principal,
            artist_id,
            structural_features=structural_features or None,
            wardrobe=split(wardrobe),
            negatives=split(negatives),
        )
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(
        request,
        "components/_identity.html",
        identity=identity,
        identities=identities.list_for_artist(principal, artist_id),
        artist_id=artist_id,
    )


# ---------------------------------------------------------------- song fragments
@router.post("/ui/artists/{artist_id}/songs", response_class=HTMLResponse)
def ui_create_song(
    request: Request,
    artist_id: str,
    principal: CurrentPrincipal,
    songs: Songs,
    title: str = Form(...),
    bpm: str = Form(""),
    bpm_method: str = Form(""),
    spotify_url: str = Form(""),
):
    try:
        songs.create(
            principal,
            artist_id,
            title=title,
            bpm=float(bpm) if bpm.strip() else None,
            bpm_method=bpm_method,
            spotify_url=spotify_url,
        )
    except ValueError:
        return _error_fragment(request, ServiceError("BPM must be a number."))
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(
        request,
        "components/_songs.html",
        songs=songs.list_for_artist(principal, artist_id),
        artist_id=artist_id,
    )


@router.post("/ui/songs/{song_id}/hook", response_class=HTMLResponse)
def ui_set_hook(
    request: Request,
    song_id: str,
    principal: CurrentPrincipal,
    songs: Songs,
    start_ms: int = Form(...),
    end_ms: int = Form(...),
):
    try:
        song = songs.set_hook(principal, song_id, start_ms=start_ms, end_ms=end_ms)
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(request, "components/_song_row.html", song=song, artist_id=song.artist_id)


# ---------------------------------------------------------------- kit fragments
@router.post("/ui/kits", response_class=HTMLResponse)
def ui_create_kit(
    request: Request,
    principal: CurrentPrincipal,
    kits: Kits,
    song_id: str = Form(...),
    artist_id: str = Form(...),
    video_count: int = Form(3),
    hook_lines: str = Form(""),
):
    try:
        kits.request(
            principal,
            song_id=song_id,
            video_count=video_count,
            hook_lines=[line.strip() for line in hook_lines.splitlines() if line.strip()],
        )
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(
        request, "components/_kits.html", kits=kits.list(principal, artist_id=artist_id), artist_id=artist_id
    )


@router.get("/ui/kits/{kit_id}/row", response_class=HTMLResponse)
def ui_kit_row(request: Request, kit_id: str, principal: CurrentPrincipal, kits: Kits):
    """The htmx poll target while a kit is running.

    Polling rather than websockets on purpose: BUILD-SPEC §2b lists real-time
    websockets under deliberately deferred, and a kit takes minutes, so a 3-second
    poll on one row costs nothing and removes a whole class of infrastructure.
    """
    try:
        kit = kits.get(principal, kit_id)
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(request, "components/_kit_row.html", kit=kit)


@router.post("/ui/kits/{kit_id}/approval", response_class=HTMLResponse)
def ui_approve_kit(
    request: Request,
    kit_id: str,
    principal: CurrentPrincipal,
    kits: Kits,
    state: str = Form(...),
):
    try:
        kit = kits.set_approval(principal, kit_id, ApprovalState(state))
    except ServiceError as exc:
        return _error_fragment(request, exc)
    return _render(request, "components/_kit_row.html", kit=kit)


# ---------------------------------------------------------------- verify fragment
@router.post("/ui/verify", response_class=HTMLResponse)
async def ui_verify(request: Request, verify: Verify, file: UploadFile = File(...)):
    data = await file.read()
    report = verify.verify_bytes(data, file.filename or "")
    return _render(request, "components/_verify_report.html", report=report, filename=file.filename)


# ---------------------------------------------------------------- local file serving
@router.get("/files/{key:path}")
def serve_local_file(key: str, principal: CurrentPrincipal):
    """Dev-only asset serving for `storage_backend=local`.

    On B2 the browser gets a presigned URL and this route is never reached — which is
    the whole point of rule 2. It exists so the console shows real generated assets on
    a laptop instead of broken images.

    It takes a `Principal` for the same reason every other route does, and here it is
    load-bearing rather than ceremonial: this handler reads an arbitrary object key out
    of the URL, so leaving it open would have made it the one way around the login —
    fetch the bytes directly and skip the page that would have asked who you are.

    Documents are refused outright, signed in or not. The YAML under `tenants/` is the
    repository's private storage — accounts, artists, kits — and nothing in the console
    ever links to it. Generated assets live under Genblaze's `runs/` prefix and song
    masters under `masters/`, so this costs no legitimate fetch.
    """
    container = get_container()
    if container.settings.storage_backend != "local":
        raise HTTPException(status_code=404, detail="Not available on this storage backend")
    if key.startswith(f"{container.settings.key_prefix}/tenants/"):
        raise HTTPException(status_code=404, detail="No such object")
    try:
        data = container.storage.get(key)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="No such object") from exc

    suffix = key.rsplit(".", 1)[-1].lower()
    media_type = {
        "mp4": "video/mp4",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "json": "application/json",
        "yaml": "application/yaml",
    }.get(suffix, "application/octet-stream")
    return Response(content=data, media_type=media_type)
