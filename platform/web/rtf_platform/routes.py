"""The console.

Two things live here and the split is deliberate.

**The roster CRUD** (`/roster*`) is real: it reads and writes `tenant` and `artist` on
the live cluster, and it is the only part of the product that currently persists
anything. Server-rendered Jinja + htmx, matching `app/remixkit/ui/routes.py` — same
reasoning, same component boundaries, so a later React front end would find the seams
already cut.

**The console** (everything else) is three panes and thirteen views, and every one of
them now reads the cluster. `research.py` builds them; `demo.py` is the block vocabulary
they are described with, and no longer carries any data.

The seam held. When a table landed the fixture became a query and the template did not
change — not one line of `console/table.html`, `approvals.html` or `inbox.html` moved to
accommodate the last five. That was the whole bet of building the screens first, and it
is worth stating plainly now that it has been collected.

**What is real and what is absent are different questions.** Every view is live; not
every agent behind one exists. `/approvals` reads real drafts and the gate genuinely
prepares a send, and nothing claims the outbox because no provider is wired. `/inbox`
reads a real table that no inbound adapter fills. Those gaps are rendered as themselves —
an empty state that says what is missing beats a fixture that hides it, because an
operator looking at three invented replies has no way to learn the integration does not
exist.

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
from urllib.parse import quote_plus

import psycopg
from fastapi import (
    APIRouter, Cookie, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from rtf_platform import (
    auth, db, demo, outreach, repo, research, settings as settings_mod, statements,
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


def _q(text: str) -> str:
    """A message safe to carry back in a redirect's query string."""
    return quote_plus(text[:300])


def _thread_of(conn: psycopg.Connection, tenant_id: str | None,
               message_id: str) -> str | None:
    """The thread a draft belongs to, or None if it is not an unsent outbound draft.

    The action routes take a message id because that is what the operator selected, but
    every write in `outreach` is scoped by thread. Resolving it here — rather than
    trusting a hidden form field — means a posted id that has already been approved,
    belongs to another tenant or does not exist all end up in the same place: a message
    on the approvals screen, not a traceback.
    """
    if tenant_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thread_id FROM message WHERE tenant_id = %s AND id = %s "
            "AND direction = 'outbound' AND sent_at IS NULL",
            (tenant_id, message_id))
        row = cur.fetchone()
    return str(row["thread_id"]) if row else None


def _nav(conn: psycopg.Connection | None, tenant_id: str | None
         ) -> tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...]:
    """`demo.NAV` with the badges replaced by counts from the tables.

    The badges were the last fixtures in the shell — `approvals` read "3" on every page
    of a console with nothing waiting. A badge that is always the same number is worse
    than no badge: it trains the operator to ignore the one signal whose entire job is
    to be noticed on a page they were not visiting.

    One query for four numbers, because these render on every console page and a cluster
    that scales to zero charges for each round trip.
    """
    if conn is None or tenant_id is None:
        return tuple((group, tuple((k, label, href, "") for k, label, href, _ in items))
                     for group, items in demo.NAV)
    try:
        counts = outreach.counts(conn, tenant_id)
        parked = _one_count(conn,
                            "SELECT count(*) AS n FROM lead WHERE tenant_id = %s "
                            "AND state = 'failed'", (tenant_id,))
        pending = _one_count(conn,
                             "SELECT count(*) AS n FROM suggestion WHERE tenant_id = %s "
                             "AND state = 'pending'", (tenant_id,))
    except psycopg.OperationalError:
        # A dead socket is not worth failing a page render for, and the rail still draws
        # without numbers on it.
        #
        # Deliberately *not* `psycopg.Error`. That catches `ProgrammingError` too, and a
        # mistyped column would then render blank badges forever instead of erroring —
        # which is exactly what happened while this was being written: `suggestion` has a
        # `state` column, not `status`, and the broad except turned a bug into a silently
        # empty rail. A badge that is always absent is indistinguishable from a badge
        # whose count is legitimately zero, so nothing would ever have surfaced it.
        return tuple((group, tuple((k, label, href, "") for k, label, href, _ in items))
                     for group, items in demo.NAV)

    live = {
        "today":     pending + parked,
        "approvals": counts.get("awaiting_human", 0),
        "inbox":     counts.get("inbound", 0),
        "campaigns": counts.get("running", 0),
        "threads":   counts.get("open_threads", 0),
        "runs":      parked,
    }
    return tuple(
        (group, tuple(
            (key, label, href, str(live[key]) if live.get(key) else "")
            for key, label, href, _ in items))
        for group, items in demo.NAV)


