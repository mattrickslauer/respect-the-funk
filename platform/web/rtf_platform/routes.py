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
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote_plus

import psycopg
from fastapi import (
    APIRouter, Cookie, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from rtf_platform import (
    agents, auth, db, demo, fleet, mcp, outreach, repo, research,
    settings as settings_mod, spend, statements,
)
from rtf_platform.domain import (
    ARTIST_STATUSES, CREDIT_ROLES, DEFAULT_TYPE, RECORDING_STATUSES, ArtistType,
    Platform, ProfileMode, canonical_isrc, unrecognised,
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
    # The Tracks audio column. `none` is an error tone rather than a neutral one on
    # purpose: a recording with no master cannot be measured, serviced, or classified,
    # so it is a blocked record and not merely an empty field.
    "master": "ok", "none": "err", "instrumental": "info", "radio_edit": "info",
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
           tenant_id: str | None = None, error: str = "") -> Response:
    """Eleven of the thirteen views are this call. The View carries its own columns,
    rows and stats; everything else is shell."""
    sel = demo.select(view.rows, sel_id)
    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, conn=conn, tenant_id=tenant_id,
             here=view.key, view=view, sel=sel, live=live, error=error,
             insp_kicker=kicker,
             insp_title=(sel or {}).get(title_key, "—")),
    )


#: Offered on the demo form. A range rather than a number, because nobody knows their
#: exact roster size off the top of their head and an empty required field loses the lead.
ROSTER_SIZES: tuple[str, ...] = ("1–5 artists", "6–20", "21–50", "50+", "Not a label")


#: Windows the public demo will run R1 against. A closed set, not free text: `as_of` is
#: interpolated into SQL (CockroachDB will not accept a placeholder for `AS OF SYSTEM
#: TIME`), and `agents.shortlist_as_of` validates the shape as a second gate. Two
#: independent checks on a public input that reaches a query is the right number.
#: Kept inside the window the cluster can actually serve. `gc.ttlseconds` was raised
#: from CockroachDB's 75-minute default to seven days, but raising it does not
#: retroactively un-collect: the GC threshold only moves forward, so the reachable depth
#: grows from the moment it was changed rather than jumping. Measured 2026-08-13: -10h
#: resolved, -18h did not. These are the windows that answer today.
DEMO_WINDOWS: dict[str, str] = {
    "now": "", "-15m": "-15m", "-1h": "-1h", "-3h": "-3h", "-6h": "-6h",
}


def _demo_stats(conn: psycopg.Connection, tenant_id: str) -> dict[str, Any]:
    """The counters on the public page, read live rather than baked in.

    A judge should be able to reload and watch these move while the fleet works. Baked
    numbers age into a lie by the afternoon, and this project's whole argument is that
    the database is the thing telling the truth.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM party
                   WHERE tenant_id = %(t)s AND party_class = 'counterparty') AS counterparties,
                 (SELECT count(*) FROM party
                   WHERE tenant_id = %(t)s AND party_class = 'counterparty'
                     AND profile_embedding IS NOT NULL) AS embedded,
                 (SELECT count(DISTINCT party_id) FROM party_fact
                   WHERE tenant_id = %(t)s AND dimension = 'genre') AS with_genre,
                 (SELECT count(*) FROM lesson WHERE tenant_id = %(t)s) AS lessons,
                 (SELECT count(*) FROM agent_run WHERE tenant_id = %(t)s) AS runs,
                 (SELECT coalesce(sum(cost_micro_usd), 0) FROM agent_run
                   WHERE tenant_id = %(t)s) AS micro_usd,
                 (SELECT count(*) FROM outbox
                   WHERE tenant_id = %(t)s AND state = 'sent') AS sent""",
            {"t": tenant_id})
        return dict(cur.fetchone())


