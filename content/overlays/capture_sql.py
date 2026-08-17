#!/usr/bin/env python3
"""Execute the claims the voiceover makes, against the live cluster, and keep what came back.

    python3 capture_sql.py                 # every proof -> out/screen/proofs.json
    python3 capture_sql.py replay bus      # just those
    python3 capture_sql.py --clean         # remove the demo rows this wrote

The film says a series of specific things about CockroachDB. This runs each of them
as SQL against `$DATABASE_URL` and records the **real output**, so the terminal the
viewer sees on screen is a transcript and not a prop. Nothing here is hand-written:
if a proof stops holding, the panel it feeds changes or the script exits non-zero.

## Why the capture is separate from the render

`screen-layer.html` draws these; it never touches the database. Splitting them means
the panel can be re-typeset, re-timed and re-positioned all night without re-querying
production, and a re-query is a deliberate act rather than a side effect of adjusting
a font size. `proofs.json` is the seam, and it is committed, so an edit made on a
plane still renders the real numbers.

## Two proofs the video claims that this file deliberately does NOT produce

**Residency.** `SHOW REGIONS` on production returns one region. The three-region
demonstration was done on the throwaway cluster that `docs/runbooks/multiregion.md`
§9 deletes on purpose, and `docs/evidence/` does not exist, so there is no transcript
to show either. That beat keeps its animation and gets no terminal. A panel showing a
single-region `SHOW REGIONS` under a line about Ireland would actively disprove the
narration.

**A running changefeed job.** `SHOW CHANGEFEED JOBS` returns zero rows — the product
opens changefeeds per run rather than leaving a job resident. So `bus` below opens a
real *sinkless* changefeed and writes into it while it is listening, which shows the
mechanism working rather than asserting that a job exists.

## The GC window is load-bearing

`replay` cannot be filmed against the decisions already in the ledger. The cluster runs
`gc.ttlseconds = 4500`, so any timestamp older than 75 minutes is past the GC threshold
and `AS OF SYSTEM TIME` fails outright:

    batch timestamp … must be after replica GC threshold

That is why `replay` writes, records an instant, overwrites and reads back **inside one
run**. Splitting it across two invocations reintroduces exactly the failure it is meant
to demonstrate the absence of. Re-run the whole proof rather than half of it.

## Everything written here is marked and reversible

Writes land on production, because a proof against a scratch database proves nothing
about the system of record. They are confined to `party_fact` and `decision`, carry
`provenance='demo'` and `written_by='capture_sql'`, and attach to **no party** —
`party_id` stays NULL so no row can be read as a claim about a real company. `--clean`
deletes exactly what the marker matches.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Committed, unlike `out/`, which is gitignored and a few gigabytes. A transcript of
# what production answered is evidence and belongs in the tree; re-rendering the film
# on a machine that cannot reach the cluster must still draw the real numbers.
PROOFS_JSON = HERE / "screen" / "proofs.json"
OUT = HERE / "out" / "screen"

TENANT = "1f9e6dd3-ef33-4962-bc63-f0f535c24996"   # Respect the Funk

MARK = "capture_sql"          # written_by on every row this file creates

# The panel is ~600px wide on a 1920 frame. Anything wider than this wraps, and a
# wrapped SQL keyword reads as a typo to anyone who knows SQL.
WIDTH = 58


class ProofFailed(RuntimeError):
    pass


def db_url() -> str:
    u = os.environ.get("DATABASE_URL")
    if not u:
        raise SystemExit(
            "DATABASE_URL is not set. `set -a; . ./.env; set +a` from the repo root.")
    return u


def psql(sql: str, *, url: str, timeout: int = 120) -> str:
    """Run one statement and return psql's own rendering of the result.

    Deliberately the default aligned output rather than `-t -A`: the viewer is meant
    to recognise this as a psql session, and the column rules are most of what makes
    it legible as one.
    """
    p = subprocess.run(
        ["psql", url, "-X", "-P", "pager=off", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise ProofFailed(f"{sql.strip().splitlines()[0]} …\n{p.stderr.strip()}")
    return p.stdout.rstrip("\n")


def psql_file(body: str, *, url: str, timeout: int = 180) -> str:
    """Run a script through `-f`, which is the only form that expands `:vars`.

    `-c` does not do variable interpolation — a fact this file learned the slow way
    while trying to pass a 1024-dimension vector into an EXPLAIN.
    """
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        p = subprocess.run(
            ["psql", url, "-X", "-P", "pager=off", "-v", "ON_ERROR_STOP=1", "-f", path],
            capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise ProofFailed(p.stderr.strip())
        return p.stdout.rstrip("\n")
    finally:
        os.unlink(path)


def scalar(sql: str, *, url: str) -> str:
    p = subprocess.run(["psql", url, "-X", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise ProofFailed(f"{sql}\n{p.stderr.strip()}")
    return p.stdout.strip()


def dedent_sql(s: str) -> str:
    lines = [l.rstrip() for l in s.strip("\n").split("\n")]
    return "\n".join(lines)


def step(sql: str, out: str, *, note: str | None = None) -> dict:
    return {"sql": dedent_sql(sql), "out": out, "note": note}


def too_wide(steps: list[dict]) -> list[str]:
    bad = []
    for st in steps:
        for line in st["sql"].split("\n"):
            if len(line) > WIDTH:
                bad.append(f"  sql {len(line):>3} > {WIDTH}: {line}")
    return bad


# ---------------------------------------------------------------------------
# the proofs
# ---------------------------------------------------------------------------

def proof_memory(url: str) -> dict:
    """What the agents have accumulated. The 'this is not a toy' panel."""
    sql = """SELECT
  (SELECT count(*) FROM party)          AS counterparties,
  (SELECT count(*) FROM party_fact)     AS facts,
  (SELECT count(*) FROM party_chunk
     WHERE embedding IS NOT NULL)       AS embedded,
  (SELECT count(*) FROM agent_run)      AS agent_runs;"""
    return {
        "slug": "memory",
        "caption": "the memory, right now",
        "steps": [step(sql, psql(sql, url=url))],
    }


def proof_prefix(url: str) -> dict:
    """'And that index starts with tenant_id.' — shown, not asserted."""
    sql = """SELECT seq_in_index, column_name
  FROM [SHOW INDEXES FROM party_chunk]
 WHERE index_name = 'chunk_semantic'
 ORDER BY seq_in_index;"""
    return {
        "slug": "prefix",
        "caption": "one column, two jobs",
        "steps": [step(sql, psql(sql, url=url))],
    }


def proof_search(url: str) -> dict:
    """'the filters live inside the index itself, not bolted on after'

    The whole claim rests on EXPLAIN printing a `vector search` node whose prefix
    spans are pinned to the tenant and the model. If the planner ever degrades this
    to a filter over a scan, this proof fails loudly instead of quietly filming a
    lie.
    """
    model = scalar("SELECT model FROM party_chunk WHERE embedding IS NOT NULL LIMIT 1;",
                   url=url)
    vec = scalar("SELECT embedding FROM party_chunk WHERE embedding IS NOT NULL LIMIT 1;",
                 url=url)
    vecfile = OUT / "probe-vector.txt"
    vecfile.write_text(vec)

    shown = """EXPLAIN