def _one_count(conn: psycopg.Connection, sql: str, params: tuple[Any, ...]) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int((cur.fetchone() or {}).get("n", 0))


def _ctx(request: Request, principal: auth.Principal, *,
         conn: psycopg.Connection | None = None, tenant_id: str | None = None,
         **extra: Any) -> dict[str, Any]:
    return {
        "request": request,
        "principal": principal,
        "tenant_slug": SETTINGS.tenant_slug,
        "type_groups": ArtistType.grouped(),
        "default_type": DEFAULT_TYPE,
        # Stored value -> what a human reads. A row whose type is absent here is
        # rendered as its raw value rather than blanked.
        "type_labels": {t.value: t.label for t in ArtistType},
        "nav": _nav(conn, tenant_id),
        "scopes": demo.SCOPES,
        "here": None,
        "insp_kicker": "",
        "insp_title": "",
        # (href, label) for the inspector's create control, on the views that have one.
        "insp_new": None,
        # False unless a route says otherwise. A view that forgets to declare itself
        # reads as wireframe, which is the direction that cannot mislead.
        "live": False,
        # A refused write, carried back through the redirect. Rendered above the table
        # rather than as an error page, so the operator keeps their place.
        "error": "",
        "stats": (),
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
           live: bool = False, conn: psycopg.Connection | None = None,
           tenant_id: str | None = None) -> Response:
    """Eleven of the thirteen views are this call. The View carries its own columns,
    rows and stats; everything else is shell."""
    sel = demo.select(view.rows, sel_id)
    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, conn=conn, tenant_id=tenant_id,
             here=view.key, view=view, sel=sel, live=live,
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

    # Real rows, not `demo.TODAY`. An empty queue is the correct and common state here —
    # it means the fleet decided everything it was allowed to decide — so this renders
    # nothing rather than falling back to fixtures. A fixture on the home screen is the
    # one place a demo is most likely to be mistaken for the product.
    conn = _conn()
    tenant_id = _tenant_id(conn)
    items, quiet = research.today(conn, tenant_id) if tenant_id else ([], ())
    row = demo.select(items, sel or None)
    return templates.TemplateResponse(
        request, "console/today.html",
        _ctx(request, principal, conn=conn, tenant_id=tenant_id,
             here="today", items=items, sel=row, quiet=quiet, live=True,
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


#: The two screens that are a reading surface rather than a table. Both take the same
#: shape — a list of cards, a selected one rendered in full, and the inspector — so they
#: share a loader and differ only in which builder and template they name.
def _cards(request: Request, principal: auth.Principal, *, here: str, template: str,
           build, key: str, kicker: str, error: str = "") -> Response:
    conn = _conn()
    tenant_id = _tenant_id(conn)
    rows, stats = ([], ()) if tenant_id is None else build(conn, tenant_id)
    sel_id = request.query_params.get("sel", "")
    row = demo.select(rows, sel_id or None)
    return templates.TemplateResponse(
        request, template,
        _ctx(request, principal, conn=conn, tenant_id=tenant_id, here=here, live=True,
             stats=stats, error=error, **{key: rows},
             sel=row, insp_kicker=kicker, insp_title=(row or {}).get("who", "—")),
    )


@router.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, principal: Operator, error: str = "") -> Response:
    """The send gate, live from `thread` and `message`.

    Empty is the normal state and it means the fleet is not blocked on anybody, which is
    what the screen says rather than showing three invented pitches.
    """
    return _cards(request, principal, here="approvals", template="console/approvals.html",
                  build=research.approvals, key="drafts", kicker="draft", error=error)


@router.post("/approvals/{message_id}/approve", response_class=HTMLResponse)
def approvals_approve(request: Request, principal: Operator, message_id: str) -> Response:
    """Prepare the send. This is the irreversible half of the product and it is a POST
    from a form, never a link — see the note in the inspector's actions block."""
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    thread_id = _thread_of(conn, tenant_id, message_id)
    if thread_id is None:
        return RedirectResponse("/approvals?error=That+draft+is+no+longer+waiting.",
                                status_code=303)
    try:
        outreach.approve(conn, tenant_id, thread_id, message_id,
                         approver=principal.subject)
    except psycopg.errors.UniqueViolation:
        # A double-click lands here, and it is `UNIQUE (message_id)` on `outbox` doing
        # its job rather than a fault: the first click already queued it, and the second
        # one was refused instead of queueing a second copy. Reported as that fact — the
        # driver's message names a constraint, which tells an operator nothing and tells
        # anyone else more about the schema than they should get from a form post.
        return RedirectResponse(
            "/approvals?error=" + _q("Already queued — the first click prepared the "
                                     "send, and the database refused a second copy."),
            status_code=303)
    except outreach.TransitionRefused as exc:
        return RedirectResponse(f"/approvals?error={_q(str(exc))}", status_code=303)
    return RedirectResponse("/approvals", status_code=303)


@router.post("/approvals/{message_id}/reject", response_class=HTMLResponse)
def approvals_reject(request: Request, principal: Operator, message_id: str) -> Response:
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    thread_id = _thread_of(conn, tenant_id, message_id)
    if thread_id is None:
        return RedirectResponse("/approvals?error=That+draft+is+no+longer+waiting.",
                                status_code=303)
    try:
        outreach.reject(conn, tenant_id, thread_id, message_id,
                        reason=f"rejected by {principal.subject}")
    except outreach.TransitionRefused as exc:
        return RedirectResponse(f"/approvals?error={_q(str(exc))}", status_code=303)
    return RedirectResponse("/approvals", status_code=303)


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, principal: Operator, error: str = "") -> Response:
    """Replies, live from `message`.

    Nothing writes inbound messages yet — there is no mail provider — so this reads a
    real table that is legitimately empty. `outreach.record_reply` is the writer and the
    tests drive it; what is missing is the thing that would call it, and the empty state
    says exactly that rather than inventing three replies to fill the screen.
    """
    return _cards(request, principal, here="inbox", template="console/inbox.html",
                  build=research.inbox, key="messages", kicker="reply", error=error)


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
        return _table(request, principal, empty, None, kicker, title_key, live=True,
                      conn=conn, tenant_id=tenant_id)
    return _table(request, principal, build(conn, tenant_id), sel_id, kicker,
                  title_key, live=True, conn=conn, tenant_id=tenant_id)


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


