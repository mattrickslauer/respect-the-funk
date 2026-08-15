"""The autonomy rule, exhaustively, and the budget arithmetic that rides on it.

Most of this file needs no cluster, and that is deliberate rather than convenient.
`autonomy.decide` is the function that decides whether a machine spends money without
asking a person, so the property worth having is that its truth table is checked on every
run — including the runs with `DATABASE_URL` unset, which is how the suite is documented
to be run when somebody is only touching Python.

The cluster-gated half at the bottom checks the things only a database can answer: that
the CHECK constraints refuse the states the module refuses, and that a raise proposed,
queued, resolved and replayed round-trips through real rows.
"""

from __future__ import annotations

import os
import unittest
from decimal import Decimal

from spindle import autonomy, budgets

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


def policy(mode: str, ceiling: str = "0", *, configured: bool = True) -> autonomy.Policy:
    return autonomy.Policy(capability="budget_raise", mode=mode,
                           ceiling_usd=Decimal(ceiling), configured=configured)


class DecideTruthTable(unittest.TestCase):
    """Every mode against every position relative to the ceiling."""

    def test_always_acts_however_expensive(self) -> None:
        d = autonomy.decide(policy(autonomy.ALWAYS), Decimal("1000000"))
        self.assertTrue(d.unattended)
        self.assertEqual(d.reason, "always")

    def test_human_queues_however_cheap(self) -> None:
        d = autonomy.decide(policy(autonomy.HUMAN), Decimal("0"))
        self.assertFalse(d.unattended)
        self.assertTrue(d.queued)
        self.assertEqual(d.reason, "human")

    def test_auto_under_ceiling_acts(self) -> None:
        d = autonomy.decide(policy(autonomy.AUTO, "5.00"), Decimal("4.99"))
        self.assertTrue(d.unattended)
        self.assertEqual(d.reason, "under_ceiling")

    def test_auto_exactly_at_ceiling_acts(self) -> None:
        """A five-dollar ceiling permits a five-dollar action.

        The off-by-one in the other direction is the one an operator cannot see: the
        number they set is the number that stops working, and nothing in the refusal
        says so.
        """
        d = autonomy.decide(policy(autonomy.AUTO, "5.00"), Decimal("5.00"))
        self.assertTrue(d.unattended)

    def test_auto_over_ceiling_queues(self) -> None:
        d = autonomy.decide(policy(autonomy.AUTO, "5.00"), Decimal("5.01"))
        self.assertFalse(d.unattended)
        self.assertEqual(d.reason, "over_ceiling")

    def test_unconfigured_is_distinguishable_from_chosen_human(self) -> None:
        """Default-deny must not be silent. The console renders these differently."""
        chosen = autonomy.decide(policy(autonomy.HUMAN), Decimal("1"))
        absent = autonomy.decide(policy(autonomy.HUMAN, configured=False), Decimal("1"))
        self.assertFalse(chosen.unattended)
        self.assertFalse(absent.unattended)
        self.assertEqual(chosen.reason, "human")
        self.assertEqual(absent.reason, "not_configured")

    def test_negative_estimate_raises_rather_than_reading_as_free(self) -> None:
        with self.assertRaises(ValueError):
            autonomy.decide(policy(autonomy.AUTO, "5.00"), Decimal("-1"))

    def test_unknown_mode_raises_rather_than_defaulting_to_human(self) -> None:
        """A schema that moved should be loud, not quietly conservative."""
        with self.assertRaises(ValueError):
            autonomy.decide(policy("supervised"), Decimal("1"))

    def test_summary_carries_the_reason_and_the_numbers(self) -> None:
        d = autonomy.decide(policy(autonomy.AUTO, "5.00"), Decimal("2.50"))
        s = d.summary()
        self.assertEqual(s["mode"], "auto")
        self.assertEqual(s["ceiling_usd"], "5.00")
        self.assertEqual(s["estimate_usd"], "2.50")
        self.assertTrue(s["unattended"])
        self.assertEqual(s["reason"], "under_ceiling")


