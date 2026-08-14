"""The write side. Seven actions, and not one line of domain logic in this file.

Every handler here does the same three things: gate on `Writer`, translate a path and a
body into the arguments an existing domain function already takes, and translate the
exception it raises into a refusal. The transaction, the state machine, the locking and
the uniqueness are all somewhere else — `outreach.py`, `repo.py`, `assets.py` — and this
module is forbidden from reimplementing any of them.

That is not tidiness. It is the only way the guarantees survive a second front end.
`outreach.approve` writes the message stamp, the outbox row and the thread move in one
transaction, and the argument for why all three must be atomic is thirty lines long and
lives above the function. A handler here that opened its own transaction and did the
"same" three writes would be a second implementation of that argument, written by
somebody reading a summary of it, and it would be wrong in a way no test would notice
until a send double-fired.

## The guarantees, and where each is actually enforced

Not one of these is enforced in this file, which is the point. Each entry names where
it lives, because a reader checking that the API preserves them should go and read the
real thing rather than trust a list.

  * **Every route is gated.** `deps.Writer`, which depends on `deps.require_operator`.
    Structural: there is no way to annotate a write handler that skips the read gate.
    `tests/test_api_surface.py` walks the router and proves it for every route.
  * **Tenant scoping.** `deps.current_tenant` resolves it once per request and every
    domain call takes it as its second argument. `tests/test_tenant_scoping.py` parses
    this package along with the rest of `rtf_platform/` and would fail on any statement
    here that lacked a predicate — there are no statements here at all, which is the
    strongest form of passing that test.
  * **One open thread per counterparty.** The partial unique index
    `one_open_thread_per_counterparty`. `outreach.open_thread` lets the `UniqueViolation`
    propagate; `open_thread` below turns it into `thread_occupied`.
  * **Outbox idempotency.** `UNIQUE (message_id)` on `outbox`. `outreach.approve` lets
    the `UniqueViolation` propagate; `approve_draft` below turns it into
    `already_queued` — a refusal, not an error, because the first click worked.
  * **The state machine.** `outreach.ALLOWED`, checked under a `FOR UPDATE` inside
    `outreach.advance`. A posted transition meets exactly the gate a fleet worker does.
  * **Decision provenance is not client-supplied.** `open_thread` recomputes the
    shortlist server-side. See the comment there; it is the one guarantee this file
    could plausibly have broken by accepting a convenient parameter.

## What was asked for and is not here

**No `POST /threads/{id}/draft`.** Writing a pitch by hand is on the console and could
be exposed identically — `outreach.draft` takes a subject and a body and lands on the
same gate. It is left out because it was not on the list of actions the agentic console
needs, and an action that exists because it was easy is an action nobody has decided the
shape of.

**No campaign create, no campaign state change, no queue expedite, no fleet toggle.**
Same reason. Every one is a one-line call to an existing function and none was asked
for; adding them now fixes their request shapes before a client has an opinion.

**Nothing sends.** `approve_draft` prepares a send in one transaction and performs
none, because no provider is wired and nothing claims `outbox`. A client should render
that as the honest state it is rather than as a completed action — `/api/v1/approvals`
returns `sender_wired: false` for exactly this.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Body

from rtf_platform import agents, assets, outreach, repo, research
from rtf_platform.api import errors
from rtf_platform.api.deps import Conn, Tenant, Writer, not_allowed, not_found

router = APIRouter()


# ----------------------------------------------------------------- approvals

@router.post("/drafts/{message_id}/approve")
def approve_draft(principal: Writer, conn: Conn, tenant: Tenant,
                  message_id: str) -> dict[str, Any]:
    """Prepare the send. The irreversible half of the product, and it sends nothing.

    `outreach.approve` stamps the message, writes the `outbox` row and moves the thread
    to `queued` in one transaction, sharing the idempotency key the message already
    carries. Everything that makes that safe is in that function.

    **A double approve is a refused insert, not a second send.** `UNIQUE (message_id)`
    on `outbox` is what refuses it, and it is reported here as the fact it is rather
    than as a failure: the first call prepared the send and the database declined to
    prepare it twice. A client may safely retry this endpoint — that is the whole
    property the constraint buys — and it will get `409 already_queued` rather than a
    second copy in flight.

    The driver's message is not passed through. It names a constraint, which tells an
    operator nothing and tells anybody else more about the schema than a request should
    return.
    """
    thread_id = research.thread_of_unsent_draft(conn, tenant, message_id)
    if thread_id is None:
        raise errors.Refusal(
            409, errors.NO_DRAFT_WAITING,
            "That draft is no longer waiting. It has already been approved, or it was "
            "never an unsent outbound message on a thread in this tenant.")
    try:
        outreach.approve(conn, tenant, thread_id, message_id,
                         approver=principal.subject)
    except psycopg.errors.UniqueViolation as exc:
        raise errors.Refusal(
            409, errors.ALREADY_QUEUED,
            "Already queued — the first approval prepared the send, and the database "
            "refused a second copy.") from exc
    except outreach.TransitionRefused as exc:
        raise errors.Refusal(409, errors.TRANSITION_REFUSED, str(exc)) from exc

    return {"thread_id": thread_id, "message_id": message_id, "thread_state": "queued",
            # Stated on every successful approval, because "it worked" and "it was sent"
            # are different and a client rendering the first as the second would be
            # telling an operator a pitch had left when nothing claims the outbox.
            "sent": False,
            "note": "The send is prepared and has not happened. Nothing claims outbox."}


@router.post("/drafts/{message_id}/reject")
def reject_draft(principal: Writer, conn: Conn, tenant: Tenant,
                 message_id: str,
                 reason: Annotated[str, Body(embed=True)] = "") -> dict[str, Any]:
    """Send the draft back rather than deleting it.

    The message row stays. What the drafter wrote and what an operator refused is the
    only training signal this part of the product produces, and deleting it to keep a
    list tidy would throw it away. The thread returns to `drafted`, so rejecting costs
    the thread nothing.
    """
    thread_id = research.thread_of_unsent_draft(conn, tenant, message_id)
    if thread_id is None:
        raise errors.Refusal(
            409, errors.NO_DRAFT_WAITING,
            "That draft is no longer waiting. It has already been approved, or it was "
            "never an unsent outbound message on a thread in this tenant.")
    try:
        outreach.reject(conn, tenant, thread_id, message_id,
                        reason=reason.strip() or f"rejected by {principal.subject}")
    except outreach.TransitionRefused as exc:
        raise errors.Refusal(409, errors.TRANSITION_REFUSED, str(exc)) from exc
    return {"thread_id": thread_id, "message_id": message_id,
            "thread_state": "drafted"}


# ----------------------------------------------------------------- campaigns

#: `campaign_channel_known` in `010_outreach.sql`, as a Python set. Duplicated from the
#: schema deliberately and narrowly: the CHECK is the authority and would refuse a bad
#: value anyway, but it would refuse it as a driver-level `CheckViolation` naming a
#: constraint, which tells a client author nothing about what the legal values are.
#: Validated here so the refusal can list them. `test_api_surface` asserts this set
#: still matches the migration, so the copy cannot go stale unnoticed.
CHANNELS: frozenset[str] = frozenset({"curator", "ugc", "press", "radio", "sync"})


@router.post("/campaigns")
def create_campaign(
    principal: Writer, conn: Conn, tenant: Tenant,
    artist_id: Annotated[str, Body(embed=True)],
    name: Annotated[str, Body(embed=True)],
    channel: Annotated[str, Body(embed=True)] = "curator",
    goal: Annotated[str, Body(embed=True)] = "",
    recording_id: Annotated[str | None, Body(embed=True)] = None,
) -> dict[str, Any]:
    """Start a campaign. It begins in `draft` and opens nothing.

    **Creating is not running.** `running` is the state in which the fleet may open
    threads against a campaign, so starting one stays a second, deliberate act — the
    same shape as the send gate, one step earlier and much cheaper to get wrong. This
    endpoint cannot put a campaign into `running`, and there is no `state` parameter for
    it to accept; a client that wants to run one has to say so separately, and today
    that control exists only on the console.

    `recording_id` is genuinely optional and `None` is a real answer, not a missing one:
    an artist-level push — a profile feature, a residency announcement — is a real
    campaign with no single recording behind it, which is why the column is nullable.

    `name` is required, and no default is invented for it. `campaign.name` is `NOT NULL`
    with no default, and a name derived from the artist and the date would be a label
    nobody chose appearing in a list everybody reads.
    """
    if channel not in CHANNELS:
        raise not_allowed("campaign channel", channel, CHANNELS)
    if not name.strip():
        raise errors.Refusal(
            400, errors.NOT_ALLOWED_VALUE,
            "A campaign needs a name — it is how it is identified everywhere it is "
            "listed, and there is no sensible default for it.")
    if not artist_id.strip():
        raise errors.Refusal(
            400, errors.NOT_ALLOWED_VALUE,
            "A campaign needs an artist. Lessons accumulate on the party, which is why "
            "campaign.party_id is NOT NULL.")

    try:
        row = outreach.create_campaign(
            conn, tenant, party_id=artist_id, name=name, channel=channel, goal=goal,
            recording_id=recording_id)
    except psycopg.errors.ForeignKeyViolation as exc:
        # Either id could be the bad one; the message says so rather than guessing.
        raise errors.Refusal(
            404, errors.NOT_FOUND,
            f"No artist {artist_id!r}"
            + (f" or recording {recording_id!r}" if recording_id else "")
            + " in this tenant.") from exc

    return {"id": str(row["id"]), "name": row["name"], "state": row["state"],
            "channel": row["channel"],
            "note": "Created as a draft. It opens no threads until it is running."}


# -------------------------------------------------------------------- threads

@router.post("/campaigns/{campaign_id}/threads")
def open_thread(principal: Writer, conn: Conn, tenant: Tenant, campaign_id: str,
                counterparty_id: Annotated[str, Body(embed=True)]) -> dict[str, Any]:
    """Open a conversation with one counterparty, and take them off the shortlist.

    **The ranking is recomputed here and cannot be supplied by the caller.** That is the
    one guarantee in this file that an API is uniquely able to break, and the reason is
    worth stating at length because a `decided_rank` parameter would look so reasonable.

    `thread.decided_at_hlc/rank/distance` is the record that answers *why was this
    person contacted* — reachable from any message anybody actually sent, and the one
    artifact that matters the moment something goes wrong. A client-supplied rank is
    operator-supplied, which is to say forgeable, by the one party with a motive to
    forge it, in the one place the record exists to answer for an irreversible act.
    Recomputed server-side it costs nothing (the read is unpriced) and cannot be
    anything but what was true when the call was made. The console makes the same
    argument in `campaigns_open_thread` and reaches the same code.

    A counterparty who is not on the shortlist at all yields no decision, and the thread
    honestly reads as hand-opened — `025_decision_provenance.sql` argues at length that
    an absent reason must be left absent rather than given a plausible default, because
    "the ranking at the instant somebody typed a name" reads exactly like a real
    justification.

    A `UniqueViolation` is `one_open_thread_per_counterparty` doing its job: somebody
    else is already talking to this person, across every campaign. Reported as that fact
    rather than as an error, because nothing is wrong.
    """
    party_id = research.campaign_party_id(conn, tenant, campaign_id)
    if party_id is None:
        raise not_found("campaign", campaign_id)

    decided = None
    hlc, ranking = agents.shortlist_with_instant(conn, tenant, party_id)
    placed = agents.rank_of(ranking, counterparty_id)
    if placed is not None:
        decided = (hlc, placed[0], placed[1])

    try:
        row = outreach.open_thread(conn, tenant, campaign_id=campaign_id,
                                   counterparty_id=counterparty_id, decided=decided)
    except psycopg.errors.UniqueViolation as exc:
        raise errors.Refusal(
            409, errors.THREAD_OCCUPIED,
            "Somebody already has an open thread with them — one open thread per "
            "counterparty, across every campaign. Close that one first.") from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        # A counterparty id that names nothing in this tenant. Caught rather than left
        # to become a 500, because it is a caller mistake with an obvious remedy.
        raise not_found("counterparty", counterparty_id) from exc

    return {
        "id": str(row["id"]),
        "state": row["state"],
        "campaign_id": str(row["campaign_id"]),
        "counterparty_id": str(row["counterparty_id"]),
        # None when the counterparty was not on the shortlist. Not an omission — see
        # above; the client should render "opened by hand" and not "rank unknown".
        "decided": None if decided is None else {
            "as_of": str(row["decided_at_hlc"]),
            "rank": row["decided_rank"],
            "distance": float(row["decided_distance"]),
        },
    }


@router.post("/threads/{thread_id}/close")
def close_thread(principal: Writer, conn: Conn, tenant: Tenant, thread_id: str,
                 outcome: Annotated[str, Body(embed=True)],
                 reason: Annotated[str, Body(embed=True)] = "") -> dict[str, Any]:
    """Close a conversation, which also releases the counterparty.

    `outcome` is one of `closed_won`, `closed_lost`, `closed_no_reply`. Validated
    against `outreach.CLOSED` — the same definition the partial index's predicate, the
    release logic and every "open" count mean — rather than against a list written here,
    so this endpoint cannot come to disagree with the database about what closed means.

    Both halves happen inside `outreach.advance`: the thread leaves the partial unique
    index and `party.contact_state` follows it in the same transaction. Closing through
    this endpoint is therefore the only way a client can return somebody to the
    shortlist, and doing it with a bare UPDATE would leave the two disagreeing.

    Closing also queues a `distil_lesson` lead, because a closed thread is the only
    moment the system knows how an approach actually went. That happens in `advance`
    too; nothing here has to remember it.
    """
    if outcome not in outreach.CLOSED:
        raise not_allowed("closing state", outcome, outreach.CLOSED)
    try:
        was = outreach.advance(conn, tenant, thread_id, outcome,
                               reason=reason.strip() or f"closed by {principal.subject}")
    except outreach.TransitionRefused as exc:
        # Covers both a genuinely illegal move and a thread that does not exist — the
        # exception carries `was == "missing"` for the latter. Kept as one refusal
        # rather than split, because the message already says which, in words.
        raise errors.Refusal(409, errors.TRANSITION_REFUSED, str(exc)) from exc
    return {"id": thread_id, "was": was, "state": outcome,
            "counterparty_released": True}


# ---------------------------------------------------------------- recordings

@router.post("/recordings/{recording_id}/analyse")
def analyse_recording(principal: Writer, conn: Conn, tenant: Tenant,
                      recording_id: str) -> dict[str, Any]:
    """Queue `analyse_recording` against the current master.

    Queues a lead; does not analyse. There is no orchestrator to call, and holding an
    HTTP request open across an audio fetch would be working outside the lease that
    makes the work restartable. A worker claims it on its next pass.

    `assets.current` is what "the master" means — stored, of this kind, and not
    superseded. A remaster does not delete the master that was serviced to radio in
    March, it just stops being the current one, and analysing the superseded file would
    spend money measuring a track nobody ships.

    `queue_analysis` returns False for two reasons a caller can act on and they are
    reported together, because the endpoint cannot distinguish them without a second
    query and neither is a failure: either this master is already waiting in the
    frontier (`UNIQUE (tenant_id, target_hash)`, one analysis per distinct file,
    forever), or nobody is credited on the track and a recording-scoped lead has to
    carry a party (`lead_scope_shape`).
    """
    master = assets.current(conn, tenant, recording_id, "master")
    if master is None:
        raise errors.Refusal(
            409, errors.NO_MASTER,
            "There is no current stored master to analyse. Upload one — the analysis "
            "is queued from the upload itself.")

    if not assets.queue_analysis(conn, tenant, recording_id, str(master["id"])):
        raise errors.Refusal(
            409, errors.NOTHING_QUEUED,
            "Nothing was queued. Either this master is already waiting in the frontier "
            "— one analysis per distinct file — or nobody is credited on the track, and "
            "a recording-scoped lead has to carry a party.")
    return {"recording_id": recording_id, "asset_id": str(master["id"]),
            "queued": True}


# --------------------------------------------------------------- suggestions

@router.post("/suggestions/{suggestion_id}/accept")
def accept_suggestion(principal: Writer, conn: Conn, tenant: Tenant,
                      suggestion_id: str) -> dict[str, Any]:
    """Confirm a match. Writes the surface and marks the suggestion, in one transaction.

    A name match is inference: two acts can share a name, and a wrong accept quietly
    attaches somebody else's catalogue to your artist. That is why nothing promotes one
    automatically and why this is an endpoint rather than a background job.

    `repo.SuggestionUnacceptable` — a payload `harvested.Presence.parse` rejects, or a
    suggestion kind with no accept path — becomes a refusal naming the reason. It is
    explicitly not swallowed into a 200 with no effect: a silent no-op on a broken
    accept is worse than a refusal, because an operator clicking Accept needs to know it
    did not work and why.
    """
    try:
        repo.accept_suggestion(conn, tenant, suggestion_id,
                               by=principal.subject or "operator")
    except repo.SuggestionUnacceptable as exc:
        raise errors.Refusal(
            400, errors.SUGGESTION_UNACCEPTABLE, exc.reason) from exc
    return {"id": suggestion_id, "state": "accepted"}


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(principal: Writer, conn: Conn, tenant: Tenant,
                      suggestion_id: str) -> dict[str, Any]:
    """Turn a candidate down. The row stays, marked, so the same match is not offered
    again and the refusal is itself a signal."""
    repo.reject_suggestion(conn, tenant, suggestion_id,
                           by=principal.subject or "operator")
    return {"id": suggestion_id, "state": "rejected"}