def _safe_back(back: str, fallback: str = "/") -> str:
    """Only ever redirect somewhere inside this console.

    `back` arrives in a query string, so it is attacker-controlled. A bare
    `startswith("/")` is not enough — `//evil.example` is a protocol-relative URL that
    browsers follow off-site — so the second character has to be checked too.
    """
    if back.startswith("/") and not back.startswith("//"):
        return back
    return fallback


#: Accept and reject are POSTs with no page of their own. A decision is an act on a
#: record that is already open, so it happens in the inspector and returns you to
#: exactly where you were — the needs-you queue or the artist. Adding a `/suggestions`
#: page would make an operator go somewhere to do something they were already looking at.
@router.post("/suggestions/{suggestion_id}/accept")
def suggestion_accept(principal: Operator, suggestion_id: str,
                      back: str = "/") -> Response:
    """Confirm a match. Writes the surface and queues the mapping in one transaction.

    `repo.SuggestionUnacceptable` — a payload `harvested.Presence.parse` rejects, or
    a suggestion `kind` with no accept path — becomes a `400` naming the reason,
    the same idiom every other rejected write in this file uses (see
    `artists_create`/`artists_update`). The alternative is not raising and instead
    quietly doing nothing: this endpoint has no page of its own to re-render with
    an inline error, but a silent no-op on a broken accept is worse than a plain
    `400` — an operator clicking Accept needs to know it did not work and why.
    """
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        try:
            repo.accept_suggestion(conn, tenant_id, suggestion_id,
                                   by=principal.subject or "operator")
        except repo.SuggestionUnacceptable as exc:
            raise HTTPException(status_code=400, detail=exc.reason) from exc
    return RedirectResponse(_safe_back(back), status_code=303)


@router.post("/suggestions/{suggestion_id}/reject")
def suggestion_reject(principal: Operator, suggestion_id: str,
                      back: str = "/") -> Response:
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        repo.reject_suggestion(conn, tenant_id, suggestion_id,
                               by=principal.subject or "operator")
    return RedirectResponse(_safe_back(back), status_code=303)


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
    return _live(request, principal, research.fleet, sel or None, "agent", "agent")


