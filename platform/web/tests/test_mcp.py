"""The Ask screen's safety argument, proved rather than asserted in `mcp.py`'s docstring.

    cd platform/web && .venv/bin/python -m pytest tests/test_mcp.py -q

`mcp.py` routes an operator's English question to CockroachDB's Cloud Managed MCP Server.
That server, measured 2026-08-13, runs whatever SQL it is handed with the API key's
privileges and has no tenant concept whatsoever — `SELECT tenant_id, count(*) FROM party
GROUP BY tenant_id` returns every tenant's rows, and `insert_rows` sits on the same
endpoint. So every isolation guarantee lives on this side of the wire, in a module that
`test_tenant_scoping.py` **cannot see**: that lint statically resolves the SQL passed to
`cursor.execute(...)`, and `mcp.py` has no `cursor.execute` call at all. Its statements
travel over HTTPS to a third party.

**This file is the cover for that blind spot**, and it is the reason `mcp.py` may exist.
Three properties are checked and each corresponds to a way the design could fail quietly:

  * **Every statement in `mcp.QUESTIONS` is a scoped, single read.** Not "the ones that
    happen to be exercised" — the whole catalogue, rendered with each of its slots, run
    through the same guard the production path uses. A question added tomorrow without a
    `tenant_id` predicate fails here.
  * **The guard refuses what it claims to refuse.** Every refusal branch is driven with
    a statement crafted to hit exactly it, because a guard whose failure paths are never
    executed is a guard nobody knows is wired up. The canonical case is the one
    `test_tenant_scoping`'s own docstring records: a `JOIN` whose `ON` clause carries no
    `tenant_id`, in a statement whose `WHERE` does — which a whole-statement substring
    search would wave through.
  * **No model output ever becomes SQL.** The classifier's every pathological answer —
    an unknown key, prose instead of JSON, a slot out of range, a term with a quote in it
    — ends as a refusal, never as a statement.

## Nothing here touches a network or spends a cent

Every test injects a `transport` callable. `mcp._post_json` hands the call to it and
returns before constructing a `urllib.request.Request`, so no test in this file can reach
`api.openai.com` or `cockroachlabs.cloud` even if the environment is fully configured.
The spend gate is driven with an explicit `Policy` rather than the ambient environment,
for the reason `test_sender.py` gives: a developer with `RTF_PAID_ENABLED=1` in their
shell must not get a different result from CI.
"""

from __future__ import annotations

import json
import re
import unittest
from decimal import Decimal
from unittest import mock

from spindle import mcp, spend

#: A tenant id to pin statements to. Any UUID works; this one is deliberately *not* the
#: production tenant, so a test that somehow reached the cluster would ask about nothing.
TENANT = "00000000-0000-4000-8000-000000000001"
OTHER_TENANT = "00000000-0000-4000-8000-00000000beef"


def _gate(*, enabled: bool = True, ceiling: str = "1.00") -> spend.Gate:
    """A gate with a policy set explicitly, never read from the developer's shell."""
    return spend.Gate(
        policy=spend.Policy(paid_enabled=enabled,
                            daily_ceiling_usd=Decimal(ceiling),
                            per_call_ceiling_usd=Decimal("0.05"),
                            dry_run=False),
        already_spent_usd=Decimal("0"), refused=[])


def _router_reply(decision: dict, *, prompt_tokens: int = 700,
                  completion_tokens: int = 20) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(decision)}}],
        "usage": {"prompt_tokens": prompt_tokens,
                  "completion_tokens": completion_tokens},
    }