SELECT id, document_id, ordinal
  FROM party_chunk
 WHERE tenant_id = :tenant
   AND model     = :model
 ORDER BY embedding <=> :query_vector
 LIMIT 10;"""

    body = f"""\\set tenant '{TENANT}'
\\set model '{model}'
\\set qv `cat {vecfile}`
EXPLAIN
SELECT id, document_id, ordinal
  FROM party_chunk
 WHERE tenant_id = :'tenant'
   AND model     = :'model'
 ORDER BY embedding <=> :'qv'::VECTOR(1024)
 LIMIT 10;
"""
    raw = psql_file(body, url=url)
    if "vector search" not in raw:
        raise ProofFailed(
            "EXPLAIN did not plan a vector search — the index is not serving the "
            f"filters, which is the claim this panel exists to make.\n{raw}")

    # Keep the part that carries the claim. The lookup-join and top-k nodes above it
    # are true and irrelevant, and at this panel width they push the spans off screen.
    keep, seen = [], False
    for line in raw.split("\n"):
        if "vector search" in line:
            seen = True
        # psql's trailing "(N rows)" counts the whole plan, including the nodes
        # trimmed above it. Kept, it would be a number that contradicts what is on
        # screen.
        if seen and not re.match(r"^\(\d+ rows?\)$", line.strip()):
            keep.append(line.replace("        ", "  ").rstrip())
    trimmed = "\n".join(keep).replace(TENANT, "<tenant>").replace(model, "<model>")

    return {
        "slug": "search",
        "caption": "the filters are inside the index",
        "steps": [step(shown, trimmed,
                       note="prefix spans pinned to tenant + model")],
    }


def proof_bus(url: str) -> dict:
    """'a changefeed on the memory tables wakes the next one'

    A sinkless changefeed streams into the SQL session, so this can show a write
    landing on the bus in one shot: open the feed, write a fact, watch the row come
    back out. That is the actual mechanism, and it is more honest than `SHOW
    CHANGEFEED JOBS` — which returns nothing here, because feeds are opened per run.
    """
    shown_feed = """CREATE CHANGEFEED FOR TABLE party_fact
  WITH format = 'json', envelope = 'wrapped';"""

    fact_id = str(uuid.uuid4())
    # The uuid gets a line of its own: at 58 columns it does not fit beside
    # `:tenant`, and capture_sql refuses to record a statement the panel would wrap.
    insert = f"""INSERT INTO party_fact
  (id, tenant_id, dimension, value_text,
   provenance, source, written_by, model, status)
