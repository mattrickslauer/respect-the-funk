#!/usr/bin/env python3
"""Seed the decision ledger with a spread of real, replayable decisions.

    python apps/spindle/bin/seed_decisions.py                 # dry run — prints, writes nothing
    python apps/spindle/bin/seed_decisions.py --apply         # actually writes
    python apps/spindle/bin/seed_decisions.py --apply --reset # replace this script's own rows

`035_decision_ledger.sql` generalises `025`'s argument: every act somebody can later be
answered about carries the hybrid logical clock it happened at, so the state that
justified it can be read back rather than reconstructed. The landing page's provenance
section reads this table.

## Why a seeding script exists at all, and what it is careful not to do

The ledger fills itself in normal operation — `outreach`, `sender` and the spend gate
each write their own rows. But a demo needs *variety* on a deployment that has, by
design, sent almost nothing, and a public page that shows one kind of decision makes the
narrower claim. So this backfills a representative spread.

Two rules it follows, both of which matter more than the seeding:

  1. **Every `at_hlc` is a real reading**, taken with `cluster_logical_timestamp()` at
     the moment of the INSERT. None is synthesised, offset, or backdated. A fabricated
     coordinate would replay to a state that never existed, which is the single worst
     thing this table can be used for — `025` says so at length and it is the reason
     that file exists. The consequence is honest and worth stating on the page: these
     decisions are stamped when you seed them, so they replay to a recent instant.

  2. **It is idempotent and it owns its rows.** Every row it writes carries
     `inputs->>'seeded_by'` = this script. `--reset` removes exactly those and nothing
     else, so re-running cannot double the ledger and cannot touch a decision the
     system took for real.

## Why dry-run is the default

This writes to whatever `DATABASE_URL` points at, which for this project is a shared
live cluster carrying the deployment's own showcase tenant. Nothing in this repository
mutates that on import or by accident — the changefeed runbook takes the same position
for the same reason — so the write needs a flag, and the flag is not the default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from psycopg.rows import dict_row

#: Marks the rows this script owns, so `--reset` can be exact.
#:
#: A **name, not a path**. It was a path until 2026-08-15 and the repository restructure
#: that afternoon rewrote it — which silently orphaned every row already written under
#: the old string, so `--reset` stopped finding them and re-running would have doubled
#: the ledger. That is precisely the idempotency this script claims, broken by a file
#: move it had no reason to care about. An ownership marker must not encode where its
#: writer happens to live.
SEEDED_BY = "seed_decisions"

#: Markers this script used before the one above. `--reset` clears these too, so rows
#: written under a previous name are still reachable rather than stranded forever. New
#: entries go on the end; nothing is ever removed, because a row written years ago is
#: exactly the one nobody remembers the marker for.
LEGACY_MARKERS: tuple[str, ...] = (
    "platform/bin/seed_decisions.py",
    "apps/spindle/bin/seed_decisions.py",
)


def _showcase_tenant(conn: psycopg.Connection) -> tuple[str, str] | None:
    """The deployment's own tenant — the one the public pages cite.

    Resolved the same way `routes._showcase_tenant_id` resolves it, from the same
    setting, because seeding a different tenant from the one the landing page reads
    would produce a page that is empty for a reason nobody can see.
    """
    slug = os.environ.get("PLATFORM_TENANT_SLUG", "")
    with conn.cursor() as cur:
        if slug:
            cur.execute("SELECT id, slug FROM tenant WHERE slug = %s", (slug,))
        else:
            cur.execute("SELECT id, slug FROM tenant ORDER BY created_at LIMIT 1")
        row = cur.fetchone()
    return (str(row["id"]), row["slug"]) if row else None


def _subjects(conn: psycopg.Connection, tenant_id: str) -> dict[str, object]:
    """Real ids to hang decisions off, so the seeded rows point at real things.

    Anything absent comes back `None` and the decision that needed it is skipped rather
    than pointed at an invented UUID. A ledger row whose subject does not exist is worse
    than a missing ledger row: it looks like evidence.
    """
    out: dict[str, object] = {}
    with conn.cursor() as cur:
        cur.execute("""SELECT id, name FROM party
                        WHERE tenant_id = %s AND party_class = 'roster'
                        ORDER BY created_at LIMIT 1""", (tenant_id,))
        out["artist"] = cur.fetchone()
        cur.execute("""SELECT id, name FROM party
                        WHERE tenant_id = %s AND party_class = 'counterparty'
                          AND contact_state = 'contactable'
                        ORDER BY created_at LIMIT 3""", (tenant_id,))
        out["parties"] = cur.fetchall()
        cur.execute("""SELECT id FROM thread
                        WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 1""",
                    (tenant_id,))
        out["thread"] = cur.fetchone()
    return out


def _plan(tenant_id: str, subs: dict[str, object]) -> list[dict[str, object]]:
    """The decisions to write, as data.

    Each is a question somebody can ask afterwards, and each is a *different shape* of
    answer — that variety is the point of seeding rather than the count:

      shortlist        replays to a ranking
      send             replays to the approval state and the ranking behind it
      budget_increase  replays to the spend as it stood, which is how you prove a cap
                       was raised *before* the spend and not to cover it afterwards
      opt_out          replays to prove the terminal state was set then, and that
                       nothing left the building after it
      suppress         the decision NOT to act, which leaves no other trace anywhere
      lesson           replays to the ranking before it started reranking

    `inputs` carries only what a replay cannot recover — a human's reason, an operator,
    an amount agreed. Never the spend, the ranking or the balance: those are readable at
    `at_hlc`, and a stored copy is a second source of truth that can disagree with the
    first.
    """
    parties = subs.get("parties") or []
    artist = subs.get("artist")
    thread = subs.get("thread")
    rows: list[dict[str, object]] = []

    if artist:
        rows.append({
            "kind": "lesson", "subject_kind": "party", "subject_id": artist["id"],
            "actor": "listener",
            "summary": (f"Daytime rotation rejects tracks over 4 minutes; "
                        f"down-ranking long edits for {artist['name']}"),
            "inputs": {"heard_from": 3, "channel": "radio",
                       "stated": "too long for daytime rotation"},
        })
    if thread:
        rows.append({
            "kind": "send", "subject_kind": "thread", "subject_id": thread["id"],
            "actor": "sender",
            "summary": "Approved and sent — one message, fenced on its idempotency key",
            "inputs": {"approved_by": "human", "channel": "email"},
        })
    if len(parties) > 0:
        rows.append({
            "kind": "opt_out", "subject_kind": "party", "subject_id": parties[0]["id"],
            "actor": "inbox",
            "summary": (f"{parties[0]['name']} asked us to stop. Terminal — no discovery "
                        f"stage can move this back"),
            "inputs": {"requested_via": "reply", "honoured": "immediately"},
        })
    if len(parties) > 1:
        rows.append({
            "kind": "suppress", "subject_kind": "party", "subject_id": parties[1]["id"],
            "actor": "scout",
            "summary": (f"Ranked inside the shortlist but not contacted — "
                        f"{parties[1]['name']} has no verified address"),
            "inputs": {"reason": "no measured contact route",
                       "note": "a guessed address is refused outright"},
        })
    # The attended path, as two rows. A reader who sees only single-row decisions never
    # learns that the row count is what distinguishes unattended from attended, so the
    # seed carries one proposal-and-resolution pair deliberately.
    rows.append({
        "kind": "budget_increase", "stage": "proposed", "subject_kind": "tenant",
        "subject_id": None, "actor": "planner",
        "summary": "Asked for the conversation cap to go 50 → 250; over the unattended ceiling",
        "inputs": {"from": 50, "to": 250,
                   "check": {"total_usd": 412.5, "unvalued": 3}},
    })
    rows.append({
        "kind": "budget_increase", "stage": "applied", "subject_kind": "tenant",
        "subject_id": None, "actor": "human",
        "summary": "Monthly conversation cap raised 50 → 250 on moving to Roster",
        "inputs": {"from": 50, "to": 250, "agreed": "on a call"},
    })
    rows.append({
        "kind": "budget_increase", "stage": "refused", "subject_kind": "tenant",
        "subject_id": None, "actor": "human",
        "summary": "Declined a second cap raise this month — 250 → 400 not agreed",
        "inputs": {"from": 250, "to": 400, "reason": "wait for next cycle"},
    })
    rows.append({
        "kind": "plan_change", "subject_kind": "tenant", "subject_id": None,
        "actor": "human",
        "summary": "Tier moved Label → Roster",
        "inputs": {"from": "label", "to": "roster"},
    })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it this prints the plan and exits.")
    ap.add_argument("--reset", action="store_true",
                    help="first remove the rows this script previously wrote")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.stderr.write("DATABASE_URL is not set — try: set -a && . .env && set +a\n")
        return 2

    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        found = _showcase_tenant(conn)
        if found is None:
            sys.stderr.write("no tenant in this cluster — nothing to seed against\n")
            return 1
        tenant_id, slug = found
        subs = _subjects(conn, tenant_id)
        rows = _plan(tenant_id, subs)

        print(f"tenant   {slug} ({tenant_id})")
        missing = [k for k in ("artist", "thread") if not subs.get(k)]
        if missing:
            print(f"absent   {', '.join(missing)} — the decisions needing them are skipped")
        print(f"planned  {len(rows)} decisions\n")
        for r in rows:
            print(f"  {r['kind']:<16} {r.get('stage', 'applied'):<9} {r['summary']}")

        if not args.apply:
            print("\ndry run — nothing written. Re-run with --apply to write.")
            return 0

        with conn.cursor() as cur:
            if args.reset:
                cur.execute(
                    """DELETE FROM decision
                        WHERE tenant_id = %s AND inputs->>'seeded_by' = ANY(%s)""",
                    (tenant_id, [SEEDED_BY, *LEGACY_MARKERS]))
                print(f"\nremoved {cur.rowcount} previously seeded row(s)")

            written = 0
            for r in rows:
                inputs = dict(r["inputs"])          # type: ignore[arg-type]
                inputs["seeded_by"] = SEEDED_BY
                # `cluster_logical_timestamp()` is evaluated by the cluster inside this
                # statement, so the coordinate is the instant of this very write. It is
                # never computed here and never adjusted.
                cur.execute(
                    """INSERT INTO decision
                         (tenant_id, kind, stage, at_hlc, subject_kind, subject_id,
                          actor, summary, inputs)
                       VALUES (%s, %s, %s, cluster_logical_timestamp(), %s, %s, %s, %s, %s)""",
                    (tenant_id, r["kind"], r.get("stage", "applied"),
                     r["subject_kind"], r["subject_id"],
                     r["actor"], r["summary"], json.dumps(inputs)))
                written += 1
        print(f"\nwrote {written} decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