class Transport:
    """A stand-in for the network that records what it was asked and answers in the two
    shapes the real endpoints use.

    It asserts on the URL rather than answering everything the same way, because a test
    whose fake would happily serve an MCP handshake to OpenAI proves less than it looks
    like it does.
    """

    def __init__(self, *, decision: dict | None = None,
                 rows: list[dict] | None = None,
                 router_payload: dict | None = None,
                 select_result: dict | None = None) -> None:
        self.decision = decision if decision is not None else {"key": "counterparties_by_role"}
        self.rows = rows if rows is not None else [{"role": "curator", "how_many": 25}]
        self.router_payload = router_payload
        self.select_result = select_result
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, body: dict, headers: dict):
        self.calls.append((url, body))
        if url == mcp.OPENAI_URL:
            assert headers["Authorization"].startswith("Bearer ")
            return self.router_payload or _router_reply(self.decision)

        assert url.endswith("/mcp"), url
        method = body.get("method")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {}, "_session": "SESSION1"}
        if method == "notifications/initialized":
            return {}
        assert method == "tools/call", method
        # The one tool this client is ever allowed to name.
        assert body["params"]["name"] == "select_query"
        if self.select_result is not None:
            return self.select_result
        payload = json.dumps({"rows": self.rows})
        return {"jsonrpc": "2.0", "id": 1,
                "result": {"content": [{"type": "text", "text": payload}]}}

    @property
    def sql(self) -> str:
        for url, body in self.calls:
            if body.get("method") == "tools/call":
                return body["params"]["arguments"]["query"]
        raise AssertionError("no select_query was sent")


# ------------------------------------------------------- the catalogue is safe as written

class TheCatalogue(unittest.TestCase):

    def test_every_question_renders_to_a_scoped_single_read(self) -> None:
        """The property `test_tenant_scoping.py` proves for the rest of the codebase, for
        the statements it structurally cannot see.

        Rendered through `mcp.render`, which is the same call the production path makes —
        not a re-implementation of it, because a test that checks the templates with its
        own copy of the guard proves the copy works and says nothing about the guard.
        """
        for question in mcp.QUESTIONS:
            with self.subTest(question=question.key):
                sql = mcp.render(question, TENANT, {"limit": 10, "term": "jazz"})
                self.assertIn(TENANT, sql,
                              "every statement must pin tenant_id to the resolved tenant")
                self.assertTrue(sql.upper().startswith("SELECT"))
                self.assertNotIn(";", sql)
                # Re-running the guard is not redundant: `render` could stop calling it.
                mcp._refuse_if_unscoped(sql, TENANT)

    def test_every_question_declares_the_slots_its_sql_uses(self) -> None:
        """A slot in the SQL that is not in `slots` renders as a `KeyError` at request
        time; a slot in `slots` that the SQL ignores means the classifier is being asked
        for a value that changes nothing, and the operator is shown a limit that does not
        limit."""
        for question in mcp.QUESTIONS:
            with self.subTest(question=question.key):
                used = set(re.findall(r"\{(\w+)\}", question.sql)) - {"tenant"}
                self.assertEqual(used, set(question.slots))
                self.assertIn("{tenant}", question.sql)

    def test_question_keys_are_unique_and_described(self) -> None:
        """`BY_KEY` silently drops a duplicate, and the classifier is prompted from
        `QUESTIONS` — so two questions sharing a key would offer the operator a choice
        that can only ever resolve to one of them."""
        keys = [q.key for q in mcp.QUESTIONS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(mcp.BY_KEY), len(keys))
        for question in mcp.QUESTIONS:
            self.assertTrue(question.asks.strip().endswith("?"),
                            "the classifier matches English questions, so `asks` must "
                            "read as one")

    def test_the_scoped_table_list_agrees_with_the_lint(self) -> None:
        """`mcp.TENANT_SCOPED_TABLES` is a hand copy of `test_tenant_scoping`'s list —
        see the comment above it for why importing across the boundary was rejected. A
        copy nobody compares is a copy that drifts, and the direction that matters is a
        table added to the schema and the lint but not here: this guard would then wave
        through an unscoped read of the newest table in the system."""
        from tests import test_tenant_scoping

        self.assertEqual(set(mcp.TENANT_SCOPED_TABLES),
                         set(test_tenant_scoping.TENANT_SCOPED_TABLES))


