"""A lint expressed as a test: every SQL statement against a tenant-scoped table must
carry a `tenant_id` equality predicate — in a `WHERE`/`ON`/column-list position, not
merely as a substring of the statement.

Three lookups in `agents.py` and `agents.retrieve()`'s scalar subqueries used to fetch
by bare `id`, with no `tenant_id` predicate at all. Two auditors had already reported
that as two different things — a cross-tenant data leak, and a performance bug (it
defeats every index whose leading column is `tenant_id`) — before anyone noticed it was
one omission. This test is the thing that stops the next one; a docstring saying "every
query is scoped by tenant_id" (`repo.py`'s own opening line) is exactly the kind of
claim this whole audit exists to stop trusting.

**DB-free by construction**, so it can never silently skip the way a cluster-gated test
does when `DATABASE_URL` is unset. It parses source, not connects to anything.

## What it does

Walks every `.py` file under `spindle/` with `Path.rglob` — not `glob`, which does
not recurse and would silently stop covering `spindle/distributors/` the moment a
query moved there or a new subpackage appeared; an earlier census test in this project
made exactly that mistake. For each file, finds every call that ultimately reaches
`cursor.execute(sql, ...)` — directly, or through a same-module wrapper that only
forwards its own `sql` parameter into `.execute()` (this codebase has two:
`research._rows`/`_one`) — and statically resolves `sql` to text: string and f-string
literals, and simple same-function variable accumulation (`sql = f"..."; sql += "..."`,
the shape `repo.list_parties` builds its `WHERE` clause with).

A statement is then split into independently-checked **units**: the statement's own
text with every parenthesized `(SELECT ...)` scalar subquery stripped out, plus each
subquery's text, recursively. This is what makes the check *mean* something rather than
being satisfied by any `tenant_id` anywhere in a big statement — `agents.retrieve()`'s
outer CTE carries `WHERE tenant_id = %s AND model = %s` and its two scalar subqueries
against `party_document` did not carry the predicate at all; a whole-statement
substring search would have missed exactly the bug this test exists to catch, because
the sibling clause's `tenant_id` would have "covered" for the subquery's lack of one.
Splitting into units is what makes the two independently checkable.

For each unit, every reference to a tenant-scoped table (`FROM`/`INTO`/`UPDATE`/`JOIN
<table>`, matched with a word boundary so `party` cannot match inside `party_fact`) is
checked in the **zone that actually governs it**, not the unit as a whole:

  * a `JOIN <table> ON ...` reference is checked in its own `ON ...` clause — the text
    from right after the table name up to the next `JOIN`/`WHERE`/`GROUP BY`/`ORDER
    BY`/`LIMIT`/`RETURNING`/`HAVING`/`FOR UPDATE`/`ON CONFLICT` — for a `tenant_id`
    **equality** comparison (`_has_tenant_equality`), not merely a mention of the word:
    `tenant_id IS NOT NULL` or `tenant_id != %s` both put `tenant_id` in the zone and
    scope nothing, and neither satisfies this check;
  * a primary `FROM`/`UPDATE` table is checked the same way, in the statement's own
    `WHERE` clause (from `WHERE` to the next such boundary; no `WHERE` at all is an
    automatic violation);
  * an `INSERT INTO <table> (...)` is checked in its own column list (from the table
    name to `VALUES`) for the **presence** of `tenant_id`, not an equality comparison —
    a column list has no operators to compare with; `tenant_id` naming a column *is*
    the claim that this INSERT writes one.

**This is the fix for the exact hole this test shipped with the first time**: an
earlier version of this check looked for the substring `tenant_id` anywhere in a unit,
which a plain `SELECT id, tenant_id, ... FROM party_document WHERE id = %s` satisfies
by having `tenant_id` in its *select list* — one of the three lookups this test exists
to catch would have sailed through it. The zone-based check above only credits
`tenant_id` where it functions as a predicate. It also catches a second blind spot the
substring version had: `JOIN other_tenant_table ON x = y` with no `tenant_id` in that
`ON` clause used to pass whenever the *outer* statement had an unrelated `WHERE
tenant_id = %s` elsewhere — satisfying the substring test while leaving that specific
join to scan across every tenant's rows of the joined table. Splitting into per-subquery
units (below) and now per-reference zones (above) is what makes each reference
independently checkable rather than any one of them able to borrow scoping it does not
carry.

A statement is split into independently-checked **units** first: the statement's own
text with every parenthesized `(SELECT ...)` scalar subquery stripped out, plus each
subquery's text, recursively — the same reasoning as above, one level up: a scalar
subquery's own zone-check must not be satisfiable by a sibling clause's `tenant_id`
either. `agents.retrieve()`'s pre-fix bug is the canonical example: the outer CTE
carried `WHERE tenant_id = %s AND model = %s` and its two scalar subqueries against
`party_document` did not carry the predicate at all.

If a unit's reference fails its zone check, the whole statement fails — unless the
exact (whitespace-normalised) unit text is in `ALLOWLIST` below, with a comment
justifying why that specific statement is genuinely tenant-free. The allowlist is
empty: every statement this test's runs found un-scoped got the predicate added rather
than excused (see the commits this test landed in), and `agents._write_map_source`
INSERTs `demo_request` and `tenant` never come up because those two tables are not in
`TENANT_SCOPED_TABLES` — the row that has no tenant yet, and the tenant row itself,
correctly have no `tenant_id` predicate to carry.

## What would make this test itself untrustworthy, and why each is guarded against

* A `.py` file that stops being discovered — guarded by `rglob`, asserted by
  `test_the_walk_finds_every_module`.
* A call site whose SQL text cannot be statically resolved (built some way this parser
  does not understand), or reached with too few positional arguments for this
  resolver's `arg_index` to be meaningful — both raise, with the file and line, rather
  than being silently skipped; see `_extract_statements`. Neither case exists in the
  codebase today; this is what stops one appearing tomorrow uncovered.
* The allowlist accumulating stale entries nobody re-examines because the statement
  they were written for no longer exists — guarded by the "unused allowlist entries"
  assertion in `test_every_tenant_scoped_statement_carries_tenant_id`.
"""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

