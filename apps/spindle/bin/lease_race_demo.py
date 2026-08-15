#!/usr/bin/env python3
"""Watch two workers race for one lead, and watch the loser get refused.

    cd apps/spindle/web && set -a && . ../../../.env && set +a
    .venv/bin/python ../bin/lease_race_demo.py            # the whole story
    .venv/bin/python ../bin/lease_race_demo.py --slow      # paced for a screen recording
    .venv/bin/python ../bin/lease_race_demo.py --act 2     # just the `ingest-cli` act

This is the visible version of `apps/spindle/web/tests/test_lease_race.py`. The tests prove
the property; this makes it *narratable* — one lead, two real connections to the real
CockroachDB cluster, and a printed transcript of who was allowed to write and who was
told no.

## The bug being shown

`PLATFORM-SPEC §0` is that agents do not call each other: they claim a row, work it, and
write the result back. There is no orchestrator, so the only thing standing between two
workers and the same piece of work is what the database will let them do. `fleet.claim`
hands a lead to exactly one worker with `FOR UPDATE SKIP LOCKED` and stamps a lease on it.

A lease is held by a *clock*, which is what makes the fleet restartable — a worker that is
OOM-killed strands nothing, because `lease_expires_at` passes and the lead comes back on
its own with no supervisor in the topology. The cost of that design is the window this
demo exercises: `LEASE_SECONDS` bounds a claim, not a fetch. `embed_batch` and the source
adapters each make one HTTP call that can genuinely outlive the lease, and while worker A
is blocked on that socket, worker B can legitimately claim the same lead. Then A's call
returns and A tries to write for a lead that has been B's for a minute.

The fence that was supposed to stop it read `WHERE id = %s AND owner_agent = %s AND
lease_expires_at > now()`. It did not, and the reason is act 2 below: `owner_agent` is the
worker's **name**, and `ingest.py --worker` defaults it to the constant `"ingest-cli"`. Two
drains started from two terminals are the same string, so A's fence matched on B's fresh
lease and both wrote. The measured consequence was three `agent_run` rows in state `ok`
for one lead and three duplicate `party_metric` rows behind them.

`apps/spindle/schema/013_lease_token.sql` closed it with one nullable column. `claim` stamps a
fresh `gen_random_uuid()` on every row it takes and returns it with the row; every fence
carries it back. A second claim overwrites the column, so the first claim's token stops
matching the instant its claim stops being current. It is a *capability*: no caller
supplies it, so no caller can get it wrong.

## Why there are two acts

Act 1 is the race with two differently-named workers. It is the legible version, and it
would also have passed against the broken fence — different names, different `owner_agent`,
so the old `WHERE` clause refused it too.

Act 2 runs the identical race with **both workers named `ingest-cli`**. Every column the
old fence could see is now identical between the stale claim and the live one. Only the
token differs. That is the act that actually demonstrates migration 013, and the reason
the demo does not stop after act 1.

## Safety

This talks to the live shared cluster, so it is built not to be able to touch anything
real. It mints its own lead `kind` per run — `lease_race_demo_<uuid8>` — which no entry in
`agents.REGISTRY` handles, so no drain can ever claim these rows and no `claim` issued here
can ever reach one of the seven thousand pending `embed_party` leads. It creates its own
fixture lead under that kind, and deletes exactly those rows on the way out, qualified on
both `tenant_id` and the generated kind. There is no unqualified `DELETE` or `UPDATE`
anywhere in this file, and nothing here mutates a row it did not itself insert — which is
also why it is safe to run repeatedly, and safe to run twice at once.

The tenant is resolved by slug rather than by "the only tenant", because a concurrent
`pytest` run creates throwaway tenants and that lookup is true right up until it is not.

Act 2 writes the literal string `ingest-cli` into `owner_agent`, because that name is the
whole point of the act. It is inert: it lands only on this run's own fixture lead, and
every write that follows is fenced on that lead's id. This demo deliberately never calls
`fleet.worker`, whose shutdown tidy-up updates by worker name with no `kind` in the
predicate and would, under that literal name, release the leases of a real drain running
alongside it. That is the defect `test_lease_race.WorkerShutdownIsNotFenced` documents.

The cleanup runs in a `finally`, so a Ctrl-C in the middle of a take still tidies up.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from spindle import fleet  # noqa: E402

TENANT_SLUG = "respect-the-funk"

#: How long each `--slow` beat holds on screen. Fast enough that the whole transcript fits
#: in a sixty-second clip, slow enough to read a line before the next one lands.
BEAT = 1.1


def short(value: object) -> str:
    """First eight characters of a UUID, with an ellipsis. Long enough to see two tokens
    differ at a glance, short enough that the transcript lines stay aligned on screen."""
    return f"{str(value)[:8]}…"


class Narrator:
    """Prints the transcript. Pauses between beats only when `--slow` asked it to."""

    def __init__(self, slow: bool) -> None:
        self.slow = slow

    def beat(self, line: str = "") -> None:
        print(line, flush=True)
        if self.slow and line:
            time.sleep(BEAT)

    def heading(self, line: str) -> None:
        self.beat()
        self.beat(f"\033[1m{line}\033[0m")
        self.beat("─" * 68)


class Fixture:
    """The demo's private corner of the cluster: one kind, one lead, exact cleanup.

    Holds the two connections as well, because the race is between two *sessions*. Running
    both claims down one connection would be two statements in sequence and would
    demonstrate nothing about concurrency.
    """

    def __init__(self, url: str, slug: str) -> None:
        self.a = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self.b = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self.kind = f"lease_race_demo_{uuid.uuid4().hex[:8]}"
        with self.a.cursor() as cur:
            cur.execute("SELECT id FROM tenant WHERE slug = %s", (slug,))
            row = cur.fetchone()
        if row is None:
            raise SystemExit(f"tenant {slug!r} is not in this cluster")
        self.tenant = str(row["id"])

    def lead(self) -> str:
        """One claimable fixture lead, due now, of this run's private kind."""
        lead_id = str(uuid.uuid4())
        with self.a.cursor() as cur:
            cur.execute(
                """INSERT INTO lead (id, tenant_id, scope_kind, kind, adapter, target,
                                     target_hash, next_action_at, reason)
                   VALUES (%s, %s, 'tenant', %s, 'demo', %s, %s, now(),
                           'lease race demo fixture — safe to delete')""",
                (lead_id, self.tenant, self.kind, lead_id, f"{self.kind}:{lead_id}"))
        return lead_id

    def expire(self, lead_id: str) -> None:
        """Drag one lead's lease into the past.

        Standing in for a `fetch` that outlived `LEASE_SECONDS`, without making the
        audience wait two minutes to see it. The clock is the only thing being faked: the
        row this leaves behind is bit-for-bit what a real slow fetch would have left, and
        every fence downstream reads it the same way.
        """
        with self.a.cursor() as cur:
            cur.execute(
                """UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute'
                    WHERE tenant_id = %s AND id = %s AND kind = %s""",
                (self.tenant, lead_id, self.kind))

    def state(self, lead_id: str) -> dict:
        with self.a.cursor() as cur:
            cur.execute(
                "SELECT state, owner_agent, lease_token, attempts FROM lead "
                "WHERE tenant_id = %s AND id = %s", (self.tenant, lead_id))
            return cur.fetchone()

    def close(self) -> None:
        """Delete exactly what this run inserted, then hang up.

        Both predicates matter. `kind` alone carries a UUID nobody else generated and
        would do, but a `DELETE` against a cluster holding real business data should not
        rest on one condition being unique in practice. `agent_run.lead_id` is
        `ON DELETE SET NULL`, so the run rows go first or they are left orphaned rather
        than erroring.
        """
        try:
            with self.a.cursor() as cur:
                cur.execute(
                    """DELETE FROM agent_run
                        WHERE tenant_id = %s
                          AND lead_id IN (SELECT id FROM lead
                                           WHERE tenant_id = %s AND kind = %s)""",
                    (self.tenant, self.tenant, self.kind))
                cur.execute("DELETE FROM lead WHERE tenant_id = %s AND kind = %s",
                            (self.tenant, self.kind))
        finally:
            self.a.close()
            self.b.close()