@router.get("/demo/tune")
def demo_tune(artist: str = "hallow-youth", window: str = "now") -> JSONResponse:
    """R1, run read-only against the index as it stood in one of `DEMO_WINDOWS`.

    Public and unauthenticated on purpose — the point of the page is that a judge can
    run the query themselves. It is safe to expose because it cannot write: it calls
    `agents.shortlist_as_of`, which takes no `Gate` and spends nothing, holds no
    transaction, and reads a vector that already exists rather than embedding anything.
    """
    if window not in DEMO_WINDOWS:
        return JSONResponse({"error": "unknown window"}, status_code=400)

    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return JSONResponse({"error": "no tenant"}, status_code=503)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name FROM party
                WHERE tenant_id = %s AND slug = %s AND party_class = 'roster'""",
            (tenant_id, artist))
        act = cur.fetchone()
    if act is None:
        return JSONResponse({"error": "unknown artist"}, status_code=404)

    try:
        rows = agents.shortlist_as_of(conn, tenant_id, str(act["id"]),
                                      as_of=DEMO_WINDOWS[window], limit=12)
    except psycopg.InternalError as exc:
        # Asking for a timestamp the cluster has already garbage-collected is a real
        # answer, not a crash: time travel is bounded by `gc.ttlseconds`, and saying so
        # is more useful to a reader than a 500. The bound is the interesting part —
        # it is the cost of the feature, and this project does not hide costs.
        if "GC threshold" not in str(exc):
            raise
        return JSONResponse({
            "artist": act["name"], "window": window, "rows": [],
            "note": ("that moment has been garbage-collected — time travel reaches back "
                     "as far as gc.ttlseconds allows, and no further"),
        })
    return JSONResponse({
        "artist": act["name"],
        "window": window,
        "rows": [{"name": r["name"], "distance": round(float(r["distance"]), 4)}
                 for r in rows],
    })


def _landing(request: Request, principal: auth.Principal, *,
             sent: str = "", error: str = "",
             form: dict[str, str] | None = None) -> Response:
    # Read live. If the cluster is unreachable the page still renders — a landing page
    # that 500s because a counter could not be fetched is worse than one that says so.
    try:
        conn = _conn()
        stats = _demo_stats(conn, _tenant_id(conn) or "")
    except Exception:  # noqa: BLE001 — a counter is not worth losing the page over
        stats = {}
    return templates.TemplateResponse(
        request, "landing.html",
        _ctx(request, principal, sent=sent, error=error, form=form or {},
             roster_sizes=ROSTER_SIZES, stats=stats),
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
          kicker: str, title_key: str, error: str = "") -> Response:
    """A view built from migration 004's tables rather than from `demo.py`.

    Before the tenant row exists there is nothing to scope a query by, so these render
    the view's own empty state rather than erroring — a fresh cluster should be
    browsable before it has data, same rule the roster already follows.

    `error` carries a refused write back through its redirect, the same way `_cards`
    already does for the approvals and inbox screens: rendered above the table, so the
    operator keeps the row they were looking at instead of landing on an error page.
    """
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        empty = demo.View(key="", title="", blurb="", stats=(), cols=(), rows=(),
                          empty="Nothing here yet — save an artist first.")
        return _table(request, principal, empty, None, kicker, title_key, live=True,
                      conn=conn, tenant_id=tenant_id, error=error)
    return _table(request, principal, build(conn, tenant_id), sel_id, kicker,
                  title_key, live=True, conn=conn, tenant_id=tenant_id, error=error)


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
def queue(request: Request, principal: Operator, sel: str = "",
          error: str = "") -> Response:
    return _live(request, principal, research.queue, sel or None, "lead", "target",
                 error=error)


@router.post("/queue/{lead_id}/run", response_class=HTMLResponse)
def queue_run_now(principal: Operator, lead_id: str) -> Response:
    """Run now: bring this lead's next action forward so the next claim takes it.

    A POST from a form rather than a link, for the reason the inspector's actions block
    gives: a GET that changes a row is a row changed by a browser prefetch.

    What this does not do is run the agent. `fleet.expedite` has the argument — there is
    no orchestrator to call, and the console holding an HTTP request open across a
    provider fetch would be working outside the lease that makes the work restartable.
    The lead becomes due; a worker claims it on its next pass.
    """
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return RedirectResponse(
            "/queue?error=" + _q("There is no tenant yet, so there is no frontier."),
            status_code=303)
    try:
        fleet.expedite(conn, tenant_id, lead_id)
    except fleet.NotExpedited as exc:
        return RedirectResponse(f"/queue?sel={lead_id}&error={_q(str(exc))}",
                                status_code=303)
    return RedirectResponse(f"/queue?sel={lead_id}", status_code=303)


#: `fleet_console`, not `fleet`, because a handler named for its view shadows the module
#: of the same name for every line below it — and the shadow is silent until something
#: reaches for `fleet.expedite` and gets an `AttributeError` on a function object at
#: runtime. Named like `artists_console` and `imports_console` for the same reason.
@router.get("/fleet", response_class=HTMLResponse)
def fleet_console(request: Request, principal: Operator, sel: str = "") -> Response:
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
            # Last, and on every thread regardless of state, because the question it
            # answers is asked *after* the send rather than before it.
            extra.extend(research.justification_sections(
                conn, tenant_id, str(sel_row["id"])))
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


#: Tracks is the second console view that writes, and for the same reason Artists is the
#: first: the inspector is where a record is already open, so changing it there does not
#: cost the selection, the scroll position, or the comparison in progress. Everything
#: below is the editor that `/tracks` used to gesture at with three inert buttons.

_TRACKS_BLURB = (
    "Recordings — the masters, identified by ISRC. Credits decide who they belong to, "
    "so a collaboration is one row. Live from recording, and editable in place."
)


def _tracks_page(
    request: Request, principal: auth.Principal, *,
    sel: str = "", confirm: str = "", error: str = "", credit_error: str = "",
    banner: str = "", form: dict[str, str] | None = None, status_code: int = 200,
) -> Response:
    """The Tracks view, including whichever editor state the inspector is in.

    One function for the GET and for every rejected POST, so a validation failure
    renders the page the operator was already looking at with the message beside the
    field — the same shape `_artists_page` uses, for the same reason.

    `error` and `banner` are both refusals and are deliberately not the same argument.
    `error` belongs to a field: it is rendered inside the form that was rejected, next
    to what the operator typed. `banner` belongs to nothing on the page — it is carried
    back through a redirect by the two routes that have no form to return to (deleting a
    track, queueing an analysis) and is drawn above the table. Passing one value to both
    places renders every validation failure twice.
    """
    conn = _conn()
    tenant_id = _tenant_id(conn)
    creating = sel == "new"

    if tenant_id is None:
        view = demo.View(
            key="tracks", title="Tracks", blurb=_TRACKS_BLURB, stats=(), cols=(),
            rows=(), empty="Nothing here yet — save an artist first.",
        )
    else:
        view = research.tracks(
            conn, tenant_id, editing_id=(None if creating else sel or None),
            error=("" if creating else error), credit_error=credit_error,
            confirm_delete=(confirm == "delete"),
        )

    if creating:
        # A synthetic selection: there is no row yet, and the inspector is the form that
        # would make one.
        sel_row: dict[str, Any] | None = {
            "id": "new", "title": "New track",
            "insp": research.new_recording_sections(form=form, error=error),
        }
    else:
        sel_row = demo.select(view.rows, sel or None)

    return templates.TemplateResponse(
        request, "console/table.html",
        _ctx(request, principal, here="tracks", view=view, sel=sel_row, live=True,
             insp_kicker="track",
             insp_title=(sel_row or {}).get("title", "—"),
             insp_new=None if creating else ("/tracks?sel=new", "New track"),
             error=banner),
        status_code=status_code,
    )


@router.get("/tracks", response_class=HTMLResponse)
def tracks(request: Request, principal: Operator, sel: str = "", confirm: str = "",
           error: str = "") -> Response:
    # A GET's `error` only ever arrives from a redirect, so it is a banner. Nothing on
    # this page was just rejected — the form is showing the stored row.
    return _tracks_page(request, principal, sel=sel, confirm=confirm, banner=error)


def _recording_form(title: str, isrc: str, released_on: str,
                    status: str = "active") -> dict[str, str]:
    """What the form sent, kept verbatim so a rejected post redraws what was typed."""
    return {"title": title, "isrc_raw": isrc, "released_on": released_on,
            "status": status}


def _validated_recording(title: str, isrc: str, released_on: str) -> tuple[str, str, Any]:
    """The three fields every recording write shares, checked. Raises `ValueError`.

    The ISRC is the one worth care. `domain.canonical_isrc` returns "" for anything
    malformed rather than guessing at a repair, and storing that blank would silently
    turn "I typed the ISRC wrong" into "this recording has no ISRC" — a different and
    entirely plausible state, which is why it cannot be the fallback. A blank *input* is
    genuinely no identifier and is allowed; a non-blank one that will not canonicalise
    is refused and says so.
    """
    title = title.strip()
    if not title:
        raise ValueError("A track needs a title.")
    if not repo.slugify(title):
        raise ValueError("That title has no URL-safe form.")

    isrc = isrc.strip()
    canonical = canonical_isrc(isrc)
    if isrc and not canonical:
        raise ValueError(
            f"{isrc!r} is not a well-formed ISRC. ISO 3901 is two letters of country, "
            "three of registrant, two digits of year and five of designation — "
            "QZ-ABC-25-00001. Leave it blank if you do not have one yet.")

    released = released_on.strip()
    parsed: Any = None
    if released:
        try:
            parsed = date.fromisoformat(released)
        except ValueError:
            raise ValueError(f"{released!r} is not a date. Use YYYY-MM-DD.") from None

    return title, canonical, parsed


@router.post("/tracks", response_class=HTMLResponse)
def tracks_create(request: Request, principal: Operator,
                  title: Annotated[str, Form()] = "",
                  isrc: Annotated[str, Form()] = "",
                  released_on: Annotated[str, Form()] = "") -> Response:
    _require_write(principal)
    typed = _recording_form(title, isrc, released_on)
    try:
        clean_title, canonical, released = _validated_recording(title, isrc, released_on)
    except ValueError as exc:
        return _tracks_page(request, principal, sel="new", form=typed,
                            error=str(exc), status_code=400)

    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return _tracks_page(
            request, principal, sel="new", form=typed, status_code=400,
            error="There is no tenant yet — save an artist first, so the recording "
                  "has a label to belong to.")

    try:
        created = repo.create_recording(conn, tenant_id, title=clean_title,
                                        isrc=canonical, isrc_raw=isrc.strip(),
                                        released_on=released)
    except psycopg.errors.UniqueViolation as exc:
        # Two unique indexes reach here and they mean different things, so the message
        # says which: a slug clash is a second track with this title, an ISRC clash is
        # this exact master already being on the roster.
        clash = ("A recording with that ISRC is already here."
                 if "isrc" in str(exc).lower()
                 else f"A track already exists with the same URL key as {clean_title!r}.")
        return _tracks_page(request, principal, sel="new", form=typed,
                            error=clash, status_code=409)
    return RedirectResponse(f"/tracks?sel={created['id']}", status_code=303)


@router.post("/tracks/{recording_id}", response_class=HTMLResponse)
def tracks_update(request: Request, principal: Operator, recording_id: str,
                  title: Annotated[str, Form()] = "",
                  isrc: Annotated[str, Form()] = "",
                  released_on: Annotated[str, Form()] = "",
                  status: Annotated[str, Form()] = "active") -> Response:
    _require_write(principal)
    try:
        clean_title, canonical, released = _validated_recording(title, isrc, released_on)
    except ValueError as exc:
        return _tracks_page(request, principal, sel=recording_id, error=str(exc),
                            status_code=400)
    if status not in RECORDING_STATUSES:
        return _tracks_page(request, principal, sel=recording_id, status_code=400,
                            error=f"{status!r} is not a supported status.")

    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None or repo.get_recording(conn, tenant_id, recording_id) is None:
        raise HTTPException(status_code=404, detail="No such recording.")

    try:
        repo.update_recording(conn, tenant_id, recording_id, title=clean_title,
                              isrc=canonical, isrc_raw=isrc.strip(),
                              released_on=released, status=status)
    except psycopg.errors.UniqueViolation as exc:
        clash = ("A recording with that ISRC is already here."
                 if "isrc" in str(exc).lower()
                 else f"A track already exists with the same URL key as {clean_title!r}.")
        return _tracks_page(request, principal, sel=recording_id, error=clash,
                            status_code=409)
    return RedirectResponse(f"/tracks?sel={recording_id}", status_code=303)


@router.post("/tracks/{recording_id}/delete")
def tracks_delete(principal: Operator, recording_id: str) -> Response:
    """Reached only from the confirmation step, which names what goes with it.

    The bucket is cleaned second and its failure is deliberately not fatal, the same
    trade `masters_delete` makes and for the same reason: versioning with a 30-day
    noncurrent expiry means a half-done delete leaves recoverable bytes and no row,
    which is the recoverable direction. Rows gone but objects still billed is the one
    that rots, so the operator is told the keys rather than left to find them.
    """
    from rtf_platform import storage

    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return RedirectResponse("/tracks", status_code=303)

    orphaned = repo.delete_recording(conn, tenant_id, recording_id)
    stranded: list[str] = []
    for key in orphaned:
        try:
            _store().delete(key)
        except storage.StorageUnconfigured:
            stranded.append(key)
        except Exception:  # noqa: BLE001 - the message is the product
            stranded.append(key)

    if stranded:
        return RedirectResponse(
            "/tracks?error=" + _q(
                "The track is gone but "
                f"{len(stranded)} object(s) are still in the bucket: "
                f"{', '.join(stranded)}"),
            status_code=303)
    return RedirectResponse("/tracks", status_code=303)


@router.post("/tracks/{recording_id}/credits", response_class=HTMLResponse)
def tracks_credit_add(request: Request, principal: Operator, recording_id: str,
                      party_id: Annotated[str, Form()] = "",
                      role: Annotated[str, Form()] = "") -> Response:
    """Credit an artist. This is what decides whose record it is — and therefore
    whether it can be analysed at all, since `lead_scope_shape` requires a party."""
    _require_write(principal)
    if role not in CREDIT_ROLES:
        return _tracks_page(request, principal, sel=recording_id, status_code=400,
                            credit_error=f"{role!r} is not a credit role this console "
                                         f"offers.")

    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        raise HTTPException(status_code=404, detail="No such recording.")
    if repo.get_recording(conn, tenant_id, recording_id) is None:
        raise HTTPException(status_code=404, detail="No such recording.")
    # Checked rather than trusted from the select: the form is a hint to a browser, and
    # a posted party id from another tenant would otherwise become a real credit row.
    if repo.get_party(conn, tenant_id, party_id) is None:
        return _tracks_page(request, principal, sel=recording_id, status_code=400,
                            credit_error="No such artist on this roster.")

    if not repo.add_credit(conn, tenant_id, recording_id,
                           party_id=party_id, role=role):
        return _tracks_page(
            request, principal, sel=recording_id, status_code=409,
            credit_error="That artist already holds that credit on this track.")
    return RedirectResponse(f"/tracks?sel={recording_id}", status_code=303)


@router.post("/tracks/{recording_id}/credits/{credit_id}/delete")
def tracks_credit_delete(principal: Operator, recording_id: str,
                         credit_id: str) -> Response:
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is not None:
        repo.delete_credit(conn, tenant_id, recording_id, credit_id)
    return RedirectResponse(f"/tracks?sel={recording_id}", status_code=303)


@router.post("/tracks/{recording_id}/analyse")
def tracks_analyse(principal: Operator, recording_id: str) -> Response:
    """Queue `analyse_recording` against the newest stored master.

    The same call `masters_confirm` makes on upload, exposed as a control for the case
    that upload does not cover: the pipeline improved, and the operator wants a record
    measured again by the better version. `platform/bin --reanalyse` is the fleet-wide
    form of this; this is the one-record form.

    `queue_analysis` returns False for a real and non-obvious reason — no credited
    party, so a recording-scoped lead cannot satisfy `lead_scope_shape` — and this says
    so rather than redirecting to an unchanged page that looks like a click that missed.
    """
    from rtf_platform import assets

    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return RedirectResponse("/tracks", status_code=303)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM recording_asset
                WHERE tenant_id = %s AND recording_id = %s AND kind = 'master'
                  AND state = 'stored'
                ORDER BY created_at DESC LIMIT 1""",
            (tenant_id, recording_id))
        master = cur.fetchone()

    if master is None:
        return RedirectResponse(
            f"/tracks?sel={recording_id}&error=" + _q(
                "There is no stored master to analyse. Upload one — the analysis is "
                "queued from the upload itself."),
            status_code=303)

    if not assets.queue_analysis(conn, tenant_id, recording_id, str(master["id"])):
        return RedirectResponse(
            f"/tracks?sel={recording_id}&error=" + _q(
                "Nothing was queued. Either this master is already waiting in the "
                "frontier — one analysis per distinct file — or nobody is credited on "
                "the track, and a recording-scoped lead has to carry a party."),
            status_code=303)
    return RedirectResponse(f"/tracks?sel={recording_id}", status_code=303)


