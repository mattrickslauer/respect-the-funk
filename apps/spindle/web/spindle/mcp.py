"""Asking the catalogue a question in English, answered through CockroachDB's Cloud
Managed MCP Server.

## What this is, and the claim it is careful not to make

The hackathon lists the Cloud Managed MCP Server as one of four CockroachDB tools. This
project's two-of-four is already satisfied by Distributed Vector Indexing and the
`ccloud` CLI, so **this module is upside, not a dependency**, and that fact is what
licenses its narrowness. It does one thing: an operator types a question, and the answer
comes back having been executed by the managed MCP server rather than by our own psycopg
connection.

`.mcp.json` at the repo root points Claude Code at the same endpoint. That is developer
tooling and is not this. This is the application reaching the server itself, over HTTP,
with a credential of its own.

## The design decision this file exists to defend: the LLM does not write SQL

The obvious build is natural-language-to-SQL: hand the model the schema, let it emit a
`SELECT`, forward that to `select_query`. It was rejected, and the reason is not taste.

`select_query` executes whatever string it is given against the cluster with the API
key's privileges — measured 2026-08-13, a bare `SELECT tenant_id, count(*) FROM party
GROUP BY tenant_id` returns every tenant's rows, and the sibling `insert_rows`,
`create_table` and `create_database` tools are writes. So the endpoint has no tenant
concept at all; whatever isolation exists has to exist on this side of the wire.

Worse, it would exist somewhere nothing in this repo can see. `test_tenant_scoping.py`
is this project's strongest guarantee, and it works by statically resolving the SQL text
passed to `cursor.execute(...)` in `spindle/*.py`. A statement composed by a model
at request time and shipped over HTTPS is invisible to it — not allowed by it, not
forbidden by it, simply outside it. A model-authored `SELECT * FROM party` would leak
every tenant and the suite would stay green, because the lint would never have been shown
the statement. **That is the hole, and it is the reason the model is not permitted to
author the statement.**

So the split is:

  * **The model classifies.** Given the operator's English and the catalogue of questions
    below, it returns a question key and typed slot values as JSON, or a refusal. That is
    a bounded task whose worst failure is picking the wrong question — visible to the
    operator, who is shown the SQL — rather than an unbounded one whose worst failure is
    reading somebody else's rows.
  * **This file authors every statement.** Every SQL string is a literal in `QUESTIONS`,
    reviewable in a diff, and pins `tenant_id` to the tenant the console resolved.

`_refuse_if_unscoped` then re-proves that on the rendered text before it goes anywhere.
It is deliberately a guard over our *own* output rather than a sandbox for hostile input:
its job is to fail a test when a future edit drops a predicate from a template, which is
the realistic way this breaks. Containment of hostile SQL is not achieved by the guard,
it is achieved by no model-authored SQL ever reaching the wire.

## Tenant isolation, concluded explicitly

There is one tenant row on this cluster today (`respect-the-funk`, measured 2026-08-13),
so nothing here is currently leaking anything to anyone. That is a fact about the data
and not about the code, and it is exactly the kind of fact that stops being true quietly.
The three rules that survive a second tenant:

1. Every statement carries `tenant_id = '<uuid>'`, and the uuid is the one `routes.py`
   resolved from `PLATFORM_TENANT_SLUG` — never a value that came from the operator or
   the model.
2. Every UUID-shaped literal in the rendered statement must *be* that uuid. A template
   that pinned to a different tenant would have to smuggle its id past this, and there is
   nowhere to put it.
3. Only `select_query` is ever called. `insert_rows`, `create_table` and
   `create_database` exist on the server and are never named here; the statement is also
   checked to be a single read before it is sent, so a template that grew a write would
   be refused rather than executed.

## What one question costs

One `gpt-4o-mini` classification per question, and nothing else. Measured against the
live API on 2026-08-13 over four questions: **$0.000049 to $0.000055 each — about one
twenty-thousandth of a dollar**, roughly 300 prompt tokens (the catalogue is in the
prompt) and under 20 completion tokens at $0.15/$0.60 per Mtok. Ten thousand questions
would cost fifty cents. It goes through `spend.Gate` under the existing
`openai:gpt-4o-mini` rate-card key like everything else metered, so `RTF_PAID_ENABLED=0`
turns this page off rather than making it silently free. The MCP call itself is not
metered by Cockroach beyond the request units the query would have cost anyway.

The classifier is OpenAI rather than Bedrock because Bedrock is not reachable from this
account: `spend.py`'s rate card records both Claude and Titan on-demand quotas at 0 RPM,
measured. `OPENAI_API_KEY` is a live entitlement, verified 2026-08-13.

## Why the handshake happens on every question

Streamable-HTTP MCP is a session protocol: `initialize`, then an `initialized`
notification, then calls, all carrying `mcp-session-id`. This client performs all three
per question and keeps nothing. The console runs in Lambda, where a cached session would
be cached per warm container and expire invisibly between invocations — three round trips
that always work beat one that works until it doesn't and then fails in a way nobody can
reproduce.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from spindle import settings as settings_mod, spend

#: The model that classifies. Named against the rate-card key rather than a bare model
#: string so the two cannot drift: a model this file could call but `spend.py` has not
#: priced is one `estimate()` refuses, which is the safe direction and also the confusing
#: one if the names differ for no reason.
ROUTER_MODEL = "gpt-4o-mini"
ROUTER_RATE_KEY = "openai:gpt-4o-mini"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

#: Ceiling on what the classifier may write back. A key and two slots is a few dozen
#: tokens; anything approaching this is a model that has started explaining itself, and
#: truncating it makes the JSON unparseable, which raises. That is the intended outcome.
ROUTER_MAX_TOKENS = 120

#: Seconds. Generous because a CockroachDB Basic cluster scales to zero and the first
#: query after an idle period pays for the resume.
MCP_TIMEOUT = 60
ROUTER_TIMEOUT = 30

MCP_PROTOCOL_VERSION = "2025-06-18"


class McpNotConfigured(RuntimeError):
    """A credential or an identifier this page cannot run without is missing.

    Separate from `McpRefused` because the two need different things from a human: this
    one is answered by setting an environment variable, that one by looking at why the
    server said no.
    """


class McpRefused(RuntimeError):
    """The managed MCP server did not answer, or answered with something unreadable."""


class QuestionRefused(RuntimeError):
    """This module declined to ask. Either the classifier could not place the question
    in the catalogue, or the rendered statement failed its own scoping check.

    Refusing is the product here. A console that answers "which stations play jazz" with
    a plausible number derived from a question the operator did not ask is worse than one
    that says it does not know that question, because the second is correctable and the
    first is believed.
    """


# ------------------------------------------------------------------ the catalogue

#: A slot's value is either a bounded integer or a token from a deliberately small
#: alphabet. There is no free-text slot: the alphabet below contains no quote, no
#: backslash and no semicolon, so a rendered `ILIKE '%…%'` literal cannot be terminated
#: from inside. That is a stronger statement than "the value is escaped" — there is
#: nothing to escape — and it is why `_slot_text` refuses rather than strips. A stripped
#: value silently answers a different question from the one that was typed.
_TERM_OK = re.compile(r"^[A-Za-z0-9 .&/-]{1,60}$")

#: `limit` is clamped by refusal, not by `min()`. An operator who asks for the top 5,000
#: stations should be told the ceiling is 200, not handed 200 rows labelled as though
#: they were the 5,000 they asked for.
LIMIT_MAX = 200

#: What a question that takes a limit uses when the operator did not name one — which is
#: most of the time, and is the classifier's *correct* answer to "how many did they ask
#: for?" when they asked for none. Observed 2026-08-13: `gpt-4o-mini` expresses that as an
#: explicit `"limit": null` rather than by omitting the key, so a `dict.get(..., default)`
#: never fires and every unqualified question was refused as "not a whole number".
#:
#: This is a default, and the rule against silent ones is satisfied by visibility rather
#: than by absence: the rendered statement is printed on the page above the rows, so an
#: operator who got 25 of something can see `LIMIT 25` and ask for more. A default nobody
#: can see would be the thing worth refusing.
DEFAULT_LIMIT = 25


@dataclass(frozen=True)
class Question:
    """One thing this console can be asked, and the exact statement that answers it.

    `asks` is written as the operator would say it, because it is what the classifier
    matches against — the model is choosing between English sentences, not guessing at
    identifiers. `note` is shown beside the answer and exists to stop a number being read
    as more than it is; several of these tables are populated by discovery runs that are
    still in progress, and a count from a half-crawled frontier is a real number about an
    incomplete world.
    """

    key: str
    asks: str
    sql: str
    slots: tuple[str, ...] = ()
    note: str = ""


#: Every question this page can answer. Adding one is a code change and a review, which
#: is the point: the set of statements that can reach the cluster is enumerable by
#: reading this list, and that property is what `test_mcp.py` asserts about each of them.
#:
#: Each statement pins `tenant_id` to `{tenant}`. Joined tables carry the predicate in
#: their own `ON` clause, propagated by equality from the pinned relation — the same
#: idiom `repo.list_parties` uses, and the one `test_tenant_scoping.py` was rewritten to
#: check per-reference rather than per-statement.
QUESTIONS: tuple[Question, ...] = (
    Question(
        key="counterparties_by_role",
        asks="How many counterparties do we know, and what kind are they?",
        sql="""SELECT r.role, count(*) AS how_many
                 FROM party_role r
                WHERE r.tenant_id = '{tenant}'
                GROUP BY r.role
                ORDER BY how_many DESC
                LIMIT 25""",
        note="Roles come from the discovery stages; a party can hold more than one, so "
             "these do not sum to the number of parties.",
    ),
    Question(
        key="genre_spread",
        asks="What genres do the stations we know actually play?",
        sql="""SELECT f.value_text AS genre, count(*) AS stations
                 FROM party_fact f
                WHERE f.tenant_id = '{tenant}'
                  AND f.dimension = 'genre'
                  AND f.status = 'live'
                GROUP BY f.value_text
                ORDER BY stations DESC
                LIMIT {limit}""",
        slots=("limit",),
        note="Genre is `asserted` — a Radio Browser contributor typed it. It is not a "
             "measurement of what the station broadcast, and `radiobrowser.py` explains "
             "why it is still worth having.",
    ),
    Question(
        key="stations_playing",
        asks="Which stations play a particular genre, such as jazz or hip-hop?",
        sql="""SELECT p.name AS station, f.value_text AS genre
                 FROM party_fact f
                 JOIN party p
                   ON p.tenant_id = f.tenant_id
                  AND p.id = f.party_id
                WHERE f.tenant_id = '{tenant}'
                  AND f.dimension = 'genre'
                  AND f.status = 'live'
                  AND f.value_text ILIKE '%{term}%'
                ORDER BY p.name
                LIMIT {limit}""",
        slots=("term", "limit"),
        note="Matched on the tag text, so 'jazz' also finds 'smooth jazz' and "
             "'jazz fusion'. That is usually what was meant and it is worth knowing it "
             "is what happened.",
    ),
    Question(
        key="discovery_queue",
        asks="What is the discovery queue doing — how much work is pending or failed?",
        sql="""SELECT l.state, l.kind, count(*) AS leads
                 FROM lead l
                WHERE l.tenant_id = '{tenant}'
                GROUP BY l.state, l.kind
                ORDER BY leads DESC
                LIMIT {limit}""",
        slots=("limit",),
        note="`pending` is work the fleet has not reached yet, not work that is stuck. "
             "`failed` is the parked pile a human is meant to look at.",
    ),
    Question(
        key="catalogue_recordings",
        asks="What recordings are in our catalogue, and do they have an ISRC?",
        sql="""SELECT rec.title,
                      CASE WHEN rec.isrc = '' THEN 'no ISRC' ELSE rec.isrc END AS isrc,
                      rec.status
                 FROM recording rec
                WHERE rec.tenant_id = '{tenant}'
                ORDER BY rec.created_at DESC
                LIMIT {limit}""",
        slots=("limit",),
        note="An empty ISRC is a recording nobody has registered yet, not a data error.",
    ),
    Question(
        key="newest_counterparties",
        asks="Which counterparties did we learn about most recently?",
        sql="""SELECT p.name, p.kind, p.status, p.created_at
                 FROM party p
                WHERE p.tenant_id = '{tenant}'
                ORDER BY p.created_at DESC
                LIMIT {limit}""",
        slots=("limit",),
        note="Ordered by when this system first wrote the row, which is not when the "
             "counterparty came into existence.",
    ),
)

BY_KEY: dict[str, Question] = {q.key: q for q in QUESTIONS}


# ------------------------------------------------------------------- the scoping guard

#: The tables that carry a `tenant_id` column, copied from `test_tenant_scoping.py`'s
#: `TENANT_SCOPED_TABLES`. Duplicated deliberately: that list is a test fixture and
#: importing `tests/` from `spindle/` would invert the dependency, and a runtime
#: guard that silently disappears when the test package is not installed is not a guard.
#: `test_mcp.py` asserts the two lists agree, so the copy cannot drift unnoticed.
TENANT_SCOPED_TABLES: frozenset[str] = frozenset({
    "party", "party_identifier", "party_role", "party_relation", "work", "recording",
    "release", "recording_work", "release_recording", "party_credit", "lead",
    "party_document", "party_chunk", "party_fact", "party_metric", "agent_run",
    "party_budget", "presence", "suggestion", "lesson", "statement_import",
    "campaign", "thread", "message", "outbox",
    "autonomy", "recording_budget", "budget_raise", "decision",
    "contact_route", "recording_asset", "tenant_budget", "sound_usage",
})

_REF_RE = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
_BOUNDARY_RE = re.compile(
    r"\b(JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT|RETURNING|HAVING|FOR\s+UPDATE|"
    r"ON\s+CONFLICT|VALUES)\b", re.I)
_JOIN_ENDERS = {"JOIN", "WHERE", "GROUP BY", "ORDER BY", "LIMIT", "RETURNING", "HAVING",
                "FOR UPDATE", "ON CONFLICT"}
_WHERE_ENDERS = {"GROUP BY", "ORDER BY", "LIMIT", "RETURNING", "HAVING", "FOR UPDATE"}

#: `tenant_id` compared for equality against something, in either direction. Copied in
#: spirit from `test_tenant_scoping._has_tenant_equality`, and for its reason: a zone
#: containing `tenant_id IS NOT NULL` mentions the column and scopes nothing.
_EQ_RE = re.compile(r"\btenant_id\b\s*=(?!=)|(?<![!<>=])=\s*\btenant_id\b")

#: A UUID literal anywhere in the statement. Every one must be the tenant we resolved —
#: see rule 2 in the module docstring.
_UUID_LITERAL_RE = re.compile(
    r"'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'")

#: Anything that would make this statement more than a single read. `SET` is here
#: because `allow_unsafe_internals` is one `SET` away from `crdb_internal`, which the
#: conftest header records as the route to information a console page has no business
#: reading.
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|DROP|ALTER|CREATE|GRANT|REVOKE|TRUNCATE|COPY|"
    r"SET|BEGIN|COMMIT|ROLLBACK|EXPORT|IMPORT|BACKUP|RESTORE|UNION|INTERSECT|"
    r"EXCEPT)\b", re.I)


def _next_boundary(unit: str, start: int, enders: set[str]) -> int:
    for m in _BOUNDARY_RE.finditer(unit, start):
        if " ".join(m.group(1).upper().split()) in enders:
            return m.start()
    return len(unit)


def _zone(unit: str, keyword: str, ref_end: int, where_pos: int | None) -> str:
    """The clause that governs one table reference — its own `ON` for a join, the
    statement's `WHERE` for the primary table. Same split as `test_tenant_scoping._zone`
    and for the same reason: a join must not be able to borrow the primary table's
    `WHERE`, nor the primary table a join's `ON`."""
    if keyword == "JOIN":
        return unit[ref_end:_next_boundary(unit, ref_end, _JOIN_ENDERS)]
    if where_pos is None:
        return ""
    return unit[where_pos:_next_boundary(unit, where_pos, _WHERE_ENDERS)]