@router.post("/fleet/{kind}/toggle", response_class=HTMLResponse)
def fleet_toggle(principal: Operator, kind: str) -> Response:
    """Turn an agent off, or back on.

    An UPDATE rather than a deploy, which combined with the lease is a clean drain: the
    agent stops claiming, finishes whatever it is holding, and goes quiet. Nothing is
    killed mid-flight, so there is no orphaned lease to wait out.
    """
    _require_write(principal)
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE agent_manifest SET enabled = NOT enabled, updated_at = now() "
            "WHERE kind = %s", (kind,))
    return RedirectResponse(f"/fleet?sel={kind}", status_code=303)


@router.get("/budgets", response_class=HTMLResponse)
def budgets(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.budgets, sel or None, "budget", "artist")


@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.runs, sel or None, "run", "what")


@router.get("/counterparties", response_class=HTMLResponse)
def counterparties(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.counterparties, sel or None,
                 "counterparty", "who")


#: What an operator can move a thread to by hand, and what the button says. Only the
#: forward edges the fleet would otherwise take — closing has its own controls, and
#: `queued` is absent because reaching it means writing an outbox row, which is
#: `approve`'s job and no button's.
_THREAD_STEPS: dict[str, tuple[str, str]] = {
    "discovered":  ("shortlisted", "Shortlist"),
    "shortlisted": ("approved", "Approve the approach"),
    "queued":      ("sent", "Mark sent"),
    "sent":        ("awaiting_reply", "Awaiting reply"),
    "replied":     ("negotiating", "Negotiating"),
    "negotiating": ("agreed", "Agreed"),
    "agreed":      ("delivered", "Delivered"),
    "delivered":   ("verified", "Verified"),
}


def _threads_page(request: Request, principal: auth.Principal, *, sel: str = "",
                  error: str = "", form: dict[str, str] | None = None,
                  status_code: int = 200) -> Response:
    """The Threads view, with the thread's own controls in the inspector.

    A selected thread gets whichever of three things its state allows: the one forward
    step, the draft form, and the closing controls. Offering only the legal move means
    the state machine is visible as a shape rather than as a list of errors — you cannot
    press the button that would be refused, because it is not drawn.
    """
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        view = demo.View(key="threads", title="Threads", blurb="", stats=(), cols=(),
                         rows=(), empty="Nothing here yet — save an artist first.")
        sel_row: dict[str, Any] | None = None
    else:
        view = research.threads(conn, tenant_id)
        sel_row = demo.select(view.rows, sel or None)
        if sel_row is not None:
            state = sel_row["state"]
            extra: list[demo.Section] = []
            step = _THREAD_STEPS.get(state)
            if step:
                to, label = step
                extra.append(demo.Section("Next step", "actions", (
                    (label, f"/threads/{sel_row['id']}/advance/{to}", "p", "post"),)))
            # `approved` is where a draft becomes possible and `awaiting_human` is where
            # a redraft does. Both write through the same function.
            if state in ("approved", "drafted", "awaiting_human"):
                extra.append(research.draft_form_section(
                    str(sel_row["id"]), form=form, error=error))
            sel_row = dict(sel_row)
            sel_row["insp"] = sel_row["insp"] + tuple(extra)

    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, conn=conn, tenant_id=tenant_id,
             here="threads", view=view, sel=sel_row, live=True,
             error=error if not form else "",
             insp_kicker="thread", insp_title=(sel_row or {}).get("who", "—")),
        status_code=status_code,
    )


@router.get("/threads", response_class=HTMLResponse)
def threads(request: Request, principal: Operator, sel: str = "",
            error: str = "") -> Response:
    return _threads_page(request, principal, sel=sel, error=error)


@router.post("/threads/{thread_id}/advance/{to}", response_class=HTMLResponse)
def threads_advance(principal: Operator, thread_id: str, to: str) -> Response:
    """One forward step, refused if the machine does not allow it.

    The route validates against `ALLOWED` rather than against `_THREAD_STEPS`, because
    the button set is a UI convenience and the state machine is the rule — a posted URL
    should meet the same gate a fleet worker does.
    """
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    try:
        outreach.advance(conn, tenant_id, thread_id, to,
                         reason=f"moved by {principal.subject}")
    except outreach.TransitionRefused as exc:
        return RedirectResponse(f"/threads?sel={thread_id}&error={_q(str(exc))}",
                                status_code=303)
    return RedirectResponse(f"/threads?sel={thread_id}", status_code=303)