SPINDLE_PKG = Path(__file__).resolve().parents[1] / "spindle"

#: Every table in `apps/spindle/schema/*.sql` that carries a `tenant_id` column — the
#: single place this list is maintained. A table is deliberately left out only when its
#: own `CREATE TABLE` has no `tenant_id` column: `tenant` (it IS the tenant), `demo_request`
#: (explicitly tenant-free — see its own comment in `005_party_first.sql`/`repo.py`),
#: `fact_basis` and `source_manifest` (neither carries the column at all). `artist`,
#: `artist_profile`, `track` and the rest of `004_research.sql`'s pre-005 shapes are
#: excluded too: migration `005` dropped them, so no live query can name them.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "party", "party_identifier", "party_role", "party_relation", "work", "recording",
    "release", "recording_work", "release_recording", "party_credit", "lead",
    "party_document", "party_chunk", "party_fact", "party_metric", "agent_run",
    "party_budget", "presence", "suggestion", "lesson", "statement_import",
    # 010's four. They were absent when this list was first written because the branch
    # that added this test did not contain `010_outreach.sql` — so for one merge the
    # lint silently did not cover the newest tables in the schema, which is the exact
    # failure mode it exists to prevent. Verified against `010_outreach.sql`: all four
    # carry `tenant_id NOT NULL`. `fact_basis`, `agent_manifest` and `source_manifest`
    # stay out because their own `CREATE TABLE` genuinely has no such column.
    "campaign", "thread", "message", "outbox",
    # 035's three, added in the same commit as the migration rather than a merge later —
    # the note above records what happened the one time that slipped. Verified against
    # `036_autonomy_and_track_budget.sql`: all three carry `tenant_id UUID NOT NULL`.
    # `budget_raise` matters most here: it is written by an agent acting unattended, so a
    # statement of its that lost its tenant predicate would let one label's automatic
    # spending decision be read or resolved from another's console.
    "autonomy", "recording_budget", "budget_raise",
    # `035`'s ledger. It carries `tenant_id` as the leading column of its own primary
    # key, and its header says an audit ledger that could be read across a tenant
    # boundary "would be worse than no audit ledger, because it would be an audit ledger
    # that leaks" — which is precisely the claim this lint is for. It was absent when the
    # table landed, so for one commit the newest table in the schema was uncovered: the
    # same gap the note above records happening with `010`'s four.
    "decision",
    # Found on 2026-08-15 by deriving the set from the schema rather than reading the
    # list. All three were already scoped correctly in every statement — the lint simply
    # was not claiming them, which is coverage believed rather than held.
    "contact_route", "recording_asset", "tenant_budget",
    # `037`'s evidence table. It holds which creator posted over which sound, which is
    # the shortlisting signal for the whole UGC channel — Pillar 10 §7 ranks on it rather
    # than on demographics. A statement of its that lost its tenant predicate would let
    # one label's creator research, the part `037` says costs a person-afternoon to
    # gather by hand, be read from another's console. Added in the same commit as the
    # migration, per the note on `035` above.
    "sound_usage",
    # `038`'s pair. `audience_profile` holds the roster artist's own audience under her
    # own OAuth grant, and `audience_segment` the breakdown beneath it — where her
    # listeners are, which is the signal the geographic rerank ranks counterparties on.
    # These are the most sensitive rows this schema has ever held: they are not a public
    # index of stations but one named person's audience data, given to us on the
    # understanding that we are her label. A statement that lost its tenant predicate
    # would hand that to whoever else is on the cluster. Added in the same commit as the
    # migration, per the note on `035` above.
    "audience_profile", "audience_segment",
)