# ---------------------------------------------------------------- the guard actually bites

class TheGuard(unittest.TestCase):

    def _refused(self, sql: str, tenant: str = TENANT) -> str:
        with self.assertRaises(mcp.QuestionRefused) as caught:
            mcp._refuse_if_unscoped(" ".join(sql.split()), tenant)
        return str(caught.exception)

    def test_a_join_cannot_borrow_the_primary_tables_scoping(self) -> None:
        """The canonical bug, restated from `test_tenant_scoping.py`'s docstring: the
        outer `WHERE` carries `tenant_id` and the join does not, so a whole-statement
        substring search passes it while the join scans every tenant's rows."""
        message = self._refused(
            f"SELECT p.name FROM party_fact f "
            f"JOIN party p ON p.id = f.party_id "
            f"WHERE f.tenant_id = '{TENANT}'")
        self.assertIn("JOIN party", message)

    def test_a_comma_join_cannot_smuggle_an_unchecked_table_reference(self) -> None:
        """The hole the per-reference loop cannot see on its own.

        `FROM party p, party_role r` introduces `party_role` with no keyword in front of
        it, so `_REF_RE` never matches it, so it is neither checked nor complained about —
        silence that reads exactly like approval while that table scans every tenant's
        rows. Counting mentions against checked references is what turns the silence into
        a refusal.
        """
        message = self._refused(
            f"SELECT p.name FROM party p, party_role r "
            f"WHERE p.tenant_id = '{TENANT}' AND r.party_id = p.id")
        self.assertIn("comma-join", message)

    def test_an_unscoped_read_is_refused(self) -> None:
        self.assertIn("does not pin", self._refused("SELECT name FROM party LIMIT 5"))

    def test_a_tenant_id_that_is_not_an_equality_scopes_nothing(self) -> None:
        """`tenant_id IS NOT NULL` mentions the column and constrains nothing — the exact
        distinction `_has_tenant_equality` was written for upstream."""
        self._refused(f"SELECT name FROM party WHERE tenant_id IS NOT NULL "
                      f"AND slug = '{TENANT}'")

    def test_another_tenants_uuid_is_refused_even_where_the_shape_is_right(self) -> None:
        """The check a shape-only guard misses: this statement has a `tenant_id`
        equality in exactly the right clause and reads somebody else's rows."""
        message = self._refused(
            f"SELECT name FROM party WHERE tenant_id = '{OTHER_TENANT}'")
        self.assertIn("not this tenant", message)

    def test_writes_and_set_operations_are_refused(self) -> None:
        for sql in (
            f"INSERT INTO party (tenant_id, name) VALUES ('{TENANT}', 'x')",
            f"SELECT name FROM party WHERE tenant_id = '{TENANT}'; DROP TABLE party",
            f"SELECT name FROM party WHERE tenant_id = '{TENANT}' "
            f"UNION SELECT name FROM party",
            f"SET allow_unsafe_internals = true",
        ):
            with self.subTest(sql=sql[:48]):
                self._refused(sql)

    def test_a_comment_is_refused_rather_than_analysed(self) -> None:
        """A comment can hide a clause boundary from the regexes above, so the honest
        answer is that this analysis does not apply — not that it passed."""
        message = self._refused(
            f"SELECT name FROM party WHERE tenant_id = '{TENANT}' -- AND x")
        self.assertIn("comment", message)

    def test_a_non_uuid_tenant_is_never_interpolated(self) -> None:
        with self.assertRaises(mcp.QuestionRefused):
            mcp.render(mcp.BY_KEY["counterparties_by_role"], "not-a-uuid", {})


# ------------------------------------------------------------------------- slot handling