@router.post("/threads/{thread_id}/draft", response_class=HTMLResponse)
def threads_draft(request: Request, principal: Operator, thread_id: str,
                  subject: Annotated[str, Form()] = "",
                  body: Annotated[str, Form()] = "") -> Response:
    """Write the pitch by hand — the Drafter's job, done by a person.

    It writes the same `message` row the agent would and lands on the same gate, so the
    approvals screen is exercised by real drafts. When the Drafter is written it
    replaces this and nothing downstream changes.
    """
    _require_write(principal)
    typed = {"subject": subject, "body": body}
    if not subject.strip() or not body.strip():
        return _threads_page(request, principal, sel=thread_id, form=typed,
                             error="A draft needs a subject and a body.",
                             status_code=400)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    try:
        outreach.draft(conn, tenant_id, thread_id, subject=subject, body=body)
    except outreach.TransitionRefused as exc:
        return _threads_page(request, principal, sel=thread_id, form=typed,
                             error=str(exc), status_code=400)
    return RedirectResponse("/approvals", status_code=303)


@router.post("/threads/{thread_id}/close/{outcome}", response_class=HTMLResponse)
def threads_close(principal: Operator, thread_id: str, outcome: str) -> Response:
    """Close a conversation, which also releases the counterparty.

    Both halves happen in `outreach.advance`: the thread leaves the partial unique index
    and `party.contact_state` follows it in the same transaction. Closing here is
    therefore the only way the shortlist gets somebody back, and doing it by hand in SQL
    would leave the two disagreeing.
    """
    _require_write(principal)
    if outcome not in outreach.CLOSED:
        raise HTTPException(status_code=400, detail=f"{outcome!r} is not a closing state.")
    conn = _conn()
    tenant_id = _tenant_id(conn)
    try:
        outreach.advance(conn, tenant_id, thread_id, outcome,
                         reason=f"closed by {principal.subject}")
    except outreach.TransitionRefused as exc:
        return RedirectResponse(f"/threads?sel={thread_id}&error={_q(str(exc))}",
                                status_code=303)
    return RedirectResponse(f"/threads?sel={thread_id}", status_code=303)


@router.get("/tracks", response_class=HTMLResponse)
def tracks(request: Request, principal: Operator, sel: str = "") -> Response:
    return _live(request, principal, research.tracks, sel or None, "track", "title")


def _campaigns_page(request: Request, principal: auth.Principal, *, sel: str = "",
                    error: str = "", form: dict[str, str] | None = None,
                    status_code: int = 200) -> Response:
    """The Campaigns view, plus whichever editor state the inspector is in.

    The selected campaign's inspector carries the shortlist — every contactable
    counterparty, each with a button that opens a thread. That list is the operator
    doing the Scout's job, and it is deliberately one decision per person rather than a
    single "shortlist everything": opening a thread takes somebody off the market for
    every other campaign, and a bulk button makes that consequence invisible.
    """
    conn = _conn()
    tenant_id = _tenant_id(conn)
    creating = sel == "new"

    if tenant_id is None:
        view = demo.View(key="campaigns", title="Campaigns", blurb="", stats=(), cols=(),
                         rows=(), empty="Add an artist first — a campaign hangs off one.")
        sel_row: dict[str, Any] | None = None
    else:
        view = research.campaigns(conn, tenant_id)
        if creating:
            sel_row = {"id": "new", "name": "New campaign",
                       "insp": research.new_campaign_sections(
                           conn, tenant_id, form=form, error=error)}
        else:
            sel_row = demo.select(view.rows, sel or None)
            if sel_row is not None:
                candidates = research.shortlist_candidates(conn, tenant_id)
                sel_row = dict(sel_row)
                sel_row["insp"] = sel_row["insp"] + (
                    demo.Section(
                        "Contactable — open a thread", "editlist",
                        tuple((c["name"], (c["roles"] or "no role recorded")
                               + ("" if c["searchable"] else " · not embedded"),
                               (("Open thread",
                                 f"/campaigns/{sel_row['id']}/thread/{c['id']}", "p"),))
                              for c in candidates)),
                    demo.Section("Note", "note", (
                        "Everyone here is contactable, which means no other campaign holds "
                        "an open thread with them — that is the partial unique index read "
                        "back through party.contact_state. Opening one takes them off this "
                        "list for every campaign at once, so it is one button per person "
                        "rather than one for the batch.",
                    )) if candidates else demo.Section("Nobody contactable", "note", (
                        "Every known counterparty is already in a thread, declined or "
                        "unusable. Prospecting finds more — map a source for an artist and "
                        "run it.",
                    )),
                )

    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, conn=conn, tenant_id=tenant_id,
             here="campaigns", view=view, sel=sel_row, live=True,
             insp_kicker="campaign",
             insp_title=(sel_row or {}).get("name", "—"),
             insp_new=None if creating else ("/campaigns?sel=new", "New campaign")),
        status_code=status_code,
    )