#: A reference to a table, tagged with which keyword introduced it — the keyword is
#: what decides which zone of the statement governs its `tenant_id` check below.
_REF_RE = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)",
                     re.IGNORECASE)

#: Clause-boundary keywords. A `JOIN`'s own governing zone ends at the next one of
#: these (most commonly the next `JOIN` or the statement's `WHERE`); the primary
#: `FROM`/`UPDATE` table's zone is bounded by `WHERE` on one side and the next of these
#: on the other; an `INSERT`'s zone is bounded by `VALUES`.
_BOUNDARY_RE = re.compile(
    r"\b(JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT|RETURNING|HAVING|FOR\s+UPDATE|"
    r"ON\s+CONFLICT|VALUES)\b", re.IGNORECASE)

_JOIN_ZONE_ENDERS = {"JOIN", "WHERE", "GROUP BY", "ORDER BY", "LIMIT", "RETURNING",
                     "HAVING", "FOR UPDATE", "ON CONFLICT"}
_WHERE_ZONE_ENDERS = {"GROUP BY", "ORDER BY", "LIMIT", "RETURNING", "HAVING",
                      "FOR UPDATE"}

#: Statements that reference a tenant-scoped table but are genuinely tenant-free,
#: keyed to the exact (whitespace-normalised) statement text so a change to the
#: statement drops out of the allowlist and has to be re-justified rather than staying
#: silently excused forever. Empty: nothing found by this test's first run stayed
#: unscoped — see the module docstring.
ALLOWLIST: dict[str, str] = {}

#: Interpolations this resolver is permitted to treat as an opaque *value* rather than
#: give up on, keyed by `(file, enclosing function, variable name)` with the reason.
#:
#: The resolver's default for an interpolation it cannot follow is to raise, and that
#: default is the whole guarantee: a statement it cannot read is a statement this file
#: no longer covers, so falling back to a placeholder *generally* would silently retire
#: the check. Registering a site here is therefore a claim about that site, and the claim
#: is narrow: **the interpolated text is a value, not SQL structure** — it cannot
#: introduce a table reference, a `JOIN`, or a `WHERE` clause, so substituting a
#: placeholder leaves the statement's skeleton — the part carrying `tenant_id` — intact
#: and still checked.
#:
#: That claim is not something this file can verify by reading the AST, which is exactly
#: why each entry has to be written by hand and justified. An interpolation that splices
#: in a clause must never be registered here; simplify the call instead.
VALIDATED_FRAGMENTS: dict[tuple[str, str, str], str] = {
    ("agents.py", "shortlist_as_of", "as_of"): (
        "A relative timestamp for `AS OF SYSTEM TIME`, interpolated because CockroachDB "
        "requires that clause to be a constant at plan time and rejects a placeholder. "
        "`shortlist_as_of` regex-validates it against `-\\d{1,4}(s|m|h)` and raises "
        "otherwise, before it reaches the SQL — so what lands here is a timestamp "
        "literal, never a clause. The surrounding statement keeps its literal "
        "`WHERE tenant_id = %s` and is checked below in both of its two possible forms."),
    ("budgets.py", "value_of", "as_of"): (
        "A cluster logical timestamp for `AS OF SYSTEM TIME`, interpolated for the same "
        "reason `agents.shortlist_as_of` interpolates one: CockroachDB requires that "
        "clause to be a constant at plan time and rejects a placeholder. "
        "`budgets._check_as_of` regex-validates it against `\\d{1,19}(\\.\\d{1,10})?` "
        "with `fullmatch` and raises otherwise, before it reaches the SQL — so what "
        "lands here is a timestamp literal and can never be a clause, a quote or a "
        "second statement. The surrounding query keeps its literal "
        "`WHERE tenant_id = %s AND party_id = %s` and is checked below."),
    ("budgets.py", "valuation", "as_of"): (
        "The same validated timestamp as the `value_of` entry above, in the query that "
        "walks track -> campaign -> thread -> counterparty. Note the clause attaches to "
        "`thread` and cannot introduce a table or a predicate: the statement's literal "
        "`WHERE t.tenant_id = %s` survives interpolation, and both joined tables are "
        "correlated on `tenant_id` in their `ON` clauses, so a replay reads one "
        "tenant's history exactly as a live read does."),
    ("platforms.py", "search", "filters"): (
        "The *optional* predicates of the public search — country, platform and a name "
        "match — and nothing else. The two predicates that matter to this test are "
        "written literally into the statement (`WHERE p.tenant_id = %(t)s AND "
        "p.party_class = 'counterparty'`) and cannot be reached from here, so a "
        "`filters` that resolved to the empty string still leaves the statement scoped; "
        "that is the reason the mandatory half was pulled out of the assembled clause "
        "rather than this entry being written to cover the whole `WHERE`. "
        "Every value inside it is a bound parameter — `%(cc)s`, `%(pf)s`, `%(q)s` — so "
        "no caller input is interpolated as SQL; the only interpolated *text* is "
        "`PLATFORM_CASE`, a module constant, and `platform` is checked against "
        "`platforms.KEYS` and raises before it reaches the string."),
}