class Slots(unittest.TestCase):

    def test_a_term_with_a_quote_is_refused_not_escaped(self) -> None:
        """The `term` alphabet contains no quote, so a rendered `ILIKE '%…%'` literal
        cannot be terminated from inside. Refusing rather than stripping matters
        independently: a silently-cleaned term answers a question nobody asked."""
        for bad in ("jazz'; DROP TABLE party --", "a\\b", "x" * 61, "", "semi;colon"):
            with self.subTest(term=bad[:24]):
                with self.assertRaises(mcp.QuestionRefused):
                    mcp.render(mcp.BY_KEY["stations_playing"], TENANT,
                               {"term": bad, "limit": 5})

    def test_a_limit_out_of_range_is_refused_not_clamped(self) -> None:
        # `None` is deliberately absent from this list: it means "no number was named"
        # and takes `DEFAULT_LIMIT`, which the two tests below pin down separately.
        for bad in (0, -1, mcp.LIMIT_MAX + 1, "many"):
            with self.subTest(limit=bad):
                with self.assertRaises(mcp.QuestionRefused):
                    mcp.render(mcp.BY_KEY["genre_spread"], TENANT, {"limit": bad})

    def test_an_explicit_null_limit_means_the_operator_named_no_number(self) -> None:
        """A real bug, caught against the live API on 2026-08-13 and kept as a test.

        Asked "what is the discovery queue doing?", `gpt-4o-mini` replies
        `{"key": "discovery_queue", "limit": null}` — the key is *present* and null, so
        `slots.get("limit", DEFAULT_LIMIT)` returned `None` and every question that named
        no number was refused as "not a whole number". Absent and null are the same
        answer and must be treated as one.
        """
        sql = mcp.render(mcp.BY_KEY["discovery_queue"], TENANT, {"limit": None})
        self.assertIn(f"LIMIT {mcp.DEFAULT_LIMIT}", sql)
        self.assertEqual(
            sql, mcp.render(mcp.BY_KEY["discovery_queue"], TENANT, {}),
            "an omitted limit and an explicit null must render identically")

    def test_a_named_zero_is_still_refused_rather_than_defaulted(self) -> None:
        """The other half of the fix above. `None` means "unspecified" and takes the
        default; `0` is a number the operator named and is out of range. An `or` would
        have collapsed the two and quietly answered a 0-row question with 25 rows."""
        with self.assertRaises(mcp.QuestionRefused):
            mcp.render(mcp.BY_KEY["discovery_queue"], TENANT, {"limit": 0})

    def test_a_term_is_never_defaulted(self) -> None:
        """Unlike `limit`, a missing search term has no sensible stand-in: guessing one
        would answer a different question and show a statement that looks deliberate."""
        with self.assertRaises(mcp.QuestionRefused):
            mcp.render(mcp.BY_KEY["stations_playing"], TENANT, {"limit": 5})

    def test_an_ordinary_term_survives_intact(self) -> None:
        sql = mcp.render(mcp.BY_KEY["stations_playing"], TENANT,
                         {"term": "hip-hop", "limit": 7})
        self.assertIn("'%hip-hop%'", sql)
        self.assertIn("LIMIT 7", sql)


# -------------------------------------------------------------- the classifier is bounded