@router.get("/campaigns", response_class=HTMLResponse)
def campaigns(request: Request, principal: Operator, sel: str = "",
              error: str = "") -> Response:
    return _campaigns_page(request, principal, sel=sel, error=error)


@router.post("/campaigns", response_class=HTMLResponse)
def campaigns_create(request: Request, principal: Operator,
                     name: Annotated[str, Form()] = "",
                     party_id: Annotated[str, Form()] = "",
                     recording_id: Annotated[str, Form()] = "",
                     channel: Annotated[str, Form()] = "curator",
                     goal: Annotated[str, Form()] = "") -> Response:
    _require_write(principal)
    typed = {"name": name, "party_id": party_id, "recording_id": recording_id,
             "channel": channel, "goal": goal}
    if not name.strip():
        return _campaigns_page(request, principal, sel="new", form=typed,
                               error="A campaign needs a name.", status_code=400)
    if channel not in {c for c, _ in research.CHANNELS}:
        # The select offers only known values, so anything else is a crafted post —
        # rejected rather than coerced, the same rule `_validated_type` follows.
        return _campaigns_page(request, principal, sel="new", form=typed,
                               error=f"{channel!r} is not a supported channel.",
                               status_code=400)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return _campaigns_page(request, principal, sel="new", form=typed,
                               error="Add an artist before creating a campaign.",
                               status_code=400)
    row = outreach.create_campaign(conn, tenant_id, party_id=party_id, name=name,
                                   channel=channel, goal=goal,
                                   recording_id=recording_id or None)
    return RedirectResponse(f"/campaigns?sel={row['id']}", status_code=303)


@router.post("/campaigns/{campaign_id}/thread/{counterparty_id}",
             response_class=HTMLResponse)
def campaigns_open_thread(principal: Operator, campaign_id: str,
                          counterparty_id: str) -> Response:
    """Open a conversation with one counterparty.

    A `UniqueViolation` here is the §3c collision — somebody else got to them between
    the page rendering and the button being pressed — and it is reported as the fact it
    is rather than as an error, because nothing is wrong: the database did exactly what
    it was built to do.
    """
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    try:
        outreach.open_thread(conn, tenant_id, campaign_id=campaign_id,
                             counterparty_id=counterparty_id)
    except psycopg.errors.UniqueViolation:
        return RedirectResponse(
            f"/campaigns?sel={campaign_id}&error="
            + _q("Somebody already has an open thread with them — one open thread per "
                 "counterparty, across every campaign."),
            status_code=303)
    return RedirectResponse(f"/campaigns?sel={campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/state/{state}", response_class=HTMLResponse)
def campaigns_set_state(principal: Operator, campaign_id: str, state: str) -> Response:
    """Run, pause or close a campaign.

    `running` is the state in which the fleet may open threads against it, so starting
    one is a deliberate act with a button behind it rather than a side effect of
    creating it — the same shape as the send gate, one step earlier and much cheaper to
    get wrong.
    """
    _require_write(principal)
    if state not in ("draft", "running", "paused", "done"):
        raise HTTPException(status_code=400, detail=f"{state!r} is not a campaign state.")
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        outreach.set_campaign_state(conn, tenant_id, campaign_id, state)
    return RedirectResponse(f"/campaigns?sel={campaign_id}", status_code=303)


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