def _refuse_if_unscoped(sql: str, tenant_id: str) -> None:
    """Prove the rendered statement is a single read, scoped to exactly this tenant.

    Raises `QuestionRefused` naming what failed. Every check refuses on doubt rather than
    permitting on doubt, which is the only direction that is safe when the thing being
    checked is about to be run against a live cluster by a server with no tenant concept.

    Not a sandbox. See the module docstring: this proves a property of statements *we*
    wrote, so that dropping a predicate from a template in some future edit fails
    `test_mcp.py` instead of shipping. Model-authored SQL never gets here because it is
    never produced.
    """
    text = " ".join(sql.split())

    if "--" in text or "/*" in text:
        raise QuestionRefused(
            "The statement contains a SQL comment. Comments can hide text from the "
            "clause analysis below, so a commented statement is refused rather than "
            "analysed. Remove the comment from the template in `mcp.QUESTIONS`.")

    if text.count(";") or not re.match(r"^\s*SELECT\b", text, re.I):
        raise QuestionRefused(
            "A question may only run a single bare `SELECT`. This statement either "
            "contains a semicolon or does not begin with SELECT, and is refused: "
            f"{text[:200]}")

    forbidden = _FORBIDDEN_RE.search(text)
    if forbidden:
        raise QuestionRefused(
            f"The statement contains `{forbidden.group(1).upper()}`, which this page "
            "will not send. Only single-table and joined reads are permitted; set "
            "operations are excluded too, because a UNION's second branch has its own "
            "FROM whose scoping this analysis cannot attribute correctly.")

    foreign = {u for u in _UUID_LITERAL_RE.findall(text) if u.lower() != tenant_id.lower()}
    if foreign:
        raise QuestionRefused(
            f"The statement names a UUID that is not this tenant: {sorted(foreign)}. "
            f"Every UUID literal must be {tenant_id}, because a statement that can "
            "mention another id is one that can read another tenant.")

    if not _EQ_RE.search(text) or tenant_id.lower() not in text.lower():
        raise QuestionRefused(
            "The statement does not pin `tenant_id` to this tenant at all. Every "
            "question's SQL must carry `tenant_id = '{tenant}'`.")

    where = re.search(r"\bWHERE\b", text, re.I)
    where_pos = where.start() if where else None
    introduced: dict[str, int] = {}
    for m in _REF_RE.finditer(text):
        keyword, table = m.group(1).upper(), m.group(2).lower()
        if table not in TENANT_SCOPED_TABLES:
            continue
        introduced[table] = introduced.get(table, 0) + 1
        if not _EQ_RE.search(_zone(text, keyword, m.end(), where_pos)):
            raise QuestionRefused(
                f"`{keyword} {table}` has no `tenant_id` equality in the clause that "
                f"governs it, so that reference would scan every tenant's rows. "
                f"Statement: {text[:200]}")

    # Every mention of a scoped table must be one the loop above actually checked.
    #
    # The loop only sees tables introduced by `FROM`/`JOIN`/`INTO`/`UPDATE`, and a
    # comma-join — `FROM party p, party_role r WHERE ...` — introduces its second table
    # with no keyword at all. That reference is invisible to `_REF_RE`, so it is neither
    # checked nor reported, which is the one failure mode this guard cannot be allowed to
    # have: silence that reads as approval. Counting mentions against checked references
    # catches it without this file having to parse a `FROM` list.
    #
    # Aliases are what make the count exact rather than approximate. Every statement in
    # `QUESTIONS` refers to its columns through an alias (`p.name`, `f.value_text`), so a
    # table name appears exactly where it is introduced and nowhere else; a template that
    # spelled out `party.name` would trip this and should be rewritten to use an alias
    # rather than have this check loosened. `\b` keeps `party` from matching inside
    # `party_fact` or `f.party_id`.
    # Iterating the whole table list rather than only the tables the loop above happened
    # to introduce. That distinction is the bug this check was first written with: a
    # table reached *only* by comma-join never appears in `introduced` at all, so
    # comparing counts within `introduced` compared nothing and the smuggled reference
    # stayed invisible. `test_a_comma_join_cannot_smuggle_an_unchecked_table_reference`
    # is the test that caught it and the one that keeps it caught.
    for table in TENANT_SCOPED_TABLES:
        mentions = len(re.findall(rf"\b{table}\b", text, re.I))
        checked = introduced.get(table, 0)
        if mentions > checked:
            raise QuestionRefused(
                f"`{table}` is named {mentions} time(s) but only {checked} of those is "
                "introduced by FROM or JOIN, so at least one reference to it was never "
                "checked for scoping. A comma-join is the usual cause; write it as an "
                "explicit JOIN with its own ON clause, and refer to columns by alias.")