#: What an entry in `VALIDATED_FRAGMENTS` resolves to. Deliberately not SQL-shaped: it
#: must not be readable as a table name by `_REF_RE`, nor as a `tenant_id` predicate by
#: `_has_tenant_equality`, so a registered fragment can only ever make a statement look
#: *less* scoped than it is — never more.
OPAQUE = "<opaque>"

#: Ceiling on the number of statically-possible texts one call site may expand into.
#: A conditional fragment doubles the count, so a statement built from several would
#: grow exponentially; a site that hits this is not one to raise the ceiling for, it is
#: one to simplify.
MAX_VARIANTS = 8


# --------------------------------------------------------------- static SQL resolution

def _cross(prefixes: list[str], suffixes: list[str]) -> list[str]:
    """Every `prefix + suffix` pairing, capped at `MAX_VARIANTS`. Raising rather than
    truncating: a silently-dropped variant is a statement that stopped being checked,
    which is the failure mode this whole file is built to make impossible."""
    if len(prefixes) * len(suffixes) > MAX_VARIANTS:
        raise AssertionError(
            f"this statement expands into more than {MAX_VARIANTS} statically-possible "
            "texts, which means it is assembled from several conditional fragments. "
            "Simplify it rather than raising the ceiling — a statement nobody can read "
            "in one piece is one nobody can review for tenant scoping either.")
    return [p + s for p in prefixes for s in suffixes]