def act_one(fix: Fixture, say: Narrator) -> None:
    """Two named workers, one lead: the race, and the refusal."""
    say.heading("Act 1 — a lease lapses mid-fetch and a second worker takes the lead")

    lead_id = fix.lead()
    say.beat(f"  a lead is on the frontier          lead={short(lead_id)}  "
             f"kind={fix.kind}")

    first = fleet.claim(fix.a, fix.tenant, "worker-A", kinds=[fix.kind])[0]
    say.beat(f"  worker-A claims lead {short(lead_id)}    token={short(first['lease_token'])}")

    contested = fleet.claim(fix.b, fix.tenant, "worker-B", kinds=[fix.kind])
    say.beat(f"  worker-B asks for work             -> nothing ({len(contested)} leads) "
             f"— a live lease is not claimable")

    say.beat("  worker-A is still fetching…        its 120s lease runs out")
    fix.expire(lead_id)

    second = fleet.claim(fix.b, fix.tenant, "worker-B", kinds=[fix.kind])[0]
    say.beat(f"  worker-B claims lead {short(lead_id)}    "
             f"token={short(second['lease_token'])}   <- same lead, new token")

    say.beat("  worker-A's fetch returns. it writes.")
    try:
        fleet.complete(fix.a, first, fleet.Outcome(summary="worker-A's stale write"),
                       agent_name="worker-A")
        say.beat("  worker-A completes                 -> \033[31mOK — THE FENCE FAILED\033[0m")
        raise SystemExit("the stale claim was allowed to complete; the fence is broken")
    except fleet.LeaseLost as exc:
        say.beat("  worker-A tries to complete         -> \033[31mREFUSED: lease lost\033[0m")
        say.beat(f"     {exc}")

    row = fix.state(lead_id)
    say.beat(f"  the row is untouched               state={row['state']}  "
             f"owner={row['owner_agent']}  attempts={row['attempts']}")

    fleet.complete(fix.b, second, fleet.Outcome(summary="worker-B's live write"),
                   agent_name="worker-B")
    row = fix.state(lead_id)
    say.beat(f"  worker-B completes                 -> \033[32mOK\033[0m  "
             f"state={row['state']}  token cleared={row['lease_token'] is None}")