class Classifier(unittest.TestCase):

    def test_an_unknown_key_never_becomes_a_statement(self) -> None:
        transport = Transport(decision={"key": "SELECT * FROM party"})
        with self.assertRaises(mcp.QuestionRefused) as caught:
            mcp.ask("anything", TENANT, _gate(), server=_server(), api_key="k",
                    transport=transport)
        self.assertIn("not one of this console's questions", str(caught.exception))
        self.assertEqual([b.get("method") for _, b in transport.calls], [None],
                         "nothing may be sent to the MCP server after a bad key")

    def test_prose_instead_of_json_is_a_refusal(self) -> None:
        transport = Transport(router_payload={
            "choices": [{"message": {"content": "I think you want the roster."}}],
            "usage": {"prompt_tokens": 700, "completion_tokens": 8},
        })
        with self.assertRaises(mcp.QuestionRefused):
            mcp.ask("anything", TENANT, _gate(), server=_server(), api_key="k",
                    transport=transport)

    def test_a_declared_refusal_is_passed_through_with_its_reason(self) -> None:
        """The model saying "I cannot place this" must reach the operator as that, not
        as a wrong answer to a question they did not ask."""
        transport = Transport(decision={"key": None, "why": "You asked about payouts."})
        with self.assertRaises(mcp.QuestionRefused) as caught:
            mcp.ask("how much did we earn?", TENANT, _gate(), server=_server(),
                    api_key="k", transport=transport)
        self.assertIn("payouts", str(caught.exception))

    def test_the_prompt_lists_every_question(self) -> None:
        """Built from `QUESTIONS` so a question added below cannot be unroutable — the
        failure where the page grows a capability nothing can reach."""
        prompt = mcp._router_prompt()
        for question in mcp.QUESTIONS:
            self.assertIn(question.key, prompt)
            self.assertIn(question.asks, prompt)

    def test_an_empty_or_enormous_question_is_refused_before_it_is_billed(self) -> None:
        gate = _gate()
        for text in ("", "   ", "x" * 501):
            with self.subTest(text=text[:12]):
                with self.assertRaises(mcp.QuestionRefused):
                    mcp.classify(text, gate, api_key="k",
                                 transport=Transport())
        self.assertEqual(gate.incurred_usd, Decimal("0"))


# ------------------------------------------------------------------ spend, and the wire

def _server() -> mcp.Server:
    return mcp.Server(url="https://cockroachlabs.cloud/mcp", cluster_id="cluster",
                      api_key="key", database="defaultdb")


class SpendAndTransport(unittest.TestCase):

    def test_paid_calls_off_means_no_question_is_asked_at_all(self) -> None:
        """The kill switch has to reach this page, or `RTF_PAID_ENABLED=0` is a setting
        that stops the agents and quietly leaves an LLM endpoint open on the console."""
        transport = Transport()
        with self.assertRaises(spend.SpendRefused) as caught:
            mcp.ask("how many curators?", TENANT, _gate(enabled=False),
                    server=_server(), api_key="k", transport=transport)
        self.assertIn("RTF_PAID_ENABLED", str(caught.exception))
        self.assertEqual(transport.calls, [],
                         "the gate must refuse before the network, not after")

    def test_the_recorded_cost_comes_from_reported_usage_not_the_estimate(self) -> None:
        """`spend.Gate.record`'s rule: an estimate is not a charge. The estimate is
        deliberately far from the reported usage here so the two cannot be confused."""
        transport = Transport(router_payload=_router_reply(
            {"key": "counterparties_by_role"}, prompt_tokens=1000,
            completion_tokens=10))
        gate = _gate()
        answer = mcp.ask("who do we know?", TENANT, gate, server=_server(),
                         api_key="k", transport=transport)
        expected = spend.estimate(mcp.ROUTER_RATE_KEY, tokens_in=1000, tokens_out=10)
        self.assertEqual(gate.incurred_usd, expected)
        self.assertEqual(answer.cost_usd, expected)

    def test_a_whole_question_reaches_select_query_and_comes_back_as_rows(self) -> None:
        transport = Transport(decision={"key": "stations_playing", "term": "soul",
                                        "limit": 3},
                              rows=[{"station": "WXYZ", "genre": "soul"}])
        answer = mcp.ask("which stations play soul?", TENANT, _gate(),
                         server=_server(), api_key="k", transport=transport)
        self.assertEqual(answer.question.key, "stations_playing")
        self.assertEqual(answer.columns, ["station", "genre"])
        self.assertEqual(answer.rows, [{"station": "WXYZ", "genre": "soul"}])
        self.assertIn("'%soul%'", answer.sql)
        self.assertIn(TENANT, answer.sql)

    def test_the_handshake_happens_before_the_query_and_carries_the_session(self) -> None:
        """Streamable-HTTP MCP is a session protocol; a `tools/call` without the
        `initialize`/`initialized` pair is refused by the real server, so a client that
        skipped them would work only against a fake."""
        transport = Transport()
        mcp.ask("who do we know?", TENANT, _gate(), server=_server(), api_key="k",
                transport=transport)
        methods = [b.get("method") for _, b in transport.calls]
        self.assertEqual(methods, [None, "initialize", "notifications/initialized",
                                   "tools/call"])

    def test_a_jsonrpc_error_in_a_200_is_still_an_error(self) -> None:
        """The HTTP status is not this protocol's error channel. An expired key can
        arrive as a successful 200 whose payload refuses the call."""
        transport = Transport(select_result={
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32602, "message": "permission denied"}})
        with self.assertRaises(mcp.McpRefused) as caught:
            mcp.ask("who do we know?", TENANT, _gate(), server=_server(),
                    api_key="k", transport=transport)
        self.assertIn("permission denied", str(caught.exception))

    def test_an_unreadable_answer_raises_rather_than_rendering_as_no_rows(self) -> None:
        """The failure this module must never have: a server that answered with
        something unexpected reported to the operator as an empty catalogue."""
        for result in (
            {"result": {"content": [{"type": "text", "text": "not json"}]}},
            {"result": {"content": [{"type": "text", "text": '{"data": []}'}]}},
        ):
            with self.subTest(result=str(result)[:40]):
                with self.assertRaises(mcp.McpRefused):
                    mcp.ask("who do we know?", TENANT, _gate(), server=_server(),
                            api_key="k", transport=Transport(select_result=result))

    def test_an_sse_framed_body_is_understood(self) -> None:
        """The managed server answers a plain POST with `text/event-stream` — measured
        2026-08-13. A client that only knew `json.loads` would report a working server
        as broken."""
        payload = {"jsonrpc": "2.0", "id": 1, "result": {}}
        self.assertEqual(
            mcp._parse_body(f"event: message\ndata: {json.dumps(payload)}\n\n", "x"),
            payload)

    def test_an_empty_result_is_reported_as_no_rows_not_as_a_failure(self) -> None:
        answer = mcp.ask("who do we know?", TENANT, _gate(), server=_server(),
                         api_key="k", transport=Transport(rows=[]))
        self.assertEqual(answer.rows, [])
        self.assertEqual(answer.columns, [])