# ------------------------------------------------------------------- masters
#
# Three routes for one upload, because the bytes do not come here. The browser hashes
# the file, asks for a signed URL, PUTs to S3 directly, and then tells us it finished —
# and that last claim is checked against the bucket rather than believed.
#
# The two JSON routes are the only JSON in this console; everything else posts a form
# and gets a redirect, deliberately (see `_inspector.html`: an editor that swaps a
# fragment gives the operator no way to tell a saved record from a rendered one). An
# upload cannot work that way. A form POST would send the bytes here, and here is a
# Lambda with a 6MB synchronous payload cap — a 90MB master does not fail slowly, it
# fails at the platform with a `RequestEntityTooLarge` that names nothing in this repo.
# So the exchange is JSON and the page reloads at the end, which is the same proof a
# redirect gives.


def _store() -> Any:
    """The masters adapter, or a raise naming what to configure.

    Read per request rather than captured in `SETTINGS` at import: the bucket can be
    created after the console is already running, and `storage.load` caches the boto3
    client so this costs a dict lookup.
    """
    from rtf_platform import storage

    cfg = settings_mod.load()
    return storage.load(cfg.masters_bucket, cfg.region)


def _recording_exists(conn: psycopg.Connection, tenant_id: str,
                      recording_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM recording WHERE tenant_id = %s AND id = %s",
                    (tenant_id, recording_id))
        return cur.fetchone() is not None


