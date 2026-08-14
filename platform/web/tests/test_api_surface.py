"""The JSON API's guarantees, proved without a cluster.

`tests/conftest.py` refuses to run the suite against production, so the default
invocation has no database and 225 tests skip themselves. An API whose only tests were
in that 225 would be an API with no tests most of the time. Everything in this file
therefore runs on every invocation, and it covers the three things that are *structural*
rather than behavioural:

  * **the gate is on every route**, walked from the router rather than asserted per
    handler — a test that listed the routes it checked would be a test that silently
    stopped covering the next one somebody added;
  * **the refusal envelope is closed** — no handler can invent a code;
  * **the shaping never invents data** — `None` survives as `null`, numbers survive as
    numbers, and an unknown type raises instead of being stringified into something
    plausible.

The behaviour that genuinely needs rows — a double approve being refused by
`UNIQUE (message_id)`, a second thread being refused by the partial index — is in
`test_api_endpoints.py`, cluster-gated in the pattern this repository already uses.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
import unittest
import uuid
from decimal import Decimal
from pathlib import Path

from fastapi.dependencies.models import Dependant

from rtf_platform import auth, outreach, research
from rtf_platform.api import ROUTERS, actions, api, deps, errors, reads, shapes


def _dependency_calls(dependant: Dependant) -> set:
    """Every callable in a route's dependency tree, flattened.

    Recursive because `Writer` depends on `require_operator` depends on
    `current_principal`, and a check that only looked one level deep would miss exactly
    the composition that makes the write gate imply the read gate.
    """
    found = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            found.add(sub.call)
        found |= _dependency_calls(sub)
    return found


def _routes():
    for router in ROUTERS:
        for route in router.routes:
            yield route


class EveryRouteIsGated(unittest.TestCase):
    """`routes.py`'s argument, applied to this package and then checked.

    The console makes the gate a dependency so that a new route is private by the act of
    annotating its principal — the failure mode of a `_require(...)` call at the top of
    each handler being the one route where somebody forgets. That argument is only worth
    anything if something proves the annotation is actually there, on all of them.
    """

    def test_there_are_routes_to_check(self) -> None:
        # Guards against the whole file passing vacuously if the assembly changes shape
        # and `_routes()` starts yielding nothing.
        self.assertGreaterEqual(len(list(_routes())), 20)

    def test_every_route_requires_an_operator(self) -> None:
        for route in _routes():
            with self.subTest(path=route.path, methods=sorted(route.methods)):
                self.assertIn(
                    deps.require_operator, _dependency_calls(route.dependant),
                    f"{route.path} is not behind require_operator")

    def test_every_write_route_also_requires_a_writer(self) -> None:
        for route in _routes():
            if route.methods <= {"GET", "HEAD", "OPTIONS"}:
                continue
            with self.subTest(path=route.path, methods=sorted(route.methods)):
                self.assertIn(
                    deps.require_writer, _dependency_calls(route.dependant),
                    f"{route.path} writes without require_writer")

    def test_no_read_route_is_a_write_route_by_accident(self) -> None:
        """Every route in `actions` writes and every route in `reads` does not.

        The split is by module, so a handler that ended up in the wrong one would get
        the wrong gate. Checked by method rather than by trusting the filename.
        """
        for route in reads.router.routes:
            with self.subTest(path=route.path):
                self.assertTrue(route.methods <= {"GET", "HEAD", "OPTIONS"},
                                f"{route.path} is in reads.py and is not a read")
        for route in actions.router.routes:
            with self.subTest(path=route.path):
                self.assertEqual({"POST"}, route.methods,
                                 f"{route.path} is in actions.py and is not a POST")

    def test_the_write_gate_implies_the_read_gate(self) -> None:
        """Not a restatement of the two tests above.

        Those assert both dependencies are present. This asserts they cannot come
        apart: `require_writer` reaches `require_operator` through its own signature, so
        there is no way to annotate a handler with the write gate alone.
        """
        writer = Dependant(call=deps.require_writer)
        from fastapi.dependencies.utils import get_dependant

        resolved = get_dependant(path="/", call=deps.require_writer)
        self.assertIn(deps.require_operator, _dependency_calls(resolved))
        self.assertIsNotNone(writer)


class TheGateItself(unittest.TestCase):

    def setUp(self) -> None:
        self._token = deps.SETTINGS.admin_token

    def test_anonymous_is_refused_with_a_code_and_a_sentence(self) -> None:
        with self.assertRaises(errors.Refusal) as caught:
            deps.require_operator(auth.ANONYMOUS)
        refusal = caught.exception
        self.assertEqual(401, refusal.status_code)
        self.assertEqual(errors.NOT_AUTHENTICATED, refusal.code)
        self.assertIn("Sign in", refusal.message)

    def test_a_401_and_not_a_redirect(self) -> None:
        """The one deliberate divergence from the console, pinned.

        `routes.require_operator` raises 303 with a Location so a browser lands on the
        landing page. An API client following that gets a 200 full of markup and reads
        it as success. If somebody ever "fixes" this to match the console, this fails.
        """
        with self.assertRaises(errors.Refusal) as caught:
            deps.require_operator(auth.ANONYMOUS)
        self.assertEqual(401, caught.exception.status_code)
        self.assertNotEqual(303, caught.exception.status_code)

    def test_an_operator_passes(self) -> None:
        principal = auth.Principal(tenant_id=None, subject="operator", authenticated=True)
        self.assertIs(principal, deps.require_operator(principal))
        self.assertIs(principal, deps.require_writer(principal))

    def test_a_principal_that_may_not_write_is_refused_by_the_write_gate(self) -> None:
        """`may_write` is `authenticated` today, so this principal cannot arrive through
        the cookie path. The gate is checked anyway, because the guarantee is the gate
        and not the arithmetic that currently satisfies it."""
        class ReadOnly(auth.Principal):
            @property
            def may_write(self) -> bool:
                return False

        principal = ReadOnly(tenant_id=None, subject="reader", authenticated=True)
        with self.assertRaises(errors.Refusal) as caught:
            deps.require_writer(principal)
        self.assertEqual(403, caught.exception.status_code)
        self.assertEqual(errors.READ_ONLY, caught.exception.code)


class WhereTheCredentialComesFrom(unittest.TestCase):
    """`_token` decides between the cookie and the bearer header."""

    def test_the_cookie_is_used(self) -> None:
        self.assertEqual("abc", deps._token("abc", None))

    def test_a_bearer_header_is_used(self) -> None:
        self.assertEqual("abc", deps._token(None, "Bearer abc"))

    def test_the_scheme_is_case_insensitive(self) -> None:
        self.assertEqual("abc", deps._token(None, "bearer abc"))
        self.assertEqual("abc", deps._token(None, "BEARER abc"))

    def test_the_cookie_wins_when_both_are_sent(self) -> None:
        """The safer credential decides. A stale header alongside a fresh cookie is a
        real situation, and preferring the header would sign somebody out invisibly."""
        self.assertEqual("cookie", deps._token("cookie", "Bearer header"))

    def test_another_scheme_is_not_a_token(self) -> None:
        self.assertIsNone(deps._token(None, "Basic abc"))

    def test_an_empty_bearer_is_not_a_token(self) -> None:
        """`Bearer ` with nothing after it must not become the empty string. `auth`
        treats a falsy token as anonymous, so this is belt and braces — but an empty
        string reaching `hmac.compare_digest` alongside an empty `admin_token` is the
        one shape that could authenticate nobody into somebody."""
        self.assertIsNone(deps._token(None, "Bearer "))
        self.assertIsNone(deps._token(None, "Bearer"))

    def test_nothing_is_nothing(self) -> None:
        self.assertIsNone(deps._token(None, None))
        self.assertIsNone(deps._token("", ""))


class TheRefusalEnvelope(unittest.TestCase):

    def test_every_code_is_declared(self) -> None:
        self.assertIn(errors.ALREADY_QUEUED, errors.CODES)
        self.assertEqual(16, len(errors.CODES))

    def test_the_sentence_is_mirrored_at_the_top_level(self) -> None:
        """`error.message` is canonical; the mirror exists so a client reaching for a
        top-level field finds a string rather than an object. Derived in one place, so
        the two cannot drift."""
        refusal = errors.Refusal(409, errors.ALREADY_QUEUED, "Already queued — …")
        self.assertEqual(refusal.body["message"], refusal.body["error"]["message"])

    def test_the_api_has_exactly_one_error_shape(self) -> None:
        """FastAPI's own validation failures are translated into the same envelope.

        Without the second handler an API client would need two parsers — one for
        `{"error": ...}` and one for the framework's `{"detail": [...]}` — and would
        find out about the second in production.
        """
        from fastapi.exceptions import RequestValidationError

        self.assertIn(RequestValidationError, api.exception_handlers)
        self.assertIs(errors.handle_validation,
                      api.exception_handlers[RequestValidationError])

    def test_a_validation_failure_names_the_field_and_keeps_the_envelope(self) -> None:
        from fastapi.exceptions import RequestValidationError

        exc = RequestValidationError([
            {"type": "missing", "loc": ("body", "counterparty_id"),
             "msg": "Field required", "input": {}}])
        response = errors.handle_validation(None, exc)
        body = json.loads(response.body)
        self.assertEqual(422, response.status_code)
        self.assertEqual(errors.MALFORMED_REQUEST, body["error"]["code"])
        self.assertIn("counterparty_id", body["error"]["message"])
        self.assertIn("counterparty_id", json.dumps(body["error"]["fields"]))
        self.assertNotIn("detail", body)

    def test_a_validation_failure_carrying_an_exception_does_not_become_a_500(self) -> None:
        """A pydantic validation error can carry a `ValueError` in `ctx`, which
        `json.dumps` cannot serialise. Encoding it is what keeps a 422 a 422."""
        from fastapi.exceptions import RequestValidationError

        exc = RequestValidationError([
            {"type": "value_error", "loc": ("body", "x"), "msg": "bad", "input": {},
             "ctx": {"error": ValueError("boom")}}])
        body = json.loads(errors.handle_validation(None, exc).body)
        self.assertEqual(errors.MALFORMED_REQUEST, body["error"]["code"])

    def test_an_undeclared_code_cannot_be_raised(self) -> None:
        """Not a fallback to something generic. An unknown code is a bug in this
        package, and one that produced a plausible-looking refusal would be invisible."""
        with self.assertRaises(ValueError):
            errors.Refusal(409, "made_up_code", "…")

    def test_the_body_carries_both_halves(self) -> None:
        refusal = errors.Refusal(409, errors.ALREADY_QUEUED, "Already queued — …")
        self.assertEqual(
            {"error": {"code": "already_queued", "message": "Already queued — …"},
             "message": "Already queued — …"},
            refusal.body)

    def test_the_handler_is_registered_on_the_api_application(self) -> None:
        """Registered on the sub-application, so the console's 303 contract is
        untouched. If this moves to the parent app, the console breaks."""
        self.assertIn(errors.Refusal, api.exception_handlers)

    def test_every_message_is_a_sentence_and_not_a_code(self) -> None:
        """The character of the product, pinned. A refusal that said `already_queued`
        twice would satisfy the envelope and defeat the point of it."""
        for code in errors.CODES:
            with self.subTest(code=code):
                refusal = errors.Refusal(409, code, "A human sentence, with words.")
                self.assertNotEqual(refusal.code, refusal.message)


class TheApiHoldsNoQueries(unittest.TestCase):
    """The strongest form of passing `test_tenant_scoping`: there is no SQL to scope.

    Every read goes through `research.rows_*` and every write through `outreach`,
    `repo` or `assets`. A statement appearing in this package would be a second
    definition of something one of those already answers, and the two would drift the
    first time one of them learned something. Expressed as a test because a rule in a
    docstring is a rule that lasts until the first inconvenient afternoon.
    """

    #: The one statement in the package, and what it is for. `deps.connection` runs it
    #: to find out whether the cached connection survived the container being frozen —
    #: it reads no table, carries no tenant, and is the liveness check `routes._conn`
    #: makes for the same reason. Named here so the exemption is a decision on the
    #: record rather than a hole in the check.
    LIVENESS = "SELECT 1"

    def test_no_module_in_the_api_package_executes_sql(self) -> None:
        """Parsed, not grepped.

        A substring search over the source was the first version of this test and it
        failed on its own prose — these modules explain *why* they contain no queries,
        using the words UPDATE and SELECT to do it, and `deps.connection`'s liveness
        check is a real `.execute` that should stay. A test that cannot tell a docstring
        from a statement would have been silenced by loosening it, which is how a
        guarantee becomes decoration. So it walks the AST, matching
        `tests/test_tenant_scoping.py`, and exempts exactly one literal.
        """
        package = Path(deps.__file__).parent
        for path in sorted(package.rglob("*.py")):
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
                        continue
                    first = node.args[0] if node.args else None
                    literal = first.value if isinstance(first, ast.Constant) else None
                    self.assertEqual(
                        self.LIVENESS, literal,
                        f"{path.name}:{node.lineno} executes a statement. Queries "
                        f"belong in research.py, so that the console and this API "
                        f"cannot come to ask the database different questions.")


class ScalarsBecomeJson(unittest.TestCase):

    def test_absent_stays_absent(self) -> None:
        """The rule the whole module exists for. The console renders an em-dash; a
        client that received one could not tell it from a value."""
        self.assertIsNone(shapes.scalar(None))

    def test_a_uuid_becomes_a_string(self) -> None:
        value = uuid.uuid4()
        self.assertEqual(str(value), shapes.scalar(value))

    def test_a_decimal_becomes_a_number(self) -> None:
        self.assertEqual(0.42, shapes.scalar(Decimal("0.42")))
        self.assertIsInstance(shapes.scalar(Decimal("0.42")), float)

    def test_a_datetime_keeps_its_zone(self) -> None:
        value = dt.datetime(2026, 8, 13, 14, 22, tzinfo=dt.timezone.utc)
        self.assertEqual("2026-08-13T14:22:00+00:00", shapes.scalar(value))

    def test_a_date_stays_a_day(self) -> None:
        """`released_on` is a day. Giving it a midnight would invent a precision the
        row does not have."""
        self.assertEqual("2026-08-13", shapes.scalar(dt.date(2026, 8, 13)))

    def test_numbers_and_booleans_are_left_alone(self) -> None:
        self.assertIs(True, shapes.scalar(True))
        self.assertEqual(7, shapes.scalar(7))
        self.assertEqual(1.5, shapes.scalar(1.5))

    def test_nested_structures_are_walked(self) -> None:
        value = uuid.uuid4()
        self.assertEqual([str(value)], shapes.scalar([value]))
        self.assertEqual({"a": str(value)}, shapes.scalar({"a": value}))

    def test_an_unknown_type_raises_instead_of_being_stringified(self) -> None:
        """`str()` would always produce something, and the something would look
        plausible in the response and be wrong in a way nobody could see."""
        class Odd:
            pass

        with self.assertRaises(TypeError):
            shapes.scalar(Odd())


class PickRefusesToInventColumns(unittest.TestCase):

    def test_a_missing_column_raises(self) -> None:
        """`row.get(key)` would serialise a typo as `null`, which is legal for most of
        these fields — so the response would be well-formed and silently wrong."""
        with self.assertRaises(KeyError):
            shapes.pick({"a": 1}, "a", "b")

    def test_a_present_null_is_fine(self) -> None:
        self.assertEqual({"a": None}, shapes.pick({"a": None}, "a"))


class TheListEnvelope(unittest.TestCase):

    def test_it_states_the_limit_it_applied(self) -> None:
        """`LIMIT 200` with no marker is a client that believes it has the table and
        has a page."""
        out = shapes.listing([{"x": 1}], lambda r: r)
        self.assertEqual(research.LIMIT, out["limit"])
        self.assertEqual(1, out["returned"])
        self.assertFalse(out["truncated"])

    def test_truncation_is_exact_when_a_total_is_known(self) -> None:
        out = shapes.listing([{"x": 1}], lambda r: r, total=9)
        self.assertTrue(out["truncated"])
        self.assertTrue(out["truncated_is_exact"])
        self.assertEqual(9, out["total"])

    def test_truncation_is_an_upper_bound_otherwise(self) -> None:
        """A table holding exactly the limit reports `true` while having been sent
        whole. Documented, and cheaper than a second count per endpoint."""
        out = shapes.listing([{"x": 1}] * research.LIMIT, lambda r: r)
        self.assertTrue(out["truncated"])
        self.assertFalse(out["truncated_is_exact"])
        self.assertIsNone(out["total"])

    def test_extra_keys_ride_along(self) -> None:
        out = shapes.listing([], lambda r: r, sender_wired=False)
        self.assertIs(False, out["sender_wired"])


class EntityShapes(unittest.TestCase):
    """One fabricated row per shaper. Cheap, and it covers the translations that a
    client would actually feel."""

    def test_a_counterparty_is_searchable_as_a_boolean(self) -> None:
        row = {"id": uuid.uuid4(), "name": "A Curator", "contact_state": "contactable",
               "embedding_model": None, "searchable": False, "platform": None,
               "url": None, "roles": 2, "role_list": "curator, journalist",
               "profile": "  body  "}
        out = shapes.counterparty(row)
        self.assertIs(False, out["searchable"])
        self.assertEqual(["curator", "journalist"], out["role_list"])
        self.assertEqual("body", out["profile"])
        self.assertIsNone(out["embedding_model"])

    def test_a_counterparty_with_no_profile_gets_null_not_an_empty_string(self) -> None:
        row = {"id": uuid.uuid4(), "name": "X", "contact_state": "contactable",
               "embedding_model": None, "searchable": True, "platform": None,
               "url": None, "roles": 0, "role_list": None, "profile": "   "}
        out = shapes.counterparty(row)
        self.assertIsNone(out["profile"])
        self.assertEqual([], out["role_list"])

    def test_a_thread_reports_whether_it_holds_the_counterparty(self) -> None:
        """Not cosmetic: it is whether this row is inside
        `one_open_thread_per_counterparty`, and therefore whether the counterparty is
        unavailable to every other campaign."""
        base = {"id": uuid.uuid4(), "reason": "", "created_at": None,
                "updated_at": None, "closed_at": None, "owner_agent": None,
                "lease_expires_at": None, "attempts": 0, "last_error": "",
                "who": "C", "contact_state": "in_thread", "campaign": "K",
                "channel": "curator", "artist": "A", "track": None, "messages": 0,
                "last_message": None, "queued": 0}
        open_ = shapes.thread({**base, "state": "awaiting_human"})
        self.assertIs(True, open_["holds_counterparty"])
        for closed in outreach.CLOSED:
            with self.subTest(state=closed):
                self.assertIs(False, shapes.thread({**base, "state": closed})["holds_counterparty"])

    def test_thread_progress_matches_the_shared_definition(self) -> None:
        row = {"id": uuid.uuid4(), "state": "sent", "reason": "", "created_at": None,
               "updated_at": None, "closed_at": None, "owner_agent": None,
               "lease_expires_at": None, "attempts": 0, "last_error": "", "who": "C",
               "contact_state": "in_thread", "campaign": "K", "channel": "curator",
               "artist": "A", "track": None, "messages": 0, "last_message": None,
               "queued": 0}
        self.assertEqual(research.thread_progress("sent"), shapes.thread(row)["progress"])

    def test_a_recording_translates_the_consoles_em_dash_back_to_null(self) -> None:
        """`artist_name` is `coalesce`d to an em-dash in SQL shared with the console.
        A client should not have to know that one column carries typography."""
        row = {"id": uuid.uuid4(), "title": "T", "slug": "t", "isrc": None,
               "isrc_raw": None, "released_on": dt.date(2026, 1, 1), "status": "draft",
               "created_at": None, "facts": 0, "leads": 0, "places": 0,
               "artist_name": "—"}
        self.assertIsNone(shapes.recording(row, [], [])["artist_name"])
        self.assertEqual("2026-01-01", shapes.recording(row, [], [])["released_on"])

    def test_a_recording_keeps_a_real_artist_name(self) -> None:
        row = {"id": uuid.uuid4(), "title": "T", "slug": "t", "isrc": None,
               "isrc_raw": None, "released_on": None, "status": "draft",
               "created_at": None, "facts": 0, "leads": 0, "places": 0,
               "artist_name": "Hallow Youth"}
        self.assertEqual("Hallow Youth", shapes.recording(row, [], [])["artist_name"])

    def test_an_unmanifested_agent_has_a_null_manifest(self) -> None:
        """That null is the answer, not a missing value — it is how you find a worker
        nobody declared."""
        out = shapes.agent({"kind": "ingest-cli", "state": "unmanifested",
                            "manifest": None, "has_code": False, "leases_held": 0,
                            "work_waiting": 0, "runs": {"total": 3}})
        self.assertIsNone(out["manifest"])
        self.assertEqual(3, out["runs"]["total"])
        self.assertEqual(0, out["runs"]["failed"])

    def test_money_stays_in_micro_dollars(self) -> None:
        """The schema stores micro so that summing spend never drifts; dividing on the
        way out would throw that away in the last hop."""
        out = shapes.agent({"kind": "k", "state": "idle", "manifest": None,
                            "has_code": True, "leases_held": 0, "work_waiting": 0,
                            "runs": {"cost_micro": 1_234_567}})
        self.assertEqual(1_234_567, out["runs"]["cost_micro_usd"])
        self.assertIsInstance(out["runs"]["cost_micro_usd"], int)

    def test_a_fact_keeps_a_null_confidence_null(self) -> None:
        row = {"id": uuid.uuid4(), "dimension": "genre", "value_text": "funk",
               "provenance": "inferred", "status": "live", "confidence": None,
               "source": None, "written_by": None, "observed_at": None, "model": None,
               "supersedes_id": None, "artist_name": None}
        self.assertIsNone(shapes.fact(row)["confidence"])

    def test_a_lead_score_is_a_number(self) -> None:
        row = {"id": uuid.uuid4(), "kind": "probe", "adapter": "test", "target": "t",
               "depth": 1, "score": Decimal("0.75"), "state": "pending",
               "owner_agent": None, "lease_expires_at": None, "next_action_at": None,
               "attempts": 0, "last_error": "", "cadence_seconds": None,
               "scope_kind": "tenant", "reason": "", "parent_lead_id": None,
               "artist_name": None}
        out = shapes.lead(row)
        self.assertEqual(0.75, out["score"])
        self.assertNotIsInstance(out["score"], str)

    def test_a_reply_keeps_an_unclassified_intent_empty(self) -> None:
        """Mapping `""` to a word would make an unclassified reply indistinguishable
        from one a model classified as unclassifiable."""
        row = {"id": uuid.uuid4(), "thread_id": uuid.uuid4(), "subject": "s",
               "body": "b", "intent": "", "confidence": 0.0, "received_at": None,
               "channel": "email", "thread_state": "replied", "who": "C",
               "artist": "A", "campaign": "K"}
        self.assertEqual("", shapes.reply(row)["intent"])

    def test_a_draft_names_its_basis_as_what_it_could_have_stood_on(self) -> None:
        """The field name is the disclaimer. Nothing records what the drafter actually
        read, and a field called `evidence` would let a client claim otherwise."""
        row = {"message_id": uuid.uuid4(), "thread_id": uuid.uuid4(), "state":
               "awaiting_human", "updated_at": None, "subject": "s", "body": "b",
               "created_at": None, "channel": "email", "idempotency_key": "k",
               "who": "C", "campaign": "K", "campaign_channel": "curator",
               "artist": "A", "track": None, "drafts": 1}
        out = shapes.draft(row, [])
        self.assertIn("could_stand_on", out)
        self.assertNotIn("evidence", out)
        self.assertNotIn("basis", out)


class TheChannelSetMatchesTheSchema(unittest.TestCase):
    """`actions.CHANNELS` is a copy of `campaign_channel_known`, and copies go stale.

    The CHECK is the authority and would refuse a bad channel on its own — but as a
    `CheckViolation` naming a constraint, which tells a client author nothing about what
    the legal values are. The copy exists so the refusal can list them. This test is the
    price of the copy: if somebody adds a channel to the migration, this fails, and the
    API stops silently refusing a value the database would have accepted.
    """

    def _migration(self) -> str:
        root = Path(__file__).resolve().parents[3]
        path = root / "platform" / "schema" / "010_outreach.sql"
        self.assertTrue(path.is_file(), f"{path} is missing — did the schema move?")
        return path.read_text()

    def test_the_copy_still_matches(self) -> None:
        sql = self._migration()
        match = re.search(
            r"campaign_channel_known\s+CHECK\s*\(\s*channel\s+IN\s*\(([^)]*)\)",
            sql, re.IGNORECASE)
        self.assertIsNotNone(match, "could not find campaign_channel_known in 010")
        declared = set(re.findall(r"'([^']+)'", match.group(1)))
        self.assertEqual(declared, set(actions.CHANNELS))


class TheNeedsYouQueue(unittest.TestCase):
    """`/today` shaping, on fabricated items."""

    def _group(self, **over):
        base = {
            "id": "sug-abc", "kind": "suggestion_group", "tone": "act",
            "subject_kind": "party", "subject_id": "abc", "subject_name": "Hallow Youth",
            "head": "2 candidate surfaces for Hallow Youth",
            "sub": "deezer · best match 0.82 · found by search, not asserted",
            "best_confidence": 0.82, "platforms": ["deezer"],
            "suggestions": [{
                "id": uuid.uuid4(), "party_id": uuid.uuid4(),
                "party_name": "Hallow Youth", "party_slug": "hallow-youth",
                "kind": "presence", "confidence": 0.82, "rationale": "name match",
                "payload": {"kind": "presence", "platform": "deezer", "value": "1",
                            "url": "https://deezer.com/artist/1", "label": "HY",
                            "mode": "owned"},
            }],
        }
        return {**base, **over}

    def _parked(self, **over):
        base = {
            "id": "lead-1", "kind": "parked_lead", "tone": "warn",
            "subject_kind": "lead", "subject_id": "1", "subject_name": None,
            "head": "probe parked", "sub": "deezer · 4 attempts · 503",
            "lead": {"id": uuid.uuid4(), "kind": "probe", "platform": "deezer",
                     "attempts": 4, "last_error": "503 from the provider",
                     "party_name": None},
        }
        return {**base, **over}

    def test_a_group_carries_its_reasoning_in_order(self) -> None:
        out = shapes.today_item(self._group())
        labels = [s["label"] for s in out["why"]]
        self.assertEqual("how it was found", labels[0])
        self.assertIn("candidates", labels)
        self.assertIn("best match", labels)

    def test_a_group_marks_its_reasoning_as_inference(self) -> None:
        """A suggestion *is* an inferred match — that is the entire reason a person is
        being asked — so the provenance is recorded rather than invented."""
        out = shapes.today_item(self._group())
        self.assertEqual("inferred", out["why"][0]["provenance"])

    def test_a_parked_lead_claims_no_provenance(self) -> None:
        """`measured`/`inferred`/`asserted` belong to `party_fact` and mean something
        specific about how a claim was arrived at. An attempt count is not a claim."""
        out = shapes.today_item(self._parked())
        for step in out["why"]:
            with self.subTest(label=step["label"]):
                self.assertNotIn("provenance", step)

    def test_a_parked_lead_offers_no_action_it_cannot_perform(self) -> None:
        """`fleet.expedite` is what "run it now" would mean and no endpoint exposes it.
        Listing the action would be offering a control that posts nowhere."""
        self.assertEqual([], shapes.today_item(self._parked())["actions"])

    def test_a_group_offers_per_candidate_actions(self) -> None:
        """The group is one decision, but the acceptable units are the candidates — an
        artist has one page on a service, so accepting is per row."""
        out = shapes.today_item(self._group())
        keys = {a["key"] for a in out["actions"]}
        self.assertEqual({"accept", "reject"}, keys)
        for action in out["actions"]:
            self.assertEqual("candidate", action["per"])

    def test_a_clean_candidate_is_not_refused_in_advance(self) -> None:
        out = shapes.today_item(self._group())
        self.assertIsNone(out["candidates"][0]["refused_because"])

    def test_a_candidate_missing_mode_is_refused_in_advance(self) -> None:
        """The landmine `test_repo` covers from the write side, predicted from the read
        side by the same predicate — so a console can disable Accept and say why."""
        item = self._group()
        payload = dict(item["suggestions"][0]["payload"])
        payload.pop("mode")
        item["suggestions"][0]["payload"] = payload
        out = shapes.today_item(item)
        self.assertIn("mode", out["candidates"][0]["refused_because"])

    def test_a_candidate_of_an_unknown_kind_is_refused_in_advance(self) -> None:
        item = self._group()
        item["suggestions"][0]["payload"] = {"kind": "merge"}
        self.assertIn("merge", shapes.today_item(item)["candidates"][0]["refused_because"])

    def test_the_prediction_agrees_with_the_write_path(self) -> None:
        """The point of `repo.why_unacceptable` being one function.

        `accept_suggestion` raises exactly when this returns non-None (except for the
        no-platform case, which it declines by returning False). Asserted here so the
        two cannot drift into a console that greys out a button the server would have
        honoured, or offers one it would refuse.
        """
        from rtf_platform import repo

        self.assertIsNone(repo.why_unacceptable(
            {"kind": "presence", "platform": "deezer", "value": "1",
             "url": "https://deezer.com/artist/1", "label": "X", "mode": "owned"}))
        self.assertIs(repo.NO_PLATFORM,
                      repo.why_unacceptable({"kind": "presence", "platform": ""}))
        self.assertIn("unlabelled", repo.why_unacceptable({}))
        self.assertIn("unlabelled", repo.why_unacceptable(None))


if __name__ == "__main__":
    unittest.main()
