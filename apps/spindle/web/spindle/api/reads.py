"""The read side. Thirteen collections, one summary, and one reconstruction.

Every handler here is the same four lines: take the connection and the tenant from the
dependency, call the `research.rows_*` function that the console's own view calls, shape
it with `shapes`, and return it. That is the entire point of the design — there is no
SQL in this file, and there is no place for any to be added, because a query written
here would be a second definition of something `research.py` already answers and the two
would drift the first time one of them learned something.

`shapes.listing` puts a `limit`, a `returned` and a `truncated` on every collection, for
the reason stated there: `LIMIT 200` with no marker is a client that believes it has the
table and has a page.

## What is not here

**No pagination.** `research.LIMIT` is 200 and there is no cursor. That is honest rather
than finished: the console is a working surface, the filter is supposed to reach the
database rather than the browser, and adding an offset-based pager would be adding the
wrong one — offsets skip and duplicate rows under concurrent writes, and the right
answer is a keyset cursor per collection, which is a real piece of work and not one to
guess at before a client exists to shape it. `/counterparties` is the collection large
enough to feel this (14,170 rows on the cluster today), which is why it is the one that
got filters instead.

**No sort parameters.** Each collection's order is chosen in `research.py` and is part
of what the view means — the queue is pending-first then by score, threads are
awaiting-human-first. A client sorting a truncated page would be sorting 200 arbitrary
rows and calling it a ranking.

**No per-object GET.** `GET /api/v1/threads/{id}` is absent because nothing needs it
yet: the collections carry every field the detail view would, so a client that has the
list has the object. The one exception is `/threads/{id}/justification`, which is a
genuinely different question with a genuinely different answer and could not be folded
into a list.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from spindle import agents, outreach, research
from spindle.api import errors, shapes
from spindle.api.deps import Conn, SignedIn, Tenant, not_found

router = APIRouter()


# ------------------------------------------------------------------- summary

@router.get("/summary")
def summary(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """The counts the console draws its nav badges from, in one round trip.

    `outreach.counts` is one statement for five numbers, written that way because they
    render on every console page and a cluster that scales to zero charges per round
    trip. The same argument applies harder to a client that polls.

    The two counts outside that statement — parked leads and pending suggestions — are
    the two the console's `_nav` adds separately, and they are added here rather than
    left out so a client does not have to fetch `/queue` and `/suggestions` in full to
    render a badge.
    """
    counts = outreach.counts(conn, tenant)
    waiting = research.counts_work_waiting(conn, tenant)
    queue_counts = research.counts_queue(conn, tenant)
    return {
        "awaiting_human": int(counts.get("awaiting_human", 0)),
        "open_threads": int(counts.get("open_threads", 0)),
        "inbound": int(counts.get("inbound", 0)),
        "queued_unsent": int(counts.get("queued", 0)),
        "running_campaigns": int(counts.get("running", 0)),
        "leads_pending": int(queue_counts.get("pending", 0)),
        "leads_failed": int(queue_counts.get("failed", 0)),
        "leads_due": waiting["lead"],
        "suggestions_pending": len(research.pending_suggestions(conn, tenant)),
        # Stated rather than implied, so a client showing "3 queued" or an empty inbox
        # can say whether that is the data or the plumbing.
        #
        # Both were hardcoded `False` and both were true when written: nothing claimed
        # `outbox` and nothing wrote inbound mail. `sender.py` closed the first gap and
        # `040_mailbox.sql` closed the second, at which point these two constants became
        # the console telling clients an integration is missing while it runs. That is
        # the failure this repository keeps naming — reporting success is bad, and
        # reporting absence while working is the same lie pointed the other way.
        "sender_wired": True,
        "inbound_adapter_wired": True,
    }


@router.get("/today")
def today(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """The needs-you queue: everything asking something of a person, in one list.

    `research.today_items` composes it and this shapes it — including the grouping rule,
    which is the part worth not re-deriving in a client. Five candidate Deezer pages for
    one artist is *one* decision made five times, and a list that showed five entries
    would make an operator feel behind when they are not. Two front ends deciding that
    separately would report different queue lengths for the same cluster.

    Two kinds land here today and both are things the fleet genuinely will not decide:
    `suggestion_group` (an inferred match needing confirmation) and `parked_lead` (work
    stopped for a reason a person has to remove). An empty list is the correct and
    common state and means the fleet is blocked on nobody.

    `quiet` is the four background numbers the console shows beneath the queue — what
    is ticking over while nothing needs you.
    """
    items = research.today_items(conn, tenant)
    counts = research.counts_today(conn, tenant)
    return {
        "rows": [shapes.today_item(i) for i in items],
        "returned": len(items),
        # Numbers, not the console's pre-rendered strings. Same four values, same
        # statement, shaped for a client that may want to chart them.
        "quiet": {
            "leads_pending": int(counts["pending"]),
            "facts_live": int(counts["facts"]),
            "chunks_indexed": int(counts["chunks"]),
            "runs_24h": int(counts["runs"]),
        },
    }


# --------------------------------------------------------------- knowledge

@router.get("/artists")
def artists(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """The roster. `profiles` on each row is the count; the surfaces themselves are on
    `/artists/{id}/profiles`, because fetching presence per artist is a query per row
    and the list should not pay for it."""
    return shapes.listing(research.rows_artists(conn, tenant), shapes.artist)


@router.get("/artists/{artist_id}/profiles")
def artist_profiles(principal: SignedIn, conn: Conn, tenant: Tenant,
                    artist_id: str) -> dict[str, Any]:
    """Where we look for one artist.

    No existence check on the artist first. A party in another tenant and a party that
    does not exist both yield an empty list here, which is the same answer, and a
    preflight `SELECT` would buy a distinction that should not exist anyway — see
    `deps.not_found` on existence oracles.
    """
    return shapes.listing(research.rows_presence(conn, tenant, artist_id),
                          shapes.presence)


@router.get("/recordings")
def recordings(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """Recordings, each with its masters and its credits.

    Three queries for the whole tenant and the grouping done in Python, which is what
    `research.rows_recording_assets` exists for — the alternative is a query per row,
    and the table is the unit of work here rather than the record.
    """
    rows = research.rows_tracks(conn, tenant)
    assets = research.rows_recording_assets(conn, tenant)
    credits = research.rows_recording_credits(conn, tenant)
    return shapes.listing(
        rows, lambda r: shapes.recording(r, assets.get(str(r["id"]), []),
                                         credits.get(str(r["id"]), [])))


@router.get("/counterparties")
def counterparties(
    principal: SignedIn, conn: Conn, tenant: Tenant,
    q: Annotated[str, Query(description="Case-insensitive substring of the name.")] = "",
    contact_state: Annotated[str, Query(description="Exact contact_state.")] = "",
    searchable: Annotated[
        bool | None,
        Query(description="true: only those R1 can see. false: only those it cannot. "
                          "Omit for both."),
    ] = None,
) -> dict[str, Any]:
    """Everyone we could take a record to, filtered in the database.

    The three filters are why this endpoint has parameters and the others do not: there
    are 14,170 counterparties on the cluster and `LIMIT 200` means a client without
    filters is looking at 1.4% of them in alphabetical order. Filtering client-side
    would filter that page, not the table.

    `searchable=false` is the query worth knowing about. A counterparty with no
    embedding is invisible to the shortlist regardless of how good a match they would
    be, and this is how you get the list of them.
    """
    rows = research.rows_counterparties(
        conn, tenant, query=q, contact_state=contact_state, searchable=searchable)
    return shapes.listing(rows, shapes.counterparty,
                          filters={"q": q, "contact_state": contact_state,
                                   "searchable": searchable})


@router.get("/facts")
def facts(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """Everything the fleet believes. `total` here is exact — the counts come from a
    second statement over the whole table, so `truncated` is not a heuristic."""
    counts = research.counts_facts(conn, tenant)
    return shapes.listing(
        research.rows_facts(conn, tenant), shapes.fact,
        total=int(counts.get("total", 0)),
        by_status={k: int(counts.get(k, 0)) for k in ("live", "stale", "retracted")})


@router.get("/suggestions")
def suggestions(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """Pending matches an agent found by inference rather than measurement.

    Nothing promotes one automatically, by design. These are the rows the accept and
    reject actions act on, and they are exposed as a collection of their own because a
    client building a decision queue needs them without walking the roster.
    """
    return shapes.listing(research.pending_suggestions(conn, tenant), shapes.suggestion)


# ---------------------------------------------------------------- campaigns

@router.get("/campaigns")
def campaigns(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    return shapes.listing(research.rows_campaigns(conn, tenant), shapes.campaign)


@router.get("/threads")
def threads(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    return shapes.listing(research.rows_threads(conn, tenant), shapes.thread)


@router.get("/threads/{thread_id}/justification")
def justification(principal: SignedIn, conn: Conn, tenant: Tenant,
                  thread_id: str) -> dict[str, Any]:
    """Why this counterparty was contacted — the shortlist replayed as it stood.

    Three outcomes, and keeping them apart is the whole reason
    `025_decision_provenance.sql` exists:

      * a ranking, with `matched` saying whether the replay still puts this
        counterparty where the thread recorded it;
      * `not_justified` — nobody ever recorded a reason. A human opened the thread, or
        it predates the migration;
      * `history_expired` — a reason *was* recorded and the cluster no longer retains
        the memory to reconstruct it.

    An API that collapsed the last two into one 404 would be lying about one of them,
    and this is the endpoint somebody reaches for when a curator asks why they were
    emailed.

    `matched` is passed through unchanged and is the field to read. The replay alone is
    an unfalsifiable claim — whatever it returns looks like an answer. Compared against
    the rank and distance the thread stored at decision time it becomes checkable, and a
    client should show a `matched: false` as loudly as it shows the ranking.
    """
    try:
        found = outreach.justification(conn, tenant, thread_id)
    except LookupError as exc:
        raise not_found("thread", thread_id) from exc
    except outreach.NotJustified as exc:
        raise errors.Refusal(409, errors.NOT_JUSTIFIED, str(exc)) from exc
    except agents.HistoryExpired as exc:
        raise errors.Refusal(409, errors.HISTORY_EXPIRED, str(exc)) from exc

    return {
        "as_of": found["as_of"],
        "recorded_rank": found["recorded_rank"],
        "recorded_distance": found["recorded_distance"],
        "replayed_rank": found["replayed_rank"],
        "matched": found["matched"],
        "ranking": [shapes.scalar(r) for r in found["ranking"]],
    }


@router.get("/approvals")
def approvals(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """The send gate: every thread on `awaiting_human` with its newest unsent draft.

    Empty is the normal state and means the fleet is blocked on nobody. `queued_unsent`
    is carried alongside because a client showing an empty gate should be able to say
    how many prepared sends are sitting in `outbox` that nothing will claim.
    """
    rows = research.rows_approvals(conn, tenant)
    return shapes.listing(
        rows,
        lambda r: shapes.draft(r, research.rows_draft_basis(conn, tenant, r["thread_id"])),
        queued_unsent=research.count_outbox_pending(conn, tenant),
        sender_wired=True)


@router.get("/inbox")
def inbox(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """Replies, newest first.

    This used to open "**Nothing writes these.**", and it was accurate: there was no
    inbound adapter, so an empty result meant an absent integration rather than a quiet
    label, and `inbound_adapter_wired: false` was how a client could tell.

    `040_mailbox.sql` and `inbound.py` wrote the adapter, so the flag now reports `true`
    and an empty result means what it appears to mean. What it does **not** claim is that
    any particular tenant is connected — a tenant with no `mail_account` row receives on
    the platform identity, and one whose mailbox is in `failed` receives nothing at all.
    That is per-tenant state and belongs on the account view, not in a deployment-wide
    boolean that would be answering a different question than the one it is asked.
    """
    return shapes.listing(research.rows_inbox(conn, tenant), shapes.reply,
                          inbound_adapter_wired=True,
                          threads_awaiting_reply=research.count_awaiting_reply(conn, tenant))


# ------------------------------------------------------------------- system

@router.get("/queue")
def queue(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    counts = research.counts_queue(conn, tenant)
    return shapes.listing(
        research.rows_queue(conn, tenant), shapes.lead,
        total=int(counts.get("total", 0)),
        by_state={k: int(counts.get(k, 0))
                  for k in ("pending", "claimed", "failed", "done")})


@router.get("/runs")
def runs(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """Runs and errors. The counts are over the last 24 hours and the rows are not, so
    `total` is deliberately *not* passed to `listing` — it would make `truncated` mean
    "more than fit in a day" rather than "more than fit in the response"."""
    counts = research.counts_runs(conn, tenant)
    return shapes.listing(
        research.rows_runs(conn, tenant), shapes.run,
        last_24h={
            "total": int(counts.get("total", 0)),
            "errors": int(counts.get("errors", 0)),
            "refused": int(counts.get("refused", 0)),
            "lease_lost": int(counts.get("lease_lost", 0)),
            "cost_micro_usd": int(counts.get("micro", 0) or 0),
        })


@router.get("/fleet")
def fleet(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """The fleet: what the manifest declares, what this process can run, what has run.

    `state` is computed in `research.fleet_agents` and not here, because `off` and
    `declared` mean opposite things and two front ends deriving that separately is two
    chances to get it backwards.
    """
    waiting = research.counts_work_waiting(conn, tenant)
    rows = research.fleet_agents(conn, tenant, waiting=waiting)
    return shapes.listing(rows, shapes.agent, total=len(rows),
                          work_waiting=waiting)


@router.get("/budgets")
def budgets(principal: SignedIn, conn: Conn, tenant: Tenant) -> dict[str, Any]:
    """Per-artist token caps and what has been spent against them.

    Spend is summed from `agent_run` rather than decremented from a counter, and the
    reason is worth carrying to a client that might be tempted to cache it: a counter
    row is a serialization point under SERIALIZABLE, and ten workers on one launching
    artist would retry against each other on exactly the row meant to protect them.
    """
    rows = research.rows_budgets(conn, tenant)
    return shapes.listing(rows, shapes.budget, total=len(rows))