def _refused(message: str, status: int = 400) -> Response:
    return JSONResponse({"error": message}, status_code=status)


@router.post("/tracks/{recording_id}/masters")
async def masters_claim(request: Request, principal: Operator,
                        recording_id: str) -> Response:
    """Reserve a row and hand back a signed PUT URL.

    Everything checkable is checked here, before a URL exists: the kind, the MIME type,
    the size. Refusing after the bytes have been transferred is not refusing — the
    upload has already been paid for in the operator's time and the account's bandwidth.
    """
    from rtf_platform import assets, storage

    _require_write(principal)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client bug, not a crash
        return _refused("that request body was not JSON.")

    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return _refused("there is no tenant yet — save an artist first.")
    if not _recording_exists(conn, tenant_id, recording_id):
        return _refused("no such recording.", status=404)

    try:
        store = _store()
        row, is_new = assets.claim(
            conn, tenant_id, recording_id,
            kind=str(body.get("kind", "")),
            content_hash=str(body.get("content_hash", "")),
            mime=str(body.get("mime", "")),
            size=int(body.get("size") or 0),
            uploaded_by=principal.subject,
            label=str(body.get("label", ""))[:200],
        )
    except storage.StorageUnconfigured as exc:
        return _refused(str(exc), status=503)
    except assets.AssetConflict as exc:
        # 409 specifically: the request is well-formed and collides with existing
        # state. The message names the other recording, because the operator's actual
        # mistake is almost always the file they picked.
        return _refused(str(exc), status=409)
    except assets.AssetRefused as exc:
        # 400: the request itself is wrong — an unsupported type, an empty file, a size
        # over the ceiling. Nothing about the stored state would make it acceptable.
        return _refused(str(exc), status=400)
    except (TypeError, ValueError):
        return _refused("that upload description was malformed.")

    asset_id = str(row["id"])
    payload = {
        "asset_id": asset_id,
        "state": row["state"],
        "key": row["object_key"],
        "is_new": is_new,
        "confirm": f"/tracks/{recording_id}/masters/{asset_id}/confirm",
    }
    # A row that is already `stored` needs no PUT at all — the operator picked a file
    # that is already here. Signing a URL anyway would invite a pointless 90MB upload
    # that overwrites an object with identical bytes.
    if row["state"] != "stored":
        payload["url"] = store.presign_put(row["object_key"],
                                           content_type=row["mime"])
    return JSONResponse(payload)