VALUES
  ('{fact_id}',
   :tenant, 'demo.capture',
   'a fact an agent just learned',
   'asserted', 'capture_sql.py', '{MARK}',
   'none', 'live');"""

    # THIS CANNOT BE DONE WITH psql, and it is worth writing down because the
    # failure looks exactly like the feature being broken.
    #
    # psql collects an ENTIRE result set before printing the first row. A sinkless
    # changefeed is a result set that never ends, so psql prints nothing, forever —
    # zero bytes on stdout and nothing on stderr, whatever you do to its buffering.
    # `initial_scan='only'` appears to work only because that query terminates. An
    # hour went into `stdbuf` before this was clear, and stdbuf is the wrong layer:
    # the buffering that matters is libpq's, not stdio's.
    #
    # psycopg streams, so the row arrives when the database emits it.
    import psycopg

    got: dict[str, object] = {}
    needle = fact_id.encode()
    conn = psycopg.connect(url, autocommit=True)

    def reader() -> None:
        try:
            with conn.cursor() as cur:
                # initial_scan='no' because party_fact holds 111,212 rows and a
                # backfill would still be emitting last week's writes long after the
                # deadline, with the row this proof waits for somewhere behind them.
                for rec in cur.stream(
                        "CREATE CHANGEFEED FOR TABLE party_fact "
                        "WITH format='json', envelope='wrapped', "
                        "initial_scan='no'"):
                    if needle in bytes(rec[2]):
                        got["rec"] = rec
                        return
        except Exception as e:                    # the cancel below lands here
            got.setdefault("err", e)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        time.sleep(8)                      # let the rangefeed reach steady state
        psql(insert.replace(":tenant", f"'{TENANT}'"), url=url)
        th.join(timeout=75)
        if "rec" not in got:
            raise ProofFailed(
                "the changefeed did not emit the row that was just written; the bus "
                "panel would be showing an empty terminal under a line about a bus")
    finally:
        try:
            conn.cancel()
            conn.close()
        except Exception:
            pass

    table, _, value = got["rec"]                  # type: ignore[misc]
    doc = json.loads(bytes(value).decode("utf-8"))
    after = doc.get("after", {})
    # The full envelope is eighteen columns of uuids. Show only the fields a viewer
    # can check against the sentence being spoken.
    slim = {k: after[k] for k in
            ("dimension", "value_text", "provenance", "written_by")
            if after.get(k) is not None}
    pretty = f"{table}\n" + json.dumps({"after": slim}, indent=1)

    return {
        "slug": "bus",
        "caption": "the write wakes the next agent",
        "steps": [
            step(shown_feed, "", note="sinkless — the feed streams into this session"),
            step(insert, "INSERT 0 1", note="an agent stores what it learned"),
            step("-- the feed emits it, with no orchestrator", pretty),
        ],
    }


def proof_replay(url: str) -> dict:
    """'Four extra words of SQL, pointed at that timestamp, and they come back.'

    The full cycle, live, in one run — see the GC note in the module docstring for
    why it cannot be split. Store a fact, record the instant in the ledger the way
    the product does, overwrite the fact, then read the overwritten value back.
    """
    fact_id, dec_id = str(uuid.uuid4()), str(uuid.uuid4())

    write = f"""INSERT INTO party_fact
  (id, tenant_id, dimension, value_text,
   provenance, source, written_by, model, status)
VALUES
  ('{fact_id}', '{TENANT}', 'demo.replay',
   'weekly rotation, 2 spins',
   'asserted', 'capture_sql.py', '{MARK}',
   'none', 'live');"""
    psql(write, url=url)

    # The ledger row is a coordinate, not a copy — 035_decision_ledger.sql is
    # emphatic about this, and the replay below is the reason it can be.
    ledger = f"""INSERT INTO decision
  (tenant_id, id, kind, stage, at_hlc, at_wall,
   subject_kind, actor, summary, inputs)