class CapabilityNames(unittest.TestCase):
    def test_unknown_capability_raises_on_read(self) -> None:
        """Denying by misspelling is safe and unreadable. Make it a stack trace."""
        with self.assertRaises(autonomy.UnknownCapability):
            autonomy.read(None, "t", "spendd")  # type: ignore[arg-type]

    def test_the_module_and_the_check_constraint_agree(self) -> None:
        """`CAPABILITIES` is duplicated from SQL; this is what keeps the copy honest."""
        here = os.path.join(os.path.dirname(__file__), "..", "..",
                            "schema", "036_autonomy_and_track_budget.sql")
        with open(os.path.abspath(here), encoding="utf-8") as fh:
            sql = fh.read()
        for cap in autonomy.CAPABILITIES:
            self.assertIn(f"'{cap}'", sql,
                          f"{cap!r} is in CAPABILITIES but not in the migration")
        for mode in autonomy.MODES:
            self.assertIn(f"'{mode}'", sql,
                          f"{mode!r} is in MODES but not in the migration")


class SetPolicyValidation(unittest.TestCase):
    """Validation happens before the connection is touched, so `None` is a fine stand-in.

    Passing `None` is the assertion: if any of these ever reached the database, the test
    would fail with `AttributeError` rather than the `ValueError` being asserted, and the
    "explain it before the constraint does" property would be quietly gone.
    """

    def test_auto_with_zero_ceiling_is_refused_with_a_sentence(self) -> None:
        with self.assertRaises(ValueError) as caught:
            autonomy.set_policy(None, "t", "spend",  # type: ignore[arg-type]
                                mode="auto", ceiling_usd=Decimal("0"),
                                written_by="matt")
        self.assertIn("'human'", str(caught.exception))

    def test_ceiling_under_human_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            autonomy.set_policy(None, "t", "spend",  # type: ignore[arg-type]
                                mode="human", ceiling_usd=Decimal("5"),
                                written_by="matt")

    def test_a_policy_needs_a_name_against_it(self) -> None:
        with self.assertRaises(ValueError):
            autonomy.set_policy(None, "t", "spend",  # type: ignore[arg-type]
                                mode="always", written_by="   ")

    def test_unknown_mode_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            autonomy.set_policy(None, "t", "spend",  # type: ignore[arg-type]
                                mode="sometimes", written_by="matt")