@router.post("/tracks/{recording_id}/masters/{asset_id}/confirm")
def masters_confirm(principal: Operator, recording_id: str, asset_id: str) -> Response:
    """Ask the bucket what arrived, and promote the row if it agrees.

    The browser saying its PUT succeeded is not evidence: a tab closed mid-transfer
    reports nothing, and S3 will happily keep the 60MB it received of a 90MB file. This
    is the sentence that checks.
    """
    from rtf_platform import assets, storage

    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return _refused("there is no tenant yet.")

    try:
        row = assets.confirm(conn, _store(), tenant_id, asset_id)
    except storage.StorageUnconfigured as exc:
        return _refused(str(exc), status=503)
    except assets.AssetRefused as exc:
        return _refused(str(exc), status=409)

    queued = assets.queue_analysis(conn, tenant_id, recording_id, asset_id)
    return JSONResponse({"asset_id": str(row["id"]), "state": row["state"],
                         "bytes": row["bytes"], "analysis_queued": queued})


@router.post("/tracks/{recording_id}/masters/{asset_id}/delete")
def masters_delete(principal: Operator, recording_id: str, asset_id: str) -> Response:
    """Drop the row, and the object with it when nothing else points at those bytes.

    A form POST and a redirect, unlike its two siblings, because no bytes move through
    here — this is an ordinary console write and it should behave like one.

    The object is deleted second and its failure is deliberately not fatal to the row.
    The bucket has versioning with a 30-day noncurrent expiry, so a delete that half
    happens leaves recoverable bytes and no row, which is the recoverable direction. The
    reverse — row gone from the table's point of view but still billed and still
    listed — is the one that rots.
    """
    from rtf_platform import assets, storage

    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return RedirectResponse(f"/tracks?sel={recording_id}", status_code=303)

    orphaned_key = assets.delete(conn, tenant_id, asset_id)
    error = ""
    if orphaned_key:
        try:
            _store().delete(orphaned_key)
        except storage.StorageUnconfigured:
            error = ("the row is gone but the object could not be deleted — no bucket "
                     "is configured, so nothing here can reach it.")
        except Exception as exc:  # noqa: BLE001 - the message is the product
            error = (f"the row is gone but the object is still in the bucket: {exc}. "
                     f"Key: {orphaned_key}")

    suffix = f"&error={_q(error)}" if error else ""
    return RedirectResponse(f"/tracks?sel={recording_id}{suffix}", status_code=303)


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

    # The ranking is recomputed here rather than carried from the page that rendered the
    # button, and the difference is the whole value of the record. A hidden field holding
    # the instant and the rank would be operator-supplied — which is to say forgeable, by
    # the one party with a motive to forge it, in the one place where the record exists to
    # answer for an irreversible act. Recomputed server-side it costs nothing (this read
    # is unpriced) and cannot be anything but what was true when the button was pressed.
    #
    # A counterparty who is not on the shortlist at all yields no decision, and the thread
    # honestly reads as hand-opened. See `agents.rank_of`.
    decided = None
    with conn.cursor() as cur:
        cur.execute("SELECT party_id FROM campaign WHERE tenant_id = %s AND id = %s",
                    (tenant_id, campaign_id))
        campaign_row = cur.fetchone()
    if campaign_row is not None:
        hlc, ranking = agents.shortlist_with_instant(
            conn, tenant_id, str(campaign_row["party_id"]))
        placed = agents.rank_of(ranking, counterparty_id)
        if placed is not None:
            decided = (hlc, placed[0], placed[1])

    try:
        outreach.open_thread(conn, tenant_id, campaign_id=campaign_id,
                             counterparty_id=counterparty_id, decided=decided)
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