# ----------------------------------------------------------------------- configuration

class Configuration(unittest.TestCase):

    def test_every_missing_variable_is_named(self) -> None:
        """"Not configured" is not actionable, and this screen needs four things from
        three places."""
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(mcp.McpNotConfigured) as caught:
                mcp.load()
        message = str(caught.exception)
        for name in ("COCKROACH_API_KEY", "COCKROACH_CLUSTER_ID", "OPENAI_API_KEY",
                     "DATABASE_URL"):
            self.assertIn(name, message)
        self.assertIn("ccloud auth login", message,
                      "the message must say where the key comes from, because the "
                      "answer is not obvious and the CLI has already minted one")

    def test_the_database_name_comes_from_database_url(self) -> None:
        """One fact, one setting. A separate `MCP_DATABASE` variable would let the MCP
        page answer confidently about a database the rest of the console is not reading.
        """
        self.assertEqual(
            mcp._database_name(
                "postgresql://u:p@host.cockroachlabs.cloud:26257/defaultdb"
                "?sslmode=verify-full"),
            "defaultdb")

    def test_a_url_with_no_database_raises_rather_than_guessing_defaultdb(self) -> None:
        for url in ("postgresql://u:p@host:26257", "postgresql://u:p@host:26257/"):
            with self.subTest(url=url):
                with self.assertRaises(mcp.McpNotConfigured):
                    mcp._database_name(url)

    def test_load_builds_a_server_when_everything_is_present(self) -> None:
        env = {
            "COCKROACH_API_KEY": "CCDB1_x", "COCKROACH_CLUSTER_ID": "cluster-uuid",
            "OPENAI_API_KEY": "sk-x",
            "DATABASE_URL": "postgresql://u:p@host:26257/defaultdb",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            server, api_key = mcp.load()
        self.assertEqual(server.url, "https://cockroachlabs.cloud/mcp")
        self.assertEqual(server.database, "defaultdb")
        self.assertEqual(api_key, "sk-x")


# ------------------------------------------------------------------- the screen renders

class TheScreen(unittest.TestCase):
    """`console/ask.html` in each of the four states a route can put it in.

    Jinja resolves nothing until render, so a typo in a block name or an attribute that
    does not exist is invisible to every other test in this suite and surfaces as a 500
    the first time an operator opens the page. Nothing else in `tests/` drives a
    template; this class exists because this screen has more states than any other — it
    is the only one that can be unconfigured, refused, empty *and* answered — and the
    two that matter most are the two nobody clicks through by accident.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        from spindle import demo

        root = Path(__file__).resolve().parents[1] / "spindle" / "templates"
        env = Jinja2Templates(directory=str(root)).env
        env.globals["chip_tone"] = lambda value: ""
        env.globals["prov_abbr"] = lambda value: str(value)
        cls.template = env.get_template("console/ask.html")
        cls.nav = tuple((group, tuple((k, label, href, "") for k, label, href, _ in items))
                        for group, items in demo.NAV)
        cls.scopes = demo.SCOPES

    def _render(self, **extra: object) -> str:
        base = {
            "request": None, "principal": None, "tenant_slug": "respect-the-funk",
            "type_groups": {}, "default_type": "", "type_labels": {},
            "nav": self.nav, "scopes": self.scopes, "here": "ask", "insp_kicker": "",
            "insp_title": "", "insp_new": None, "live": True, "error": "", "stats": (),
            "questions": mcp.QUESTIONS, "asked": "", "answer": None,
            "mcp_configured": True, "mcp_missing": "",
        }
        return self.template.render(**{**base, **extra})

    def test_the_empty_state_lists_every_question(self) -> None:
        html = self._render()
        for question in mcp.QUESTIONS:
            self.assertIn(question.asks, html)

    def test_an_unconfigured_screen_names_the_variable_and_offers_no_box(self) -> None:
        """A form whose submit is guaranteed to raise is worse than no form."""
        html = self._render(mcp_configured=False,
                            mcp_missing="COCKROACH_API_KEY is unset.")
        self.assertIn("COCKROACH_API_KEY is unset.", html)
        self.assertNotIn("Your question", html)

    def test_a_refusal_keeps_the_question_in_the_box(self) -> None:
        """Losing the typing to report the refusal makes the operator retype a sentence
        in order to rephrase it, which is how a correctable refusal becomes a dead end."""
        html = self._render(asked="what were the payouts?",
                            error="This console cannot answer that yet.")
        self.assertIn("cannot answer that yet", html)
        self.assertIn('value="what were the payouts?"', html)

    def test_an_answer_shows_the_statement_that_produced_it(self) -> None:
        html = self._render(asked="who do we know?", answer=mcp.Answer(
            question=mcp.BY_KEY["counterparties_by_role"],
            sql=f"SELECT r.role FROM party_role r WHERE r.tenant_id = '{TENANT}'",
            columns=["role", "how_many"],
            rows=[{"role": "radio_programmer", "how_many": 14144}],
            cost_usd=Decimal("0.000049")))
        self.assertIn("14144", html)
        self.assertIn("WHERE r.tenant_id", html)
        self.assertIn("0.000049", html)

    def test_an_empty_result_says_it_ran_and_matched_nothing(self) -> None:
        """The distinction the whole module protects, carried to the last inch: a
        statement that ran and matched nothing must not render the same blank space as a
        statement that never ran."""
        html = self._render(asked="who?", answer=mcp.Answer(
            question=mcp.BY_KEY["catalogue_recordings"], sql="SELECT 1", columns=[],
            rows=[], cost_usd=Decimal("0.00005")))
        self.assertIn("matched no rows", html)


if __name__ == "__main__":
    unittest.main()