def _literal_texts(node: ast.AST | None, env: dict[str, list[str] | None],
                   opaque: set[str]) -> list[str] | None:
    """Every statically-possible string value of an AST node, or `None` if it depends on
    something this resolver does not follow (a function call, a dict lookup, ...).

    A *list*, not a single string, because a conditional fragment (`x if c else y`) gives
    the statement more than one possible text and checking only one of them would let the
    other hide. Every variant is returned and every variant is checked, so a branch that
    drops `tenant_id` fails the suite even when its sibling branch keeps it.

    `opaque` names the variables — drawn from `VALIDATED_FRAGMENTS` for this scope — whose
    interpolated value may stand in as `OPAQUE` instead of defeating resolution.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)):
        return [str(node.value)]
    if isinstance(node, ast.JoinedStr):  # an f-string
        parts: list[str] = [""]
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts = _cross(parts, [str(value.value)])
            elif isinstance(value, ast.FormattedValue):
                resolved = _literal_texts(value.value, env, opaque)
                if resolved is None:
                    # Unreadable — but permitted to be, if this exact interpolation is
                    # registered as a value rather than structure. Only a bare name can
                    # be registered: an expression has no single name to justify.
                    if isinstance(value.value, ast.Name) and value.value.id in opaque:
                        resolved = [OPAQUE]
                    else:
                        return None
                parts = _cross(parts, resolved)
            else:
                return None
        return parts
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.IfExp):  # `a if cond else b` — both are possible
        left = _literal_texts(node.body, env, opaque)
        right = _literal_texts(node.orelse, env, opaque)
        if left is None or right is None:
            return None
        return _cross([""], left + right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_texts(node.left, env, opaque)
        right = _literal_texts(node.right, env, opaque)
        return None if left is None or right is None else _cross(left, right)
    return None


def _gather_assigns(nodes: Any) -> list[tuple[str, str, ast.AST, int]]:
    out = []
    for n in nodes:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            out.append(("assign", n.targets[0].id, n.value, n.lineno))
        elif (isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)
              and isinstance(n.op, ast.Add)):
            out.append(("aug", n.target.id, n.value, n.lineno))
    return out


def _build_env(assigns: list[tuple[str, str, ast.AST, int]],
               base: dict[str, list[str] | None] | None = None,
               opaque: set[str] | None = None) -> dict[str, list[str] | None]:
    """A same-scope string environment: `sql = f"..."` then `sql += "..."`, in source
    order, so a variable built across a few statements (`repo.list_parties`) resolves
    the same as a single literal would. Order matters more than control flow here — an
    `if` branch's assignment is folded in unconditionally, which over-approximates
    (assumes every branch ran) rather than under-approximates, so a statement that
    *sometimes* omits `tenant_id` cannot hide behind a branch that includes it.

    Each name maps to every value it could statically hold, so a variable assigned a
    conditional expression carries both of its possibilities forward into whatever
    statement uses it."""
    env: dict[str, list[str] | None] = dict(base or {})
    names = opaque or set()
    for kind, name, valnode, _ in sorted(assigns, key=lambda t: t[3]):
        val = _literal_texts(valnode, env, names)
        if kind == "assign":
            env[name] = val
        else:
            prev = env.get(name)
            env[name] = None if prev is None or val is None else _cross(prev, val)
    return env


def _find_forwarders(tree: ast.AST) -> tuple[dict[str, int], set[int]]:
    """Functions in this module whose body immediately forwards one of their own
    parameters into `<obj>.execute(...)` — `research._rows`/`_one`. Returns the
    forwarding parameter's position for each such function, and the node-ids of the
    `.execute(...)` calls that were used to detect it (so the walk below does not also
    try, and fail, to resolve their generic parameter as if it were literal SQL)."""
    forwarders: dict[str, int] = {}
    internal: set[int] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        params = [a.arg for a in fn.args.args]
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "execute" and call.args):
                continue
            arg0 = call.args[0]
            if isinstance(arg0, ast.Name) and arg0.id in params:
                forwarders[fn.name] = params.index(arg0.id)
                internal.add(id(call))
    return forwarders, internal


def _extract_statements(path: Path) -> list[tuple[int, str]]:
    """`(lineno, sql_text)` for every statement reaching `.execute()` in this file, one
    entry per statically-possible text — a call whose SQL is assembled with a conditional
    fragment yields every form it could take, so each is checked independently.

    Raises with file/line detail — rather than skipping — for any call whose SQL text
    this resolver cannot statically determine, so a future dynamic-SQL pattern fails
    the test loudly instead of quietly falling out of coverage."""
    tree = ast.parse(path.read_text(), filename=str(path))
    forwarders, internal = _find_forwarders(tree)
    fn_scopes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]

    try:
        relative = path.resolve().relative_to(SPINDLE_PKG).as_posix()
    except ValueError:  # a path outside the package — the self-tests below use these
        relative = path.name

    def opaque_for(scope: ast.FunctionDef | None) -> set[str]:
        """The names this scope may leave opaque, per `VALIDATED_FRAGMENTS`. Keyed by
        function name rather than line, so moving the function does not silently drop
        its registration — renaming it does, which is the intended re-justification."""
        fn_name = scope.name if scope else "<module>"
        return {var for (f, fn, var) in VALIDATED_FRAGMENTS
                if f == relative and fn == fn_name}

    module_assigns = _gather_assigns(
        n for n in tree.body if isinstance(n, (ast.Assign, ast.AugAssign)))
    module_env = _build_env(module_assigns, opaque=opaque_for(None))
    env_cache: dict[int | None, dict[str, list[str] | None]] = {}

    def env_for(scope: ast.FunctionDef | None) -> dict[str, list[str] | None]:
        key = id(scope) if scope else None
        if key not in env_cache:
            if scope is None:
                env_cache[key] = module_env
            else:
                env_cache[key] = _build_env(_gather_assigns(ast.walk(scope)),
                                            base=module_env, opaque=opaque_for(scope))
        return env_cache[key]

    def scope_of(node: ast.AST) -> ast.FunctionDef | None:
        best = None
        for fn in fn_scopes:
            if fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno):
                if best is None or fn.lineno > best.lineno:
                    best = fn
        return best

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else None)

        if name == "execute" and isinstance(node.func, ast.Attribute):
            if id(node) in internal:
                continue
            arg_index = 0
        elif name in forwarders:
            arg_index = forwarders[name]
        else:
            continue

        if len(node.args) <= arg_index:
            # A call reaching `.execute`/a known forwarder with fewer positional args
            # than the SQL parameter's position — e.g. the SQL passed by keyword, or a
            # same-named function that is not actually the forwarder this resolver
            # detected. No such call exists today; raising rather than skipping is what
            # keeps that true instead of merely documenting it.
            raise AssertionError(
                f"{path}:{node.lineno}: this call reaches position {arg_index} for its "
                f"SQL argument but was only given {len(node.args)} positional "
                "argument(s) — this resolver cannot tell whether SQL text is even "
                "involved. Pass the SQL positionally, or teach this resolver the new "
                "call shape.")
        scope = scope_of(node)
        texts = _literal_texts(node.args[arg_index], env_for(scope), opaque_for(scope))
        if texts is None:
            raise AssertionError(
                f"{path}:{node.lineno}: this test cannot statically resolve the SQL "
                "text passed here — it is neither a literal/f-string nor a variable "
                "this resolver can trace. Either simplify the call so the SQL is "
                "readable statically, or teach `_literal_texts`/`_find_forwarders` the "
                "new shape. If the unreadable part is an interpolated *value* rather "
                "than SQL structure, register it in `VALIDATED_FRAGMENTS` with the "
                "argument for why. A statement this test cannot see is a statement the "
                "tenant-scoping guarantee no longer covers.")
        out.extend((node.lineno, text) for text in texts)
    return out


# ------------------------------------------------------------ per-statement checking

def _extract_units(text: str) -> list[str]:
    """Split `text` into independently-checkable segments: the statement's own text
    with every `(SELECT ...)` scalar subquery removed, plus each subquery's inner text,
    recursively. See the module docstring for why whole-statement substring search is
    not enough."""
    units: list[str] = []
    pattern = re.compile(r"\(\s*SELECT\b", re.IGNORECASE)
    i = 0
    remainder: list[str] = []
    last = 0
    n = len(text)
    while True:
        m = pattern.search(text, i)
        if not m:
            break
        start = m.start()
        depth = 0
        j = start
        while j < n:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        end = j
        remainder.append(text[last:start])
        last = end + 1
        units.extend(_extract_units(text[start + 1:end]))
        i = end + 1
    remainder.append(text[last:])
    units.append("".join(remainder))
    return units


def _next_boundary(unit: str, start: int, enders: set[str]) -> int:
    """Position of the next boundary keyword in `enders`, or end-of-string."""
    for m in _BOUNDARY_RE.finditer(unit, start):
        label = " ".join(m.group(1).upper().split())
        if label in enders:
            return m.start()
    return len(unit)


def _zone(unit: str, keyword: str, ref_end: int, where_pos: int | None) -> str:
    """The text that governs one table reference's `tenant_id` check.

    `JOIN`: its own `ON ...` clause — from right after the table name to the next
    clause boundary. `FROM`/`UPDATE`: the statement's own `WHERE` clause — a join
    elsewhere in the same statement must not be able to lend its `ON`-clause
    `tenant_id` to the primary table, nor may the primary table's `WHERE` lend
    `tenant_id` back to a join that lacks its own. `INTO`: the column list up to
    `VALUES`."""
    if keyword == "JOIN":
        return unit[ref_end:_next_boundary(unit, ref_end, _JOIN_ZONE_ENDERS)]
    if keyword == "INTO":
        return unit[ref_end:_next_boundary(unit, ref_end, {"VALUES"})]
    # FROM / UPDATE — governed by the statement's own WHERE, not by any JOIN's ON.
    if where_pos is None:
        return ""
    return unit[where_pos:_next_boundary(unit, where_pos, _WHERE_ZONE_ENDERS)]


#: `tenant_id` immediately followed by a bare `=` (not `==`) — the forward direction of
#: an equality comparison, `tenant_id = <anything>` including `tenant_id = ANY(%s)`.
_EQ_FORWARD_RE = re.compile(r"\btenant_id\b\s*=(?!=)")
#: The reversed direction, `<anything> = tenant_id`. The lookbehind excludes `!=`,
#: `<=`, `>=` and `==` — none of those are a bare `=` immediately before `tenant_id`.
_EQ_REVERSE_RE = re.compile(r"(?<![!<>=])=\s*\btenant_id\b")


def _has_tenant_equality(zone: str) -> bool:
    """Whether `zone` compares `tenant_id` for equality against something — not just
    mentions it. `tenant_id IS NOT NULL`, `tenant_id != %s` and `tenant_id > %s` all
    contain the word `tenant_id` and scope nothing; none of them satisfy this, because
    neither regex's `=` requirement is met (`IS`/`IN`/`<`/`>` are not `=`, and `!=`/`<=`/
    `>=` are excluded by the lookbehind on the reversed check)."""
    return bool(_EQ_FORWARD_RE.search(zone) or _EQ_REVERSE_RE.search(zone))


def _violations(text: str) -> list[tuple[str, str, str]]:
    """`(unit, keyword, table)` for every table reference in `text` whose governing
    zone (see `_zone`) does not carry a `tenant_id` predicate. `JOIN`/`FROM`/`UPDATE`
    zones require an *equality* comparison (`_has_tenant_equality`) — a zone can
    mention `tenant_id` in a comparison that scopes nothing at all (`tenant_id IS NOT
    NULL` passes exactly one predicate: that the column has ever been set, which it
    always has) and that must not pass this check. `INTO`'s zone is a column list, not
    a comparison — `tenant_id` appearing there at all is what "this INSERT writes a
    tenant" means, so it is checked by simple presence instead."""
    found = []
    for unit in _extract_units(text):
        where_match = re.search(r"\bWHERE\b", unit, re.IGNORECASE)
        where_pos = where_match.start() if where_match else None
        for m in _REF_RE.finditer(unit):
            keyword = m.group(1).upper()
            table = m.group(2).lower()
            if table not in TENANT_SCOPED_TABLES:
                continue
            zone = _zone(unit, keyword, m.end(), where_pos)
            ok = (re.search(r"\btenant_id\b", zone) if keyword == "INTO"
                  else _has_tenant_equality(zone))
            if not ok:
                found.append((unit, keyword, table))
    return found


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------------- the tests