# ------------------------------------------------------------------------- ask

def _ask_page(request: Request, principal: auth.Principal, *, asked: str = "",
              answer: Any = None, error: str = "") -> Response:
    """The Ask screen, in all four of its states: unconfigured, empty, answered, refused.

    One function rather than a GET handler and a POST handler that each build a context,
    because the two differ only in whether an answer exists and the failure mode of the
    other shape is a refusal that renders without the question that caused it — leaving
    the operator with an error and an empty box.
    """
    conn = _conn()
    return templates.TemplateResponse(
        request, "console/ask.html",
        _ctx(request, principal, conn=conn, tenant_id=_tenant_id(conn), here="ask",
             live=True, error=error, asked=asked, answer=answer,
             questions=mcp.QUESTIONS,
             mcp_configured=SETTINGS.mcp_configured,
             mcp_missing=_mcp_missing()),
    )


def _mcp_missing() -> str:
    """Which variables the Ask screen is short of, named individually.

    "Not configured" is not an actionable message, and this screen needs four things from
    three different places. Naming them is the difference between a page somebody fixes
    in a minute and one they file a bug about.
    """
    absent = [name for name, value in (
        ("COCKROACH_API_KEY", SETTINGS.cockroach_api_key),
        ("COCKROACH_CLUSTER_ID", SETTINGS.cockroach_cluster_id),
        ("OPENAI_API_KEY", SETTINGS.openai_api_key),
        ("DATABASE_URL", SETTINGS.database_url),
    ) if not value]
    if not absent:
        return ""
    return (f"{', '.join(absent)} " + ("is" if len(absent) == 1 else "are")
            + " unset. The Cockroach key is the one `ccloud auth login` writes to "
              "~/.config/.cockroachdb/credentials.json.")