VALUES
  ('{TENANT}', '{dec_id}', 'budget_increase',
   'applied', cluster_logical_timestamp(), now(),
   'tenant', 'agent',
   'Put more behind this record',
   '{{"written_by": "{MARK}"}}'::JSONB);"""
    psql(ledger, url=url)

    at_hlc = scalar(f"SELECT at_hlc FROM decision WHERE id = '{dec_id}';", url=url)

    # "We never stop collecting, so those metrics have been overwritten."
    psql(f"UPDATE party_fact SET value_text = 'weekly rotation, 9 spins' "
         f"WHERE id = '{fact_id}';", url=url)

    now_sql = f"""SELECT value_text
  FROM party_fact
 WHERE id = '{fact_id[:8]}…';"""
    now_out = psql(f"SELECT value_text FROM party_fact WHERE id = '{fact_id}';", url=url)

    ledger_sql = """SELECT at_hlc, summary
  FROM decision
 WHERE kind = 'budget_increase'
 ORDER BY at_wall DESC LIMIT 1;"""
    ledger_out = psql(
        f"SELECT at_hlc, summary FROM decision WHERE id = '{dec_id}';", url=url)

    back_sql = f"""SELECT value_text
  FROM party_fact
  AS OF SYSTEM TIME '{at_hlc}'
 WHERE id = '{fact_id[:8]}…';"""
    back_out = psql(
        f"SELECT value_text FROM party_fact AS OF SYSTEM TIME '{at_hlc}' "
        f"WHERE id = '{fact_id}';", url=url)

    if "9 spins" not in now_out or "2 spins" not in back_out:
        raise ProofFailed(
            "the replay did not return the superseded value — this is the one panel "
            f"the film cannot fake.\nnow: {now_out}\nthen: {back_out}")

    return {
        "slug": "replay",
        "caption": "pointed at the instant it decided",
        "steps": [
            step(ledger_sql, ledger_out, note="the ledger stored a coordinate"),
            step(now_sql, now_out, note="the metric has moved since"),
            step(back_sql, back_out, note="four extra words — not a copy of them"),
        ],
    }


PROOFS = {
    "memory": proof_memory,
    "prefix": proof_prefix,
    "search": proof_search,
    "bus": proof_bus,
    "replay": proof_replay,
}


def clean(url: str) -> int:
    a = psql(f"DELETE FROM party_fact WHERE written_by = '{MARK}';", url=url)
    b = psql(f"DELETE FROM decision WHERE inputs->>'written_by' = '{MARK}';", url=url)
    print(f"  party_fact: {a}\n  decision:   {b}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="*", help="proof slugs; default is all of them")
    ap.add_argument("--clean", action="store_true",
                    help="delete the demo rows this script wrote, then exit")
    args = ap.parse_args()

    url = db_url()
    OUT.mkdir(parents=True, exist_ok=True)
    PROOFS_JSON.parent.mkdir(parents=True, exist_ok=True)
    if args.clean:
        return clean(url)

    wanted = args.only or list(PROOFS)
    unknown = [w for w in wanted if w not in PROOFS]
    if unknown:
        print(f"unknown proof(s): {', '.join(unknown)}\n"
              f"available: {', '.join(PROOFS)}", file=sys.stderr)
        return 2

    existing = {}
    if PROOFS_JSON.exists():
        existing = {p["slug"]: p for p in json.loads(PROOFS_JSON.read_text())["proofs"]}

    failures = []
    for slug in wanted:
        print(f"  {slug} …", end=" ", flush=True)
        try:
            p = PROOFS[slug](url)
        except (ProofFailed, subprocess.TimeoutExpired) as e:
            print("FAILED")
            failures.append((slug, str(e)))
            continue
        wide = too_wide(p["steps"])
        if wide:
            print("TOO WIDE")
            failures.append((slug, "SQL wider than the panel:\n" + "\n".join(wide)))
            continue
        existing[slug] = p
        print(f"ok · {sum(len(s['out'].splitlines()) for s in p['steps'])} lines back")

    order = [s for s in PROOFS if s in existing]
    PROOFS_JSON.write_text(json.dumps(
        {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "cluster": re.sub(r"//[^@]*@", "//", url).split("?")[0],
         "proofs": [existing[s] for s in order]}, indent=2) + "\n")
    print(f"\n{PROOFS_JSON.relative_to(HERE)} · {len(order)} proofs")

    if failures:
        print("\nFAILED:", file=sys.stderr)
        for slug, why in failures:
            print(f"\n  {slug}\n    " + why.replace("\n", "\n    "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