# ------------------------------------------------------------------------- rendering

def _slot_int(raw: Any, name: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise QuestionRefused(
            f"The classifier returned {raw!r} for `{name}`, which is not a whole "
            "number. Ask again with a plain count.") from None
    if not 1 <= value <= LIMIT_MAX:
        raise QuestionRefused(
            f"`{name}` must be between 1 and {LIMIT_MAX}; {value} was asked for. "
            "Refused rather than clamped, so the answer is never quietly narrower "
            "than the question.")
    return value


def _slot_text(raw: Any, name: str) -> str:
    value = str(raw or "").strip()
    if not _TERM_OK.match(value):
        raise QuestionRefused(
            f"`{name}` must be 1-60 characters of letters, digits, spaces or "
            f"`. & / -`, and {value!r} is not. It is refused rather than stripped "
            "down to the allowed characters, because a silently altered search term "
            "answers a question nobody asked.")
    return value


def render(question: Question, tenant_id: str, slots: dict[str, Any]) -> str:
    """The statement this question will actually run, with the guard already applied.

    `tenant_id` is validated as a UUID before it is interpolated. It comes from
    `repo.get_tenant`, not from the operator, so this is not defending against an
    attacker — it is making sure the one string that is spliced into every statement in
    this file is the shape it claims to be, on the day somebody changes where it comes
    from.
    """
    try:
        pinned = str(uuid.UUID(str(tenant_id)))
    except (ValueError, AttributeError, TypeError):
        raise QuestionRefused(
            f"{tenant_id!r} is not a UUID, so it will not be interpolated into a "
            "statement. The tenant id must come from `repo.get_tenant`.") from None

    values: dict[str, Any] = {"tenant": pinned}
    for name in question.slots:
        if name == "limit":
            # `None` and absent both mean "the operator named no number", and are the
            # same thing; see `DEFAULT_LIMIT`. A zero or a negative is *not* that — it is
            # a number that was named and is out of range — so it goes to `_slot_int` and
            # is refused there rather than being coerced by an `or`.
            asked = slots.get("limit")
            values["limit"] = _slot_int(
                DEFAULT_LIMIT if asked is None else asked, "limit")
        elif name == "term":
            values["term"] = _slot_text(slots.get("term"), "term")
        else:  # unreachable unless a Question grows a slot nobody taught this function
            raise QuestionRefused(
                f"`{question.key}` declares a slot `{name}` that `render` does not know "
                "how to validate. Teach `render` before adding the slot.")

    sql = " ".join(question.sql.format(**values).split())
    _refuse_if_unscoped(sql, pinned)
    return sql


# --------------------------------------------------------------------- the classifier

def _router_prompt() -> str:
    """The catalogue, as the model sees it.

    Built from `QUESTIONS` rather than written out, so a question added below cannot be
    invisible to the classifier — the failure mode where a page grows a capability that
    nothing can route to.
    """
    lines = [
        "You route an operator's question about a music catalogue to one of the "
        "questions this console can answer. You do not write SQL and you are not "
        "shown any.",
        "",
        "Questions:",
    ]
    for q in QUESTIONS:
        slots = f"  slots: {', '.join(q.slots)}" if q.slots else "  slots: none"
        lines.append(f"- {q.key}: {q.asks}\n{slots}")
    lines += [
        "",
        'Reply with JSON only: {"key": "<one key above>", "limit": <1-200, if the '
        'question takes a limit>, "term": "<the genre or word searched for, if the '
        'question takes a term>"}.',
        'If no question above answers what was asked, reply {"key": null, "why": '
        '"<one short sentence saying what the operator asked for that is missing>"}.',
        "Never invent a key. Never answer the question yourself.",
    ]
    return "\n".join(lines)


def classify(question_text: str, gate: spend.Gate, *,
             api_key: str, transport: Any = None) -> tuple[Question, dict[str, Any]]:
    """Which catalogue question the operator meant, and its slot values.

    `transport` is injected so the suite can drive every branch without a network call or
    a cent of spend; it is not an extension point and nothing in the product passes it.

    The spend check happens *before* the request and the record *after* it, using the
    token counts the API actually reports rather than the estimate — `spend.Gate.record`'s
    docstring explains why an estimate must never be booked as a charge.
    """
    text = (question_text or "").strip()
    if not text:
        raise QuestionRefused("Type a question first.")
    if len(text) > 500:
        raise QuestionRefused(
            f"That question is {len(text)} characters. Ask something shorter than 500 — "
            "a long prompt is usually several questions, and the answer to several "
            "questions at once is one that is wrong about most of them.")

    prompt = _router_prompt()
    # Four characters per token is the conventional English approximation and it is
    # rounded *up* here, because an underestimate is a call the gate allows and should
    # not have. `spend.Rate`'s own docstring makes the same argument about the rates.
    estimated_in = (len(prompt) + len(text)) // 3
    gate.check(ROUTER_RATE_KEY, tokens_in=estimated_in, tokens_out=ROUTER_MAX_TOKENS)

    body = {
        "model": ROUTER_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        # Zero, because this is a classification and a console that routes the same
        # sentence to two different questions on two afternoons is one nobody can trust
        # or debug.
        "temperature": 0,
        "max_tokens": ROUTER_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    payload = _post_json(OPENAI_URL, body, timeout=ROUTER_TIMEOUT, transport=transport,
                         headers={"Authorization": f"Bearer {api_key}"},
                         what="the question classifier")

    try:
        choice = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {})
    except (KeyError, IndexError, TypeError) as exc:
        raise McpRefused(
            "The question classifier returned a response with no message in it. "
            f"Nothing was billed for a usable answer. Raw shape: {list(payload)[:8]}"
        ) from exc

    gate.record(spend.estimate(
        ROUTER_RATE_KEY,
        tokens_in=int(usage.get("prompt_tokens", estimated_in)),
        tokens_out=int(usage.get("completion_tokens", 0)),
    ))

    try:
        decision = json.loads(choice)
    except json.JSONDecodeError as exc:
        raise QuestionRefused(
            "The classifier did not return JSON, so which question it meant cannot be "
            f"established. It said: {choice[:200]!r}") from exc

    key = decision.get("key")
    if key is None:
        why = str(decision.get("why", "")).strip()
        raise QuestionRefused(
            "This console cannot answer that yet. " + (why or "")
            + " The questions it can answer are listed beside the box; adding one is a "
              "change to `mcp.QUESTIONS`.")
    if key not in BY_KEY:
        raise QuestionRefused(
            f"The classifier chose {key!r}, which is not one of this console's "
            "questions. Nothing was run. Try rephrasing.")
    return BY_KEY[key], decision


# ------------------------------------------------------------------- the MCP client

def _post_json(url: str, body: dict[str, Any], *, timeout: int, transport: Any,
               headers: dict[str, str], what: str, expect_body: bool = True) -> Any:
    """One JSON POST, or a raised error naming what could not be reached.

    There is no retry. A retry is a second call at a second price (`spend.py`'s first
    house rule) and, for the MCP handshake, a second session — so retrying belongs to
    whoever can see that both happened, which is the operator clicking Ask again.

    `expect_body=False` is for the one message in this protocol that legitimately has no
    reply: a JSON-RPC *notification*. Its body is not parsed rather than parsed
    leniently, because "an empty body means an empty answer" is precisely the silent
    degradation that would turn a broken `select_query` into a page reading "no rows".
    """
    if transport is not None:
        return transport(url, body, headers)

    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            session = response.headers.get("mcp-session-id")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise McpRefused(
            f"{what} refused the call with HTTP {exc.code}. {detail}") from exc
    except OSError as exc:
        raise McpRefused(f"{what} could not be reached: {exc}") from exc

    if not expect_body:
        return {}
    parsed = _parse_body(raw, what)
    if session and isinstance(parsed, dict):
        parsed["_session"] = session
    return parsed


def _parse_body(raw: str, what: str) -> Any:
    """JSON, or one `data:` frame of Server-Sent Events.

    The managed MCP server answers a plain POST with `text/event-stream` — measured
    2026-08-13 — so a client that only knows `json.loads` gets a parse error against a
    server that is working perfectly. Both shapes are read; anything else raises rather
    than returning an empty result that would render as "no rows".
    """
    for line in raw.splitlines():
        if line.startswith("data: "):
            raw = line[6:]
            break
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpRefused(
            f"{what} returned something that is neither JSON nor an SSE data frame, so "
            f"there is no answer to report: {raw[:200]!r}") from exc


@dataclass(frozen=True)
class Server:
    """Where the managed MCP server is and what identifies us to it.

    `database` is derived from `DATABASE_URL` rather than configured separately. The MCP
    query must run against the database the rest of the console reads, and two settings
    holding one fact is how they end up disagreeing — at which point this page answers
    confidently about a database nobody is looking at.
    """

    url: str
    cluster_id: str
    api_key: str
    database: str

    def _rpc(self, method: str, params: dict[str, Any] | None, *,
             session: str | None, notify: bool, transport: Any) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            body["id"] = 1
        if params is not None:
            body["params"] = params
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "mcp-cluster-id": self.cluster_id,
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if session:
            headers["mcp-session-id"] = session
        return _post_json(self.url, body, timeout=MCP_TIMEOUT, transport=transport,
                          headers=headers, what="the CockroachDB Cloud MCP server",
                          expect_body=not notify)

    def select(self, sql: str, *, transport: Any = None) -> tuple[list[str], list[dict]]:
        """Run one read through `select_query` and return `(columns, rows)`.

        Only this tool is ever named. `insert_rows`, `create_table` and
        `create_database` are on the same server and this client has no code path that
        can reach them — which is a stronger guarantee than checking the statement,
        because it holds even if the statement check is wrong.
        """
        handshake = self._rpc(
            "initialize",
            {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
             "clientInfo": {"name": "respect-the-funk-console", "version": "1.0"}},
            session=None, notify=False, transport=transport)
        # A JSON-RPC error carried in a 200 response. Checked explicitly because the
        # HTTP status is not the protocol's error channel: an expired key can arrive as
        # a perfectly successful 200 whose payload says the session was refused, and
        # proceeding from there would send the statement into a session that does not
        # exist and report the resulting confusion as an empty answer.
        if not isinstance(handshake, dict) or "error" in handshake:
            raise McpRefused(
                "The MCP server refused the session handshake: "
                f"{str((handshake or {}).get('error', handshake))[:300]}")
        session = handshake.get("_session")
        self._rpc("notifications/initialized", None, session=session, notify=True,
                  transport=transport)

        answer = self._rpc(
            "tools/call",
            {"name": "select_query",
             "arguments": {"database": self.database, "query": sql}},
            session=session, notify=False, transport=transport)

        if not isinstance(answer, dict) or "error" in answer:
            detail = (answer or {}).get("error", answer) if isinstance(answer, dict) \
                else answer
            raise McpRefused(
                f"The MCP server refused `select_query`: {str(detail)[:300]}")
        result = answer.get("result") or {}
        if result.get("isError"):
            raise McpRefused(
                "The MCP server ran the statement and reported an error: "
                f"{_content_text(result)[:300]}")
        return _rows_from(result)


def _content_text(result: dict[str, Any]) -> str:
    return " ".join(part.get("text", "") for part in result.get("content", [])
                    if isinstance(part, dict))


def _rows_from(result: dict[str, Any]) -> tuple[list[str], list[dict]]:
    """`{"rows": [...]}`, out of MCP's content envelope.

    Column order is taken from the first row's key order, which CockroachDB returns in
    the order the SELECT named them. An empty result therefore has no columns to show,
    and the template renders "no rows" rather than an empty table with invented headers.
    """
    text = _content_text(result)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpRefused(
            "The MCP server's answer was not the JSON result envelope this client "
            f"expects: {text[:200]!r}") from exc
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise McpRefused(
            f"The MCP server's answer carried no `rows` array: {text[:200]!r}")
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    return columns, rows


# ------------------------------------------------------------------------ assembly

def load() -> tuple[Server, str]:
    """The configured server and the classifier's key, or a refusal naming the fix.

    Every absence raises. There is no read-only mode, no "the MCP page is disabled", no
    demo answer — this page either asks the real cluster through the real server or it
    tells the operator exactly which environment variable is missing. A console that
    silently degrades into a fixture is the failure `routes.py`'s own header calls out.

    `COCKROACH_API_KEY` is not something a human has to invent: `ccloud auth login`
    already writes one to `~/.config/.cockroachdb/credentials.json`, verified 2026-08-13
    to authenticate this endpoint as a plain bearer token. It is read from the
    environment and only from the environment, because that file does not exist in
    Lambda and a settings loader that reads two sources answers differently depending on
    where it runs.
    """
    config = settings_mod.load()
    missing = [name for name, value in (
        ("COCKROACH_API_KEY", config.cockroach_api_key),
        ("COCKROACH_CLUSTER_ID", config.cockroach_cluster_id),
        ("OPENAI_API_KEY", config.openai_api_key),
        ("DATABASE_URL", config.database_url),
    ) if not value]
    if missing:
        raise McpNotConfigured(
            f"{', '.join(missing)} must be set before a question can be asked. "
            "The Cockroach key is the one `ccloud auth login` writes to "
            "~/.config/.cockroachdb/credentials.json; the cluster id is the UUID in "
            "`ccloud cluster list` and in .mcp.json. Export them, or ask this "
            "question on the Counterparties screen, which reads the cluster directly.")
    return (
        Server(url=config.mcp_url, cluster_id=config.cockroach_cluster_id,
               api_key=config.cockroach_api_key, database=_database_name(config.database_url)),
        config.openai_api_key,
    )


def _database_name(database_url: str) -> str:
    """The database `DATABASE_URL` connects to, for `select_query`'s `database` argument.

    Raises when the URL carries no database name. That is not a nicety: `select_query`
    requires the argument, and guessing `defaultdb` would answer questions about a
    database this console may not be reading — a wrong answer delivered with a table
    around it, which is the most believable kind.
    """
    path = urllib.parse.urlsplit(database_url).path.strip("/")
    name = path.split("/")[0]
    if not name:
        raise McpNotConfigured(
            "DATABASE_URL names no database, so there is nothing to tell the MCP "
            "server to query. Expected a URL of the form "
            "postgresql://user@host:26257/<database>?sslmode=verify-full.")
    return name


@dataclass(frozen=True)
class Answer:
    """What the operator gets back: the question that was actually asked, the exact
    statement that ran, and the rows.

    The SQL is part of the answer rather than a debug detail. An operator who typed
    English and got a number back has no way to know which of six questions was chosen
    unless they are shown; showing it is what makes a mis-routed question correctable
    instead of merely wrong.
    """

    question: Question
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    cost_usd: Decimal


def ask(question_text: str, tenant_id: str, gate: spend.Gate, *,
        server: Server | None = None, api_key: str | None = None,
        classifier: Any = None, transport: Any = None) -> Answer:
    """English in, rows out, through the managed MCP server.

    The order is the whole safety argument and it is worth stating: classify, render,
    prove scoped, *then* send. Nothing reaches `select_query` that has not been through
    `_refuse_if_unscoped`, and nothing reaches `_refuse_if_unscoped` that was not written
    as a literal in `QUESTIONS`.
    """
    if server is None or api_key is None:
        server, api_key = load()
    question, decision = (classifier or classify)(
        question_text, gate, api_key=api_key, transport=transport)
    sql = render(question, tenant_id, decision)
    columns, rows = server.select(sql, transport=transport)
    return Answer(question=question, sql=sql, columns=columns, rows=rows,
                  cost_usd=gate.incurred_usd)