class TenantScoping(unittest.TestCase):

    def test_the_walk_finds_every_module(self) -> None:
        # `distributors/` is a subpackage `glob("*.py")` would not recurse into — the
        # exact mistake this test's docstring warns about repeating.
        found = {p.relative_to(SPINDLE_PKG) for p in SPINDLE_PKG.rglob("*.py")}
        self.assertIn(Path("agents.py"), found)
        self.assertIn(Path("fleet.py"), found)
        self.assertIn(Path("distributors/base.py"), found,
                      "rglob must recurse into subpackages, not just the top level")

    def test_a_conditional_fragment_is_checked_in_every_form(self) -> None:
        """`_literal_texts` returning a *list* is the whole defence against a statement
        assembled with `x if cond else y`: resolving only one branch would let the other
        drop `tenant_id` unseen. Prove it against a throwaway module whose two branches
        differ exactly that way, rather than trusting that the real code happens to be
        safe in both of its forms — which it is, and which is not evidence of anything.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conditional.py"
            path.write_text(
                "def q(conn, tenant_id, scoped):\n"
                "    where = 'WHERE tenant_id = %s' if scoped else ''\n"
                "    with conn.cursor() as cur:\n"
                "        cur.execute(f'SELECT id FROM party {where}')\n"
            )
            texts = [sql for _, sql in _extract_statements(path)]

        self.assertEqual(
            len(texts), 2,
            f"both branches should surface as separate statements, got: {texts}")
        offenders = [t for t in texts if _violations(t)]
        self.assertEqual(
            len(offenders), 1,
            "the unscoped branch must be caught even though its sibling is scoped — "
            f"statements checked: {texts}")

    def test_an_unregistered_opaque_interpolation_still_raises(self) -> None:
        """The `VALIDATED_FRAGMENTS` escape must stay opt-in per site. A module that
        interpolates an untraceable value without registering it is exactly the dynamic
        SQL this resolver refuses to pretend it can read."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dynamic.py"
            path.write_text(
                "def q(conn, tenant_id, frag):\n"
                "    with conn.cursor() as cur:\n"
                "        cur.execute(f'SELECT id FROM party {frag} WHERE tenant_id = %s')\n"
            )
            with self.assertRaises(AssertionError) as caught:
                _extract_statements(path)
        self.assertIn("cannot statically resolve", str(caught.exception))

    def test_every_validated_fragment_entry_is_still_live(self) -> None:
        """An entry left behind for a call site that no longer interpolates anything is
        a standing permission nobody is checking — the same staleness the `ALLOWLIST`
        guard below refuses to tolerate, applied to the other escape hatch."""
        for (rel, fn, var), reason in VALIDATED_FRAGMENTS.items():
            with self.subTest(fragment=(rel, fn, var)):
                path = SPINDLE_PKG / rel
                self.assertTrue(path.exists(), f"{rel} no longer exists")
                tree = ast.parse(path.read_text(), filename=str(path))
                fns = [n for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef) and n.name == fn]
                self.assertEqual(
                    len(fns), 1,
                    f"{rel} does not define exactly one `{fn}` — the registration is "
                    "stale; re-justify it against whatever replaced the function")
                names = {n.id for n in ast.walk(fns[0]) if isinstance(n, ast.Name)}
                self.assertIn(
                    var, names,
                    f"`{var}` is no longer referenced in {rel}:{fn} — remove the "
                    "entry rather than let it excuse a future interpolation silently")
                self.assertTrue(reason.strip(), "an entry must carry its argument")

    def test_every_tenant_scoped_statement_carries_tenant_id(self) -> None:
        used_allowlist_entries: set[str] = set()
        failures: list[str] = []
        checked = 0

        for path in sorted(SPINDLE_PKG.rglob("*.py")):
            for lineno, sql in _extract_statements(path):
                checked += 1
                for unit, keyword, table in _violations(sql):
                    key = _normalise(unit)
                    if key in ALLOWLIST:
                        used_allowlist_entries.add(key)
                        continue
                    rel = path.relative_to(SPINDLE_PKG)
                    failures.append(
                        f"{rel}:{lineno} {keyword} {table} has no tenant_id predicate "
                        f"in its governing clause:\n    {key[:200]}")

        # A change to `spindle/` that stops finding any `.execute()` call at all
        # is this test silently checking nothing — fail loudly rather than pass empty.
        self.assertGreater(checked, 50,
                           "found suspiciously few SQL statements — is the walk broken?")

        if failures:
            self.fail(
                f"{len(failures)} statement(s) reference a tenant-scoped table without "
                "a tenant_id predicate:\n\n" + "\n\n".join(failures))

        unused = set(ALLOWLIST) - used_allowlist_entries
        self.assertFalse(
            unused,
            f"{len(unused)} allowlist entr{'y is' if len(unused) == 1 else 'ies are'} "
            "no longer matched by any statement — the code changed and the entry is "
            "stale; remove it rather than let it excuse something else silently: "
            + repr(unused))