def act_two(fix: Fixture, say: Narrator) -> None:
    """The same race with one name, which is the race migration 013 was written for."""
    say.heading("Act 2 — the same race, both workers named `ingest-cli`")

    name = "ingest-cli"
    say.beat("  `ingest.py --worker` defaults to `ingest-cli`. two terminals, one name.")
    say.beat("  the old fence read: WHERE owner_agent = 'ingest-cli' "
             "AND lease_expires_at > now()")
    say.beat("  — which the *stale* worker satisfies, on the *live* worker's lease.")

    lead_id = fix.lead()
    first = fleet.claim(fix.a, fix.tenant, name, kinds=[fix.kind])[0]
    say.beat(f"  terminal 1 claims {short(lead_id)}       owner={name}  "
             f"token={short(first['lease_token'])}")

    fix.expire(lead_id)
    say.beat("  terminal 1 is still fetching…      its lease runs out")

    second = fleet.claim(fix.b, fix.tenant, name, kinds=[fix.kind])[0]
    say.beat(f"  terminal 2 claims {short(lead_id)}       owner={name}  "
             f"token={short(second['lease_token'])}")

    # Read `owner_agent` back off the row rather than off the claim: `claim`'s RETURNING
    # list does not include it, and a demo that compared two `None`s and announced them
    # identical would be telling the truth by accident.
    say.beat(f"  owner_agent on the row:  {fix.state(lead_id)['owner_agent']!r}  "
             f"-> \033[33mthe old fence sees one worker, not two\033[0m")
    say.beat(f"  lease_token:  {short(first['lease_token'])} vs "
             f"{short(second['lease_token'])}  -> \033[32mtwo claims, two tokens\033[0m")

    try:
        fleet.complete(fix.a, first, fleet.Outcome(summary="terminal 1's stale write"),
                       agent_name=name)
        say.beat("  terminal 1 completes               -> \033[31mOK — THE FENCE FAILED\033[0m")
        raise SystemExit("two drains under one name both passed the fence")
    except fleet.LeaseLost:
        say.beat("  terminal 1 tries to complete       -> \033[31mREFUSED: lease lost\033[0m")

    fleet.complete(fix.b, second, fleet.Outcome(summary="terminal 2's live write"),
                   agent_name=name)
    row = fix.state(lead_id)
    say.beat(f"  terminal 2 completes               -> \033[32mOK\033[0m  state={row['state']}")
    say.beat("  one lead, one completion. the token is the identity, not the name.")