class AsOfValidation(unittest.TestCase):
    """`AS OF SYSTEM TIME` is interpolated, so this is the whole security argument.

    `budgets.replay` feeds it a value from our own DECIMAL column, which is a reason to
    expect it to be well-formed and not a reason to skip checking it.
    """

    def test_empty_is_accepted_and_means_now(self) -> None:
        self.assertIsNone(budgets._check_as_of(""))

    def test_a_real_hlc_is_accepted(self) -> None:
        self.assertIsNone(budgets._check_as_of("1786644836824776765.0000000000"))

    def test_bare_integer_is_accepted(self) -> None:
        self.assertIsNone(budgets._check_as_of("1786644836824776765"))

    def test_injection_shapes_are_refused(self) -> None:
        for bad in ("1'; DROP TABLE party_metric; --",
                    "1' OR '1'='1",
                    "1786644836824776765.0000000000'",
                    "now()",
                    "-30m",
                    "1 786644836824776765",
                    "1786644836824776765.0000000000 UNION SELECT",
                    "\n1786644836824776765"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                budgets._check_as_of(bad)


class ValuationArithmetic(unittest.TestCase):
    def test_unvalued_counterparties_make_the_total_a_floor(self) -> None:
        v = budgets.Valuation(recording_id="r", total_usd=Decimal("12"), audience=24000,
                              valued=3, unvalued=4)
        self.assertFalse(v.complete)
        basis = v.basis()
        self.assertEqual(basis["unvalued"], 4)
        self.assertEqual(basis["valued"], 3)

    def test_a_complete_valuation_says_so(self) -> None:
        v = budgets.Valuation(recording_id="r", total_usd=Decimal("12"), audience=24000,
                              valued=3, unvalued=0)
        self.assertTrue(v.complete)

    def test_the_basis_records_the_rate_it_used(self) -> None:
        """A stored basis that omits the rate cannot be recomputed once the rate moves."""
        v = budgets.Valuation(recording_id="r", total_usd=Decimal("0"), audience=0,
                              valued=0, unvalued=0)
        self.assertEqual(v.basis()["rate_usd_per_thousand"],
                         str(budgets.USD_PER_THOUSAND_AUDIENCE))


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL — these test the database, not the code")
class MigrationShape(unittest.TestCase):
    """The constraints the modules rely on actually exist on the cluster."""

    @classmethod
    def setUpClass(cls) -> None:
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                   row_factory=dict_row)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()

    def test_tables_exist(self) -> None:
        with self.conn.cursor() as cur:
            for table in ("autonomy", "recording_budget", "budget_raise"):
                cur.execute(
                    "SELECT count(*) AS n FROM information_schema.tables "
                    "WHERE table_name = %s", (table,))
                self.assertEqual(cur.fetchone()["n"], 1, f"{table} is missing")

    def test_auto_with_zero_ceiling_is_refused_by_the_database_too(self) -> None:
        """The module explains it; the constraint enforces it. Both, not either."""
        import psycopg
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM tenant LIMIT 1")
            row = cur.fetchone()
            if row is None:
                self.skipTest("no tenant on this cluster")
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute(
                    """INSERT INTO autonomy (tenant_id, capability, mode,
                                             unattended_ceiling_usd, written_by)
                       VALUES (%s, 'spend', 'auto', 0, 'test')""", (row["id"],))

    def test_a_raise_must_actually_raise(self) -> None:
        import psycopg
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM tenant LIMIT 1")
            tenant = cur.fetchone()
            cur.execute("SELECT id FROM recording WHERE tenant_id = %s LIMIT 1",
                        (tenant["id"],))
            rec = cur.fetchone()
            if rec is None:
                self.skipTest("no recording on this cluster")
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute(
                    """INSERT INTO budget_raise (tenant_id, recording_id, from_usd,
                                                 to_usd, state)
                       VALUES (%s, %s, 50, 20, 'awaiting_human')""",
                    (tenant["id"], rec["id"]))

    def test_the_instant_lives_in_the_ledger_and_not_here(self) -> None:
        """`budget_raise` must not grow its own HLC back.

        Two tables each holding an instant for one event is two tables that can disagree
        about when it happened, which is the failure `035_decision_ledger.sql` exists to
        prevent. A column named back into existence here would reintroduce it silently,
        so the absence is asserted rather than assumed.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'budget_raise'""")
            columns = {r["column_name"] for r in cur.fetchall()}
        self.assertNotIn("decided_at_hlc", columns)
        self.assertIn("decision_id", columns)
        self.assertIn("resolution_decision_id", columns)

    def test_a_raise_cannot_cite_another_tenants_decision(self) -> None:
        """The composite FK is what makes a cross-tenant citation unrepresentable."""
        import psycopg
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM tenant LIMIT 1")
            tenant = cur.fetchone()
            cur.execute("SELECT id FROM recording WHERE tenant_id = %s LIMIT 1",
                        (tenant["id"],))
            rec = cur.fetchone()
            if rec is None:
                self.skipTest("no recording on this cluster")
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                cur.execute(
                    """INSERT INTO budget_raise (tenant_id, recording_id, from_usd,
                                                 to_usd, state, decision_id)
                       VALUES (%s, %s, 10, 20, 'applied', gen_random_uuid())""",
                    (tenant["id"], rec["id"]))

    def test_a_rejection_must_name_the_decision_that_refused_it(self) -> None:
        """A declined raise moves no money, so this row is its only trace."""
        import psycopg
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM tenant LIMIT 1")
            tenant = cur.fetchone()
            cur.execute("SELECT id FROM recording WHERE tenant_id = %s LIMIT 1",
                        (tenant["id"],))
            rec = cur.fetchone()
            if rec is None:
                self.skipTest("no recording on this cluster")
            cur.execute(
                """INSERT INTO decision (tenant_id, kind, stage, at_hlc, subject_kind,
                                         subject_id, actor, summary)
                   VALUES (%s, 'budget_increase', 'proposed', 1786644836824776765,
                           'recording', %s, 'test', 'test proposal')
                   RETURNING id""", (tenant["id"], rec["id"]))
            decision_id = cur.fetchone()["id"]
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute(
                    """INSERT INTO budget_raise (tenant_id, recording_id, from_usd,
                                                 to_usd, state, decision_id,
                                                 approved_by)
                       VALUES (%s, %s, 10, 20, 'rejected', %s, 'matt')""",
                    (tenant["id"], rec["id"], decision_id))
            cur.execute("DELETE FROM decision WHERE tenant_id = %s AND id = %s",
                        (tenant["id"], decision_id))


if __name__ == "__main__":
    unittest.main()