if __name__ == "__main__":
    unittest.main()


#: Tables that carry `tenant_id` and are deliberately NOT in `TENANT_SCOPED_TABLES`.
#: Every entry needs a reason, because this set is the only way a table escapes the lint
#: and an unexplained entry is indistinguishable from an oversight.
#: The migrations themselves, which are the only authority on what a table has.
SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schema"

UNSCOPED_BY_DESIGN: dict[str, str] = {
    # The boundary. You consult `account` to find out *which* tenant an address belongs
    # to, so a tenant predicate cannot be a precondition of reading it — resolving the
    # tenant is the query's entire purpose. `account_count` is likewise a deployment-wide
    # capacity check ("the service is full"), not a tenant's own number. This is the same
    # category as `tenant` itself, which escapes only because its key is `id`.
    "account": "the table that resolves which tenant an email belongs to",
    # Replaced by `party` and friends in `005_party_first.sql`, which says so in its
    # header. Still defined by migrations 001-004 because this directory is additive and
    # nothing here removes them, but `spindle/` issues no statement against any of them —
    # verified by this module's own scan, which finds none to complain about.
    "artist": "superseded by party (005)",
    "artist_budget": "superseded by party_budget (005)",
    "artist_chunk": "superseded by party_chunk (005)",
    "artist_document": "superseded by party_document (005)",
    "artist_fact": "superseded by party_fact (005)",
    "artist_identifier": "superseded by party_identifier (005)",
    "artist_metric": "superseded by party_metric (005)",
    "artist_profile": "superseded by party (005)",
    "track": "superseded by recording (005)",
}


