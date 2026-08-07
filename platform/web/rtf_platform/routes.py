"""The console. Server-rendered Jinja + htmx, matching the precedent in
`app/remixkit/ui/routes.py` — same reasoning, same component boundaries, so a
later React front end would find the seams already cut.

Every fragment under `templates/components/` renders standalone and is addressable
by a route returning *just that fragment*. `_artist_rows.html` is used by the full
page render, by search-as-you-type, and by the response to a delete. One
definition, three call sites.

Reads are open. Writes require a Principal that `may_write`, checked in one place
(`_require_write`) rather than remembered at each call site.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from rtf_platform import auth, db, repo, settings as settings_mod
from rtf_platform.domain import DEFAULT_TYPE, ArtistType, unrecognised

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SETTINGS = settings_mod.load()


# ------------------------------------------------------------------ plumbing

def current_principal(
    rtf_session: Annotated[str | None, Cookie()] = None,
) -> auth.Principal:
    return auth.principal_from_cookie(rtf_session, SETTINGS.admin_token)


Principal = Annotated[auth.Principal, Depends(current_principal)]


def _require_write(principal: auth.Principal) -> None:
    if not principal.may_write:
        raise HTTPException(status_code=403, detail="Sign in to change anything.")


def _conn() -> psycopg.Connection:
    if not SETTINGS.configured:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not set.")
    try:
        conn = db.connect(SETTINGS.database_url)
        with conn.cursor() as cur:          # cheap liveness check on a warm container
            cur.execute("SELECT 1")
        return conn
    except psycopg.Error:
        db.reset()                          # stale socket after an idle period
        return db.connect(SETTINGS.database_url)


def _tenant_id(conn: psycopg.Connection) -> str | None:
    """None until the first artist is saved. Read pages render an empty state
    rather than erroring, so a fresh deployment is browsable before it has data."""
    tenant = repo.get_tenant(conn, SETTINGS.tenant_slug)
    return str(tenant["id"]) if tenant else None


def _ctx(request: Request, principal: auth.Principal, **extra: Any) -> dict[str, Any]:
    return {
        "request": request,
        "principal": principal,
        "tenant_slug": SETTINGS.tenant_slug,
        "type_groups": ArtistType.grouped(),
        "default_type": DEFAULT_TYPE,
        # Stored value -> what a human reads. A row whose type is absent here is
        # rendered as its raw value rather than blanked.
        "type_labels": {t.value: t.label for t in ArtistType},
        **extra,
    }


@contextmanager
def _friendly_conflict(name: str):
    """Turn the slug uniqueness violation into something an operator can act on.

    `artist (tenant_id, slug)` is unique and the slug is derived from the name, so
    two artists whose names differ only by punctuation or case collide. Unhandled,
    that surfaces as a 500 and reads like the console is broken rather than like
    the roster already has this act.
    """
    try:
        yield
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail=f"An artist already exists with the same URL key as {name!r}.",
        ) from None


def _validated_type(raw: str) -> str:
    """The select offers only known values, so anything else is a crafted post.
    Rejected rather than coerced — quietly storing the default would make an
    artist the wrong kind and nobody would see it happen."""
    parsed = ArtistType.parse(raw)
    if parsed is None:
        raise HTTPException(status_code=400, detail=f"{raw!r} is not a supported artist type.")
    return parsed.value


# --------------------------------------------------------------------- health

@router.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """No database call. This answers "is the Lambda alive", and making it depend
    on the cluster would turn a database blip into a failing health check."""
    return {"status": "ok"}


# ---------------------------------------------------------------------- auth

@router.get("/signin", response_class=HTMLResponse)
def signin_form(request: Request, principal: Principal) -> Response:
    return templates.TemplateResponse(request, "signin.html", _ctx(request, principal))


@router.post("/signin")
def signin(token: Annotated[str, Form()]) -> Response:
    if not SETTINGS.admin_token:
        raise HTTPException(status_code=503, detail="No admin token is configured.")
    principal = auth.principal_from_cookie(token, SETTINGS.admin_token)
    if not principal.authenticated:
        raise HTTPException(status_code=401, detail="That token is not right.")
    response = RedirectResponse("/artists", status_code=303)
    # httpOnly so console JavaScript cannot read it and an XSS cannot exfiltrate it.
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 12
    )
    return response


@router.post("/signout")
def signout() -> Response:
    response = RedirectResponse("/artists", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


# ------------------------------------------------------------------- artists

@router.get("/", include_in_schema=False)
def index() -> Response:
    return RedirectResponse("/artists", status_code=307)


@router.get("/artists", response_class=HTMLResponse)
def artists_page(request: Request, principal: Principal, q: str = "") -> Response:
    conn = _conn()
    tenant_id = _tenant_id(conn)
    artists = repo.list_artists(conn, tenant_id, q) if tenant_id else []
    return templates.TemplateResponse(
        request, "artists.html", _ctx(request, principal, artists=artists, q=q)
    )


@router.get("/artists/rows", response_class=HTMLResponse)
def artist_rows(request: Request, principal: Principal, q: str = "") -> Response:
    """The fragment htmx swaps in on search. Same template the full page uses."""
    conn = _conn()
    tenant_id = _tenant_id(conn)
    artists = repo.list_artists(conn, tenant_id, q) if tenant_id else []
    return templates.TemplateResponse(
        request, "components/_artist_rows.html", _ctx(request, principal, artists=artists, q=q)
    )


@router.get("/artists/new", response_class=HTMLResponse)
def artist_new(request: Request, principal: Principal) -> Response:
    _require_write(principal)
    return templates.TemplateResponse(
        request, "artist_form.html", _ctx(request, principal, artist=None, legacy_type=None)
    )


@router.post("/artists")
def artist_create(
    principal: Principal,
    name: Annotated[str, Form()],
    type: Annotated[str, Form()] = DEFAULT_TYPE.value,
) -> Response:
    _require_write(principal)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="An artist needs a name.")
    if not repo.slugify(name):
        raise HTTPException(status_code=400, detail="That name has no URL-safe form.")
    artist_type = _validated_type(type)

    conn = _conn()
    # Creates the label row on first save, which is why there is no seed file.
    tenant = repo.ensure_tenant(conn, SETTINGS.tenant_slug, "Respect the Funk")
    with _friendly_conflict(name):
        repo.create_artist(conn, str(tenant["id"]), name=name, type_=artist_type)
    return RedirectResponse("/artists", status_code=303)


@router.get("/artists/{artist_id}", response_class=HTMLResponse)
def artist_detail(request: Request, principal: Principal, artist_id: str) -> Response:
    conn = _conn()
    tenant_id = _tenant_id(conn)
    artist = repo.get_artist(conn, tenant_id, artist_id) if tenant_id else None
    if artist is None:
        raise HTTPException(status_code=404, detail="No such artist.")
    return templates.TemplateResponse(
        request,
        "artist_form.html",
        _ctx(request, principal, artist=artist, legacy_type=unrecognised(artist["type"])),
    )


@router.post("/artists/{artist_id}")
def artist_update(
    principal: Principal,
    artist_id: str,
    name: Annotated[str, Form()],
    type: Annotated[str, Form()] = DEFAULT_TYPE.value,
    status: Annotated[str, Form()] = "active",
) -> Response:
    _require_write(principal)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="An artist needs a name.")

    conn = _conn()
    tenant_id = _tenant_id(conn)
    current = repo.get_artist(conn, tenant_id, artist_id) if tenant_id else None

    # A type this build no longer defines is kept if the form sent it back
    # unchanged, and validated normally if the operator picked something else.
    # Editing a name must never silently reclassify the act.
    legacy = unrecognised(current["type"]) if current else None
    artist_type = type.strip() if legacy and type.strip() == legacy else _validated_type(type)

    with _friendly_conflict(name):
        updated = repo.update_artist(
            conn, tenant_id, artist_id, name=name, type_=artist_type, status=status
        ) if tenant_id else None
    if updated is None:
        raise HTTPException(status_code=404, detail="No such artist.")
    return RedirectResponse("/artists", status_code=303)


@router.post("/artists/{artist_id}/delete", response_class=HTMLResponse)
def artist_delete(request: Request, principal: Principal, artist_id: str) -> Response:
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        repo.delete_artist(conn, tenant_id, artist_id)
    artists = repo.list_artists(conn, tenant_id) if tenant_id else []
    return templates.TemplateResponse(
        request, "components/_artist_rows.html", _ctx(request, principal, artists=artists, q="")
    )
