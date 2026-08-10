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

Walks every `.py` file under `rtf_platform/` with `Path.rglob` — not `glob`, which does
not recurse and would silently stop covering `rtf_platform/distributors/` the moment a
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
import unittest
from pathlib import Path
from typing import Any

RTF_PLATFORM = Path(__file__).resolve().parents[1] / "rtf_platform"

#: Every table in `platform/schema/*.sql` that carries a `tenant_id` column — the
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


# --------------------------------------------------------------- static SQL resolution

def _literal_text(node: ast.AST | None, env: dict[str, str | None]) -> str | None:
    """Best-effort static string value of an AST node, or `None` if it depends on
    something this resolver does not follow (a function call, a dict lookup, ...)."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):  # an f-string
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                resolved = _literal_text(value.value, env)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left, env)
        right = _literal_text(node.right, env)
        return None if left is None or right is None else left + right
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
               base: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """A same-scope string environment: `sql = f"..."` then `sql += "..."`, in source
    order, so a variable built across a few statements (`repo.list_parties`) resolves
    the same as a single literal would. Order matters more than control flow here — an
    `if` branch's assignment is folded in unconditionally, which over-approximates
    (assumes every branch ran) rather than under-approximates, so a statement that
    *sometimes* omits `tenant_id` cannot hide behind a branch that includes it."""
    env = dict(base or {})
    for kind, name, valnode, _ in sorted(assigns, key=lambda t: t[3]):
        val = _literal_text(valnode, env)
        if kind == "assign":
            env[name] = val
        else:
            prev = env.get(name)
            env[name] = None if prev is None or val is None else prev + val
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
    """`(lineno, sql_text)` for every statement reaching `.execute()` in this file.
    Raises with file/line detail — rather than skipping — for any call whose SQL text
    this resolver cannot statically determine, so a future dynamic-SQL pattern fails
    the test loudly instead of quietly falling out of coverage."""
    tree = ast.parse(path.read_text(), filename=str(path))
    forwarders, internal = _find_forwarders(tree)
    fn_scopes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]

    module_assigns = _gather_assigns(
        n for n in tree.body if isinstance(n, (ast.Assign, ast.AugAssign)))
    module_env = _build_env(module_assigns)
    env_cache: dict[int | None, dict[str, str | None]] = {}

    def env_for(scope: ast.FunctionDef | None) -> dict[str, str | None]:
        key = id(scope) if scope else None
        if key not in env_cache:
            if scope is None:
                env_cache[key] = module_env
            else:
                env_cache[key] = _build_env(_gather_assigns(ast.walk(scope)), base=module_env)
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
        text = _literal_text(node.args[arg_index], env_for(scope_of(node)))
        if text is None:
            raise AssertionError(
                f"{path}:{node.lineno}: this test cannot statically resolve the SQL "
                "text passed here — it is neither a literal/f-string nor a variable "
                "this resolver can trace. Either simplify the call so the SQL is "
                "readable statically, or teach `_literal_text`/`_find_forwarders` the "
                "new shape. A statement this test cannot see is a statement the "
                "tenant-scoping guarantee no longer covers.")
        out.append((node.lineno, text))
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
        found = {p.relative_to(RTF_PLATFORM) for p in RTF_PLATFORM.rglob("*.py")}
        self.assertIn(Path("agents.py"), found)
        self.assertIn(Path("fleet.py"), found)
        self.assertIn(Path("distributors/base.py"), found,
                      "rglob must recurse into subpackages, not just the top level")

    def test_every_tenant_scoped_statement_carries_tenant_id(self) -> None:
        used_allowlist_entries: set[str] = set()
        failures: list[str] = []
        checked = 0

        for path in sorted(RTF_PLATFORM.rglob("*.py")):
            for lineno, sql in _extract_statements(path):
                checked += 1
                for unit, keyword, table in _violations(sql):
                    key = _normalise(unit)
                    if key in ALLOWLIST:
                        used_allowlist_entries.add(key)
                        continue
                    rel = path.relative_to(RTF_PLATFORM)
                    failures.append(
                        f"{rel}:{lineno} {keyword} {table} has no tenant_id predicate "
                        f"in its governing clause:\n    {key[:200]}")

        # A change to `rtf_platform/` that stops finding any `.execute()` call at all
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