def _schema_tables_with_tenant_id(schema_dir: Path) -> set[str]:
    """Every table the migrations give a `tenant_id`, read from the DDL itself."""
    bodies: dict[str, str] = {}
    for path in sorted(schema_dir.glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", path.read_text())
        for m in re.finditer(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)\s*\((.*?)\n\)\s*;",
                sql, re.S | re.I):
            bodies[m.group(1)] = bodies.get(m.group(1), "") + m.group(2)
        # A table can acquire the column later; 025 and 033 both do this elsewhere.
        for m in re.finditer(
                r"ALTER\s+TABLE\s+([a-z_]+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?tenant_id",
                sql, re.I):
            bodies[m.group(1)] = bodies.get(m.group(1), "") + " tenant_id "
    return {t for t, body in bodies.items() if re.search(r"\btenant_id\b", body)}


class ScopedTableListIsDerivable(unittest.TestCase):
    """`TENANT_SCOPED_TABLES` is hand-maintained, and it has silently gone stale twice.

    The list's own header records the first occurrence. The second was found on
    2026-08-15: `decision`, `autonomy`, `recording_budget` and `budget_raise` were added
    to the schema and to neither this list nor `mcp`'s copy, so every statement against
    them sat outside the lint — not failing it, not covered by it. `test_mcp` compares
    the two lists to *each other*, which cannot catch this: both were consistently wrong.

    The same sweep then found `contact_route`, `recording_asset` and `tenant_budget` had
    been outside it for longer. All three passed the moment they were added, which is the
    point — the statements were right and the coverage was imaginary.

    So the list stops being trusted on its own. This derives the set from the DDL and
    fails on any table that is in neither the list nor `UNSCOPED_BY_DESIGN`. A new
    tenant-scoped table now fails the build until somebody classifies it, which is the
    opposite of the failure this file has had twice: an inclusion list silently missing a
    table is invisible, and an exclusion list missing one is a red build.

    Modelled on `test_vector_plans.VectorQueryCensus`, which already does exactly this
    for `<=>` queries and for the same reason.
    """

    def test_no_tenant_scoped_table_is_unclassified(self) -> None:
        derived = _schema_tables_with_tenant_id(SCHEMA_DIR)
        classified = set(TENANT_SCOPED_TABLES) | set(UNSCOPED_BY_DESIGN)
        unclassified = derived - classified
        self.assertEqual(
            unclassified, set(),
            "these tables carry `tenant_id` in the schema but appear in neither "
            "TENANT_SCOPED_TABLES nor UNSCOPED_BY_DESIGN, so no statement against them "
            "is being linted:\n  " + "\n  ".join(sorted(unclassified)) +
            "\n\nAdd each to TENANT_SCOPED_TABLES (and to `mcp.TENANT_SCOPED_TABLES`, "
            "which test_mcp holds equal), or to UNSCOPED_BY_DESIGN with the reason.")

    def test_every_exemption_still_exists(self) -> None:
        """An exemption for a table the schema no longer defines is a stale exemption,
        and a stale exemption is how a real table gets excused by a name collision."""
        derived = _schema_tables_with_tenant_id(SCHEMA_DIR)
        stale = set(UNSCOPED_BY_DESIGN) - derived
        self.assertEqual(
            stale, set(),
            "UNSCOPED_BY_DESIGN names tables that no longer carry a tenant_id in the "
            "schema: " + ", ".join(sorted(stale)))