@router.get("/ask", response_class=HTMLResponse)
def ask_form(request: Request, principal: Operator) -> Response:
    """The box, and the list of questions beside it. No cluster call and no spend — a GET
    that costs money is one a browser can make by being refreshed."""
    return _ask_page(request, principal)


@router.post("/ask", response_class=HTMLResponse)
def ask_run(request: Request, principal: Operator,
            q: Annotated[str, Form()] = "") -> Response:
    """Ask the managed MCP server one question.

    A POST that re-renders rather than redirecting, the same shape `POST /demo` uses:
    the answer is the page, and carrying a table of rows through a query string is not
    something a redirect can do. It is a POST rather than a GET because it spends money
    and a GET that spends money is one a prefetch can trigger.

    Every failure is caught by class and reported as itself. `SpendRefused` means the
    gate said no and the fix is a ceiling; `QuestionRefused` means this console does not
    know that question or would not run the statement; `McpRefused` means the server did.
    Collapsing the three into "something went wrong" would leave the operator unable to
    tell a $0 budget from an expired key, which are hours apart in what they require.
    """
    _require_write(principal)
    conn = _conn()
    tenant_id = _tenant_id(conn)
    if tenant_id is None:
        return _ask_page(request, principal, asked=q,
                         error="There is no tenant on this cluster yet, so there is "
                               "nothing to ask about. Save an artist first.")
    gate = spend.Gate.open(conn, tenant_id)
    try:
        answer = mcp.ask(q, tenant_id, gate)
    except spend.SpendRefused as exc:
        return _ask_page(request, principal, asked=q, error=str(exc))
    except (mcp.QuestionRefused, mcp.McpRefused, mcp.McpNotConfigured) as exc:
        return _ask_page(request, principal, asked=q, error=str(exc))
    return _ask_page(request, principal, asked=q, answer=answer)