def act_three(fix: Fixture, say: Narrator) -> None:
    """The token as a capability: a live lease, the right name, and a made-up token."""
    say.heading("Act 3 — the token cannot be asserted, only held")

    lead_id = fix.lead()
    claimed = dict(fleet.claim(fix.a, fix.tenant, "worker-A", kinds=[fix.kind])[0])
    say.beat(f"  worker-A holds a live lease        token={short(claimed['lease_token'])}")

    forged = str(uuid.uuid4())
    claimed["lease_token"] = forged
    say.beat(f"  it writes with a made-up token     token={short(forged)}  "
             "(lease live, name correct)")
    try:
        fleet.complete(fix.a, claimed, fleet.Outcome(), agent_name="worker-A")
        say.beat("  complete                           -> \033[31mOK — THE FENCE FAILED\033[0m")
        raise SystemExit("a forged token completed a lead")
    except fleet.LeaseLost:
        say.beat("  complete                           -> \033[31mREFUSED: lease lost\033[0m")

    del claimed["lease_token"]
    say.beat("  it writes with no token at all")
    try:
        fleet.complete(fix.a, claimed, fleet.Outcome(), agent_name="worker-A")
        say.beat("  complete                           -> \033[31mOK — THE FENCE FAILED\033[0m")
        raise SystemExit("a lead with no token completed")
    except ValueError as exc:
        say.beat(f"  complete                           -> \033[31mValueError\033[0m — "
                 "no fallback, by design")
        say.beat(f"     {exc}")

    say.beat(f"  the lead is still pending          state={fix.state(lead_id)['state']}")


ACTS = {1: act_one, 2: act_two, 3: act_three}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch CockroachDB lease-token fencing refuse a stale claim.",
        epilog="Creates and deletes its own fixture leads under a per-run kind. "
               "Touches no real lead, and is safe to run repeatedly.")
    parser.add_argument("--slow", action="store_true",
                        help=f"pause {BEAT}s between beats, for a screen recording")
    parser.add_argument("--act", type=int, choices=sorted(ACTS), action="append",
                        help="run only this act; repeatable. Default is all three.")
    parser.add_argument("--tenant", default=TENANT_SLUG, help="tenant slug")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        return int(bool(sys.stderr.write(
            "DATABASE_URL is not set — try: set -a && . ../../../.env && set +a\n")))

    say = Narrator(args.slow)
    say.beat("\033[1mCockroachDB lease-token fencing\033[0m — "
             "an agent cannot act on work whose claim it no longer holds")
    say.beat("schema/013_lease_token.sql · spindle/fleet.py · "
             f"lease = {fleet.LEASE_SECONDS}s")

    fix = Fixture(url, args.tenant)
    say.beat(f"tenant={args.tenant}  fixture kind={fix.kind}  "
             "(no agent handles this kind — real leads are untouchable from here)")
    try:
        for number in (args.act or sorted(ACTS)):
            ACTS[number](fix, say)
        say.beat()
        say.beat("\033[32mEvery stale claim was refused. Every live claim went "
                 "through.\033[0m")
    finally:
        fix.close()
        say.beat(f"cleaned up: every lead of kind {fix.kind} deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
