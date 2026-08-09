"""The console.

Two things live here and the split is deliberate.

**The roster CRUD** (`/roster*`) is real: it reads and writes `tenant` and `artist` on
the live cluster, and it is the only part of the product that currently persists
anything. Server-rendered Jinja + htmx, matching `app/remixkit/ui/routes.py` — same
reasoning, same component boundaries, so a later React front end would find the seams
already cut.

**The console** (everything else) is the wireframe: three panes, thirteen views, driven
by `demo.py`. Buttons are inert. Every view is marked so on screen. It exists because a
layout, an information hierarchy and an inspector have to be judged before the tables
behind them are built, and judging them from a spec does not work.

The seam between the two is one line per view: a route hands the template a `View`. When
a table lands, the fixture becomes a `repo` call and the template does not change. The
`/artists` view already does this halfway — live rows, wireframe columns — which is the
honest way to show where the substrate currently stops.

**The console is private.** Four routes are public — `/` (the landing page), `/signin`,
`POST /demo` and `/healthz` — and everything else is behind `require_operator`, which
303s a visitor to the landing page rather than showing them a wall. An earlier build
served the console to anonymous readers so a hackathon judge would see the product
rather than a login box; that is reversed deliberately, and judges get a token instead.

The gate is a dependency rather than a call at the top of each handler, so a new console
route is private by the act of annotating its principal `Operator`. Writes additionally
require `may_write`, checked in `_require_write`.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import (
    APIRouter, Cookie, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from rtf_platform import (
    auth, db, demo, repo, research, settings as settings_mod, statements,
)
from rtf_platform.domain import (
    ARTIST_STATUSES, DEFAULT_TYPE, ArtistType, Platform, ProfileMode, unrecognised,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SETTINGS = settings_mod.load()


# ------------------------------------------------------------------ plumbing

def current_principal(
    rtf_session: Annotated[str | None, Cookie()] = None,
) -> auth.Principal:
    return auth.principal_from_cookie(rtf_session, SETTINGS.admin_token)


Principal = Annotated[auth.Principal, Depends(current_principal)]


def require_operator(
    rtf_session: Annotated[str | None, Cookie()] = None,
) -> auth.Principal:
    """The gate. Everything except `/`, `/signin`, `/demo` and `/healthz` is behind it.

    A 303 with a Location rather than a 401: an unauthenticated browser should land on
    the page that explains what this is and offers a way in, not on a wall. Declared as
    a dependency so a new console route is private by the act of annotating its
    principal — the failure mode of a `_require(...)` call at the top of each handler is
    the one route where somebody forgets it.
    """
    principal = auth.principal_from_cookie(rtf_session, SETTINGS.admin_token)
    if not principal.authenticated:
        raise HTTPException(
            status_code=303, detail="Sign in first.", headers={"Location": "/"}
        )
    return principal


Operator = Annotated[auth.Principal, Depends(require_operator)]


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


#: Value -> chip tone. One mapping for the whole product, so `failed` in the queue and
#: `failed` in the run log cannot drift apart into two different reds. Anything
#: unrecognised renders neutral, which is the safe direction: a state nobody has
#: classified should not shout.
_TONES: dict[str, str] = {
    "ok": "ok", "live": "ok", "agreed": "ok", "working": "ok", "sent": "ok",
    "handled": "ok", "delivered": "ok", "measured": "ok", "running": "ok",
    "warn": "warn", "stale": "warn", "pending": "warn", "claimed": "warn",
    "negotiating": "warn", "awaiting_reply": "warn", "near cap": "warn",
    "blocked_manual": "warn", "shortlisted": "warn", "winding down": "warn",
    # A statement read through a column map nobody has checked against a real
    # export. The numbers may be perfectly plausible and still wrong.
    "unchecked": "warn",
    "conflict": "err", "error": "err", "failed": "err", "rejected": "err",
    "429": "err", "do-not-contact": "err", "suppressed": "err", "paused": "err",
    "needs you": "err", "unsubscribed": "err", "off": "err", "unanalysed": "err",
    "idle": "info", "launch": "info", "holdout": "info", "replied": "info",
    "declined": "info",
}


def _chip_tone(value: Any) -> str:
    return _TONES.get(str(value).strip().lower(), "")


#: Four characters, because the column is 10% wide and "inferred" does not fit. Spelled
#: out rather than sliced — a naive `[:4]` gives INFE and ASSE, which read as typos.
_PROV_ABBR: dict[str, str] = {
    "measured": "MEAS", "inferred": "INFR", "asserted": "ASRT",
}


def _prov_abbr(value: Any) -> str:
    return _PROV_ABBR.get(str(value), str(value)[:4].upper())


templates.env.globals["chip_tone"] = _chip_tone
templates.env.globals["prov_abbr"] = _prov_abbr


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
        "nav": demo.NAV,
        "scopes": demo.SCOPES,
        "here": None,
        "insp_kicker": "",
        "insp_title": "",
        # (href, label) for the inspector's create control, on the views that have one.
        "insp_new": None,
        # False unless a route says otherwise. A view that forgets to declare itself
        # reads as wireframe, which is the direction that cannot mislead.
        "live": False,
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


# -------------------------------------------------------------------- console

def _table(request: Request, principal: auth.Principal, view: demo.View,
           sel_id: str | None, kicker: str, title_key: str,
           live: bool = False) -> Response:
    """Nine of the thirteen views are this call. The View carries its own columns,
    rows and stats; everything else is shell."""
    sel = demo.select(view.rows, sel_id)
    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, here=view.key, view=view, sel=sel, live=live,
             insp_kicker=kicker,
             insp_title=(sel or {}).get(title_key, "—")),
    )


#: Offered on the demo form. A range rather than a number, because nobody knows their
#: exact roster size off the top of their head and an empty required field loses the lead.
ROSTER_SIZES: tuple[str, ...] = ("1–5 artists", "6–20", "21–50", "50+", "Not a label")


def _landing(request: Request, principal: auth.Principal, *,
             sent: str = "", error: str = "",
             form: dict[str, str] | None = None) -> Response:
    return templates.TemplateResponse(
        request, "landing.html",
        _ctx(request, principal, sent=sent, error=error, form=form or {},
             roster_sizes=ROSTER_SIZES),
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request, principal: Principal, sel: str = "") -> Response:
    """The only route that serves two different products.

    Signed out you get the landing page; signed in you get the needs-you queue. One
    address either way, so a bookmark works before and after somebody has a token, and
    there is no `/app` prefix to explain.
    """
    if not principal.authenticated:
        return _landing(request, principal)
    row = demo.select(demo.TODAY, sel or None)
    return templates.TemplateResponse(
        request, "console/today.html",
        _ctx(request, principal, here="today", items=demo.TODAY, sel=row,
             quiet=demo.TODAY_QUIET,
             insp_kicker=(row or {}).get("kind", ""),
             insp_title=(row or {}).get("head", "—")),
    )


@router.post("/demo", response_class=HTMLResponse)
def demo_request(
    request: Request,
    principal: Principal,
    name: Annotated[str, Form()] = "",
    label: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    roster_size: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> Response:
    """The one write in the system reachable without a session.

    Re-renders the landing page rather than redirecting, so a failed submission comes
    back with the fields still filled in. Validation is deliberately shallow — a real
    address that bounces is the operator's problem to notice, and a form that argues
    with people about their own email loses more leads than it filters.
    """
    form = {"name": name, "label": label, "email": email,
            "roster_size": roster_size, "note": note}
    if not (name.strip() and label.strip() and "@" in email):
        return _landing(request, principal, form=form,
                        error="A name, a label and an email are all needed.")
    try:
        repo.create_demo_request(
            _conn(), source="landing",
            user_agent=request.headers.get("user-agent", ""), **form,
        )
    except psycopg.Error:
        # Never show a database error to a stranger, and never lose the typing.
        return _landing(request, principal, form=form,
                        error="Something broke on our side. Try again in a minute.")
    return _landing(request, principal, sent=email.strip())


@router.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, principal: Operator, sel: str = "") -> Response:
    row = demo.select(demo.APPROVALS, sel or None)
    return templates.TemplateResponse(
        request, "console/approvals.html",
        _ctx(request, principal, here="approvals", drafts=demo.APPROVALS, sel=row,
             insp_kicker="draft", insp_title=(row or {}).get("who", "—")),
    )


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, principal: Operator, sel: str = "") -> Response:
    row = demo.select(demo.INBOX, sel or None)
    return templates.TemplateResponse(
        request, "console/inbox.html",
        _ctx(request, principal, here="inbox", messages=demo.INBOX, sel=row,
             insp_kicker="reply", insp_title=(row or {}).get("who", "—")),
    )


# ------------------------------------------------------ real, from the tables

def _live(request: Request, principal: auth.Principal, build, sel_id: str | None,
          kicker: str, title_key: str) -> Response:
    """A view built from migration 004's tables rather than from `demo.py`.

    Before the tenant row exists there is nothing to scope a query by, so these render
    the view's own empty state rather than erroring — a fresh cluster should be
    browsable before it has data, same rule the roster already follows.
    """
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        empty = demo.View(key="", title="", blurb="", stats=(), cols=(), rows=(),
                          empty="Nothing here yet — save an artist first.")
        return _table(request, principal, empty, None, kicker, title_key, live=True)
    return _table(request, principal, build(conn, tenant_id), sel_id, kicker,
                  title_key, live=True)


#: Artists is the one console view that writes. Everything below it is the editor that
#: used to live at `/roster`, moved into the inspector so changing a record does not
#: cost the selection you were working from.

_ARTISTS_BLURB = (
    "The spine. Relationships, audience model and lessons accumulate here and are "
    "inherited by every release. Live from artist — and editable in place."
)


def _artists_page(
    request: Request, principal: auth.Principal, *,
    sel: str = "", confirm: str = "", error: str = "", profile_error: str = "",
    form: dict[str, str] | None = None, status_code: int = 200,
) -> Response:
    """The Artists view, including whichever editor state the inspector is in.

    One function for the GET and for every rejected POST, so a validation failure
    renders the same page the operator was already looking at, with the message beside
    the field instead of on an error page that loses their place.
    """
    conn = _conn()
    tenant_id = _tenant_id(conn)
    creating = sel == "new"

    if tenant_id is None:
        view = demo.View(
            key="artists", title="Artists", blurb=_ARTISTS_BLURB, stats=(), cols=(),
            rows=(), empty="No artists yet — start with ＋ New artist.",
        )
    else:
        view = research.artists(
            conn, tenant_id, editing_id=(None if creating else sel or None),
            error=("" if creating else error),
            profile_error=profile_error, confirm_delete=(confirm == "delete"),
        )

    if creating:
        # A synthetic selection: there is no row yet, and the inspector is the form
        # that would make one.
        sel_row: dict[str, Any] | None = {
            "id": "new", "name": "New artist",
            "insp": research.new_artist_sections(form=form, error=error),
        }
    else:
        sel_row = demo.select(view.rows, sel or None)

    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, here="artists", view=view, sel=sel_row, live=True,
             insp_kicker="artist",
             insp_title=(sel_row or {}).get("name", "—"),
             insp_new=None if creating else ("/artists?sel=new", "New artist")),
        status_code=status_code,
    )


@router.get("/artists", response_class=HTMLResponse)
def artists_console(request: Request, principal: Operator, sel: str = "",
                    confirm: str = "") -> Response:
    return _artists_page(request, principal, sel=sel, confirm=confirm)


@router.post("/artists", response_class=HTMLResponse)
def artists_create(request: Request, principal: Operator,
                   name: Annotated[str, Form()] = "",
                   type: Annotated[str, Form()] = DEFAULT_TYPE.value) -> Response:
    _require_write(principal)
    typed = {"name": name, "type": type}
    name = name.strip()
    if not name:
        return _artists_page(request, principal, sel="new", form=typed,
                             error="An artist needs a name.", status_code=400)
    if not repo.slugify(name):
        return _artists_page(request, principal, sel="new", form=typed,
                             error="That name has no URL-safe form.", status_code=400)
    try:
        artist_type = _validated_type(type)
    except HTTPException as exc:
        return _artists_page(request, principal, sel="new", form=typed,
                             error=str(exc.detail), status_code=400)

    conn = _conn()
    # Creates the label row on first save, which is why there is no seed file.
    tenant = repo.ensure_tenant(conn, SETTINGS.tenant_slug, "Respect the Funk")
    try:
        created = repo.create_party(conn, str(tenant["id"]), name=name, type_=artist_type)
    except psycopg.errors.UniqueViolation:
        return _artists_page(
            request, principal, sel="new", form=typed, status_code=409,
            error=f"An artist already exists with the same URL key as {name!r}.")
    return RedirectResponse(f"/artists?sel={created['id']}", status_code=303)


@router.post("/artists/{artist_id}", response_class=HTMLResponse)
def artists_update(request: Request, principal: Operator, artist_id: str,
                   name: Annotated[str, Form()] = "",
                   type: Annotated[str, Form()] = DEFAULT_TYPE.value,
                   status: Annotated[str, Form()] = "active") -> Response:
    _require_write(principal)
    name = name.strip()
    if not name:
        return _artists_page(request, principal, sel=artist_id,
                             error="An artist needs a name.", status_code=400)
    if status not in ARTIST_STATUSES:
        return _artists_page(request, principal, sel=artist_id, status_code=400,
                             error=f"{status!r} is not a supported status.")

    conn = _conn()
    tenant_id = _tenant_id(conn)
    current = repo.get_party(conn, tenant_id, artist_id) if tenant_id else None
    if current is None:
        raise HTTPException(status_code=404, detail="No such artist.")

    # A type this build no longer defines is kept if the form sent it back unchanged,
    # and validated normally if the operator picked something else. Editing a name must
    # never silently reclassify the act.
    legacy = unrecognised(current["type"])
    try:
        artist_type = type.strip() if legacy and type.strip() == legacy else _validated_type(type)
    except HTTPException as exc:
        return _artists_page(request, principal, sel=artist_id,
                             error=str(exc.detail), status_code=400)

    try:
        repo.update_party(conn, tenant_id, artist_id,
                           name=name, type_=artist_type, status=status)
    except psycopg.errors.UniqueViolation:
        return _artists_page(
            request, principal, sel=artist_id, status_code=409,
            error=f"An artist already exists with the same URL key as {name!r}.")
    return RedirectResponse(f"/artists?sel={artist_id}", status_code=303)


@router.post("/artists/{artist_id}/delete")
def artists_delete(principal: Operator, artist_id: str) -> Response:
    """Reached only from the confirmation step, which names what cascades with it."""
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        repo.delete_party(conn, tenant_id, artist_id)
    return RedirectResponse("/artists", status_code=303)


@router.post("/artists/{artist_id}/profiles", response_class=HTMLResponse)
def artist_profile_add(request: Request, principal: Operator, artist_id: str,
                       platform: Annotated[str, Form()] = "",
                       mode: Annotated[str, Form()] = "",
                       handle: Annotated[str, Form()] = "",
                       profile_url: Annotated[str, Form()] = "") -> Response:
    """Add or correct one surface. Where the forager is allowed to look for this act."""
    _require_write(principal)
    known_platform = Platform.parse(platform)
    known_mode = ProfileMode.parse(mode)
    if known_platform is None:
        return _artists_page(request, principal, sel=artist_id, status_code=400,
                             profile_error=f"{platform!r} is not a supported platform.")
    if known_mode is None:
        return _artists_page(request, principal, sel=artist_id, status_code=400,
                             profile_error=f"{mode!r} is not a supported mode.")

    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None or repo.get_party(conn, tenant_id, artist_id) is None:
        raise HTTPException(status_code=404, detail="No such artist.")

    repo.upsert_presence(
        conn, tenant_id, artist_id,
        platform=known_platform.value, mode=known_mode.value,
        handle=handle.strip()[:200], profile_url=profile_url.strip()[:500],
    )
    return RedirectResponse(f"/artists?sel={artist_id}", status_code=303)


@router.post("/artists/{artist_id}/profiles/{profile_id}/delete")
def artist_profile_delete(principal: Operator, artist_id: str, profile_id: str) -> Response:
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        repo.delete_presence(conn, tenant_id, artist_id, profile_id)
    return RedirectResponse(f"/artists?sel={artist_id}", status_code=303)


# ------------------------------------------------------- distributor statements

#: Bigger than any statement a small roster produces, small enough that a mistaken
#: upload cannot exhaust a Lambda's memory. Enforced on the bytes actually read
#: rather than on a declared content-length, which a client chooses.
MAX_STATEMENT_BYTES = 12 * 1024 * 1024


def _imports_page(request: Request, principal: auth.Principal, *,
                  sel: str = "", error: str = "", note: str = "",
                  keep_confirm: bool = False, status_code: int = 200) -> Response:
    """Statements, with the import form always present in the inspector.

    The form is prepended to whatever row is selected rather than living behind its
    own `?sel=new`: importing is the reason to open this view, and hiding it behind
    a second click on a screen that is usually empty gets it missed.
    """
    conn = _conn()
    tenant_id = _tenant_id(conn)
    view = (research.imports(conn, tenant_id) if tenant_id
            else demo.View(key="imports", title="Statements",
                           blurb=_IMPORTS_BLURB, stats=(), cols=(), rows=(),
                           empty="Save an artist first — a statement needs a label "
                                 "to belong to."))
    upload = research._upload_sections(
        error=error, note=note, pending_token="yes" if keep_confirm else "")
    row = demo.select(view.rows, sel or None)
    sel_row = {**row, "insp": (*upload, *row["insp"])} if row else {
        "id": "new", "file": "Import", "insp": upload}
    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, here="imports", view=view, sel=sel_row, live=True,
             insp_kicker="statement", insp_title=sel_row.get("file", "Import")),
        status_code=status_code,
    )


_IMPORTS_BLURB = ("What the distributor actually paid, per recording per territory "
                  "per month. The only source of real stream counts.")


@router.get("/imports", response_class=HTMLResponse)
def imports_console(request: Request, principal: Operator, sel: str = "") -> Response:
    return _imports_page(request, principal, sel=sel)


@router.post("/imports", response_class=HTMLResponse)
async def imports_upload(
    request: Request, principal: Operator,
    file: Annotated[UploadFile, File()],
    distributor: Annotated[str, Form()] = "",
    confirm_unverified: Annotated[str, Form()] = "",
) -> Response:
    """Read the uploaded statement and, if confirmed, load it.

    Two passes on purpose. The first reports what the file says and refuses to write,
    because no reader here has been checked against a real export and a wrong column
    map produces numbers that look entirely reasonable. Ticking the box and
    submitting again is the second, deliberate act.
    """
    _require_write(principal)
    raw = await file.read(MAX_STATEMENT_BYTES + 1)
    if len(raw) > MAX_STATEMENT_BYTES:
        return _imports_page(
            request, principal, status_code=413,
            error=f"That file is larger than {MAX_STATEMENT_BYTES // (1024 * 1024)}MB. "
                  "Split it by period and import the parts.")
    if not raw:
        return _imports_page(request, principal, status_code=400,
                             error="That file is empty.")

    # Statements are exported by spreadsheets and arrive in whatever encoding the
    # machine that made them preferred. Decoding with replacement keeps a stray byte
    # from costing the whole import; the identifiers are ASCII either way.
    text = raw.decode("utf-8-sig", errors="replace")
    confirmed = confirm_unverified.strip().lower() in {"yes", "on", "true", "1"}

    conn = _conn()
    tenant = repo.ensure_tenant(conn, SETTINGS.tenant_slug, "Respect the Funk")
    report = statements.load(
        conn, str(tenant["id"]), text,
        filename=(file.filename or "")[:200], distributor=distributor,
        imported_by=principal.subject,
        allow_unverified=confirmed,
    )

    if report.written:
        return RedirectResponse("/imports", status_code=303)
    return _imports_page(
        request, principal,
        error=report.refused,
        note=_report_note(report),
        keep_confirm=bool(report.format and not report.format.verified),
        status_code=409 if report.duplicate_of else 400,
    )


def _report_note(report: statements.Report) -> str:
    """What the file says, whether or not it was written. An operator confirming an
    unchecked column map needs to see the numbers it produced before agreeing to
    them — a confirmation with nothing to inspect is a rubber stamp."""
    if report.format is None:
        return ""
    period = "no period"
    if report.period_start:
        period = str(report.period_start)[:7]
        if report.period_end and str(report.period_end)[:7] != period:
            period += f" → {str(report.period_end)[:7]}"
    lines = [
        f"read as   {report.format.key}",
        f"period    {period}",
        f"rows      {report.rows_read}"
        + (f"  ({report.rows_no_isrc} with no usable ISRC)" if report.rows_no_isrc else ""),
        f"plays     {report.total_quantity:,}",
        f"earnings  {report.total_earnings} {report.currency}",
        f"stores    {', '.join(report.stores) or '—'}",
    ]
    return "\n".join(lines)


@router.get("/facts", response_class=HTMLResponse)
def facts(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.facts, sel or None, "claim", "dimension")


@router.get("/queue", response_class=HTMLResponse)
def queue(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.queue, sel or None, "lead", "target")


@router.get("/fleet", response_class=HTMLResponse)
def fleet(request: Request, principal: Operator, sel: str = "") -> Response:
    return _table(request, principal, demo.FLEET, sel or None, "agent", "agent")


@router.get("/budgets", response_class=HTMLResponse)
def budgets(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.budgets, sel or None, "budget", "artist")


@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.runs, sel or None, "run", "what")


@router.get("/counterparties", response_class=HTMLResponse)
def counterparties(request: Request, principal: Operator, sel: str = "") -> Response:
    return _table(request, principal, demo.COUNTERPARTIES, sel or None, "counterparty", "who")


@router.get("/threads", response_class=HTMLResponse)
def threads(request: Request, principal: Operator, sel: str = "") -> Response:
    return _table(request, principal, demo.THREADS, sel or None, "thread", "who")


@router.get("/tracks", response_class=HTMLResponse)
def tracks(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.tracks, sel or None, "track", "title")


@router.get("/campaigns", response_class=HTMLResponse)
def campaigns(request: Request, principal: Operator, sel: str = "") -> Response:
    return _table(request, principal, demo.CAMPAIGNS, sel or None, "campaign", "name")


# ---------------------------------------------------------------------- auth

@router.get("/signin", response_class=HTMLResponse)
def signin_form(request: Request, principal: Principal, error: str = "") -> Response:
    if principal.authenticated:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "signin.html", _ctx(request, principal, error=error)
    )


@router.post("/signin")
def signin(request: Request, principal: Principal,
           token: Annotated[str, Form()]) -> Response:
    """A wrong token re-renders the form rather than raising.

    A bare 401 page is the wrong answer to a typo — it looks like the site is broken
    instead of like the token was wrong, and it loses the operator's place.
    """
    if not SETTINGS.admin_token:
        raise HTTPException(status_code=503, detail="No admin token is configured.")
    if not auth.principal_from_cookie(token, SETTINGS.admin_token).authenticated:
        return templates.TemplateResponse(
            request, "signin.html",
            _ctx(request, principal, error="That token is not right."),
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    # httpOnly so console JavaScript cannot read it and an XSS cannot exfiltrate it.
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 12
    )
    return response


@router.post("/signout")
def signout() -> Response:
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


# ------------------------------------------------------- roster CRUD (real)

@router.get("/roster", response_class=HTMLResponse)
def roster_page(request: Request, principal: Operator, q: str = "") -> Response:
    conn = _conn()
    tenant_id = _tenant_id(conn)
    artists = repo.list_parties(conn, tenant_id, q) if tenant_id else []
    return templates.TemplateResponse(
        request, "artists.html", _ctx(request, principal, artists=artists, q=q)
    )


@router.get("/roster/rows", response_class=HTMLResponse)
def roster_rows(request: Request, principal: Operator, q: str = "") -> Response:
    """The fragment htmx swaps in on search. Same template the full page uses."""
    conn = _conn()
    tenant_id = _tenant_id(conn)
    artists = repo.list_parties(conn, tenant_id, q) if tenant_id else []
    return templates.TemplateResponse(
        request, "components/_artist_rows.html", _ctx(request, principal, artists=artists, q=q)
    )


@router.get("/roster/new", response_class=HTMLResponse)
def roster_new(request: Request, principal: Operator) -> Response:
    _require_write(principal)
    return templates.TemplateResponse(
        request, "artist_form.html", _ctx(request, principal, artist=None, legacy_type=None)
    )


@router.post("/roster")
def roster_create(
    principal: Operator,
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
        repo.create_party(conn, str(tenant["id"]), name=name, type_=artist_type)
    return RedirectResponse("/roster", status_code=303)


@router.get("/roster/{artist_id}", response_class=HTMLResponse)
def roster_detail(request: Request, principal: Operator, artist_id: str) -> Response:
    conn = _conn()
    tenant_id = _tenant_id(conn)
    artist = repo.get_party(conn, tenant_id, artist_id) if tenant_id else None
    if artist is None:
        raise HTTPException(status_code=404, detail="No such artist.")
    return templates.TemplateResponse(
        request,
        "artist_form.html",
        _ctx(request, principal, artist=artist, legacy_type=unrecognised(artist["type"])),
    )


@router.post("/roster/{artist_id}")
def roster_update(
    principal: Operator,
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
    current = repo.get_party(conn, tenant_id, artist_id) if tenant_id else None

    # A type this build no longer defines is kept if the form sent it back
    # unchanged, and validated normally if the operator picked something else.
    # Editing a name must never silently reclassify the act.
    legacy = unrecognised(current["type"]) if current else None
    artist_type = type.strip() if legacy and type.strip() == legacy else _validated_type(type)

    with _friendly_conflict(name):
        updated = repo.update_party(
            conn, tenant_id, artist_id, name=name, type_=artist_type, status=status
        ) if tenant_id else None
    if updated is None:
        raise HTTPException(status_code=404, detail="No such artist.")
    return RedirectResponse("/roster", status_code=303)


@router.post("/roster/{artist_id}/delete", response_class=HTMLResponse)
def roster_delete(request: Request, principal: Operator, artist_id: str) -> Response:
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        repo.delete_party(conn, tenant_id, artist_id)
    artists = repo.list_parties(conn, tenant_id) if tenant_id else []
    return templates.TemplateResponse(
        request, "components/_artist_rows.html", _ctx(request, principal, artists=artists, q="")
    )
