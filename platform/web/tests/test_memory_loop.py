"""The write half of the memory loop: a closed thread becomes a lesson.

    python -m unittest discover platform/web/tests

`SCOPE-RESET §2a` rule 2 says processes contribute facts and do not only consume them,
and until `agents.distil_lesson` existed this repository broke that rule in the one place
it most claims not to: `lessons.write` was implemented, documented and unit-tested, and
**had no caller anywhere in the codebase**. Every shortlist read `lesson`; nothing ever
wrote it. That is exactly the write-back failure `MEMORY-SPEC §1` diagnosed — *"the
identity is a YAML file that is read and never written back to"* — reproduced by the code
written after the diagnosis.

So the tests that matter here are not "does the insert work". They are:

  * the loop is **closed** — closing a thread queues the work that writes the lesson;
  * the queueing is **atomic with the outcome**, because a thread that closed without
    queueing its lesson is the old bug wearing a new shape;
  * the lesson lands **in the same transaction** as the run record and the lead's
    completion, which is the submission's load-bearing claim: the work is not done until
    the memory is;
  * and `_OUTCOME` **covers every terminal state**, structurally, so adding a closed
    state without teaching the agent what it means fails here rather than in production.

Two layers, as `test_outreach` and `test_fleet` use: structural properties offline, and
everything that is a database behaviour against the real cluster.
"""

from __future__ import annotations

import os
import unittest
import uuid
from dataclasses import dataclass
from decimal import Decimal

from spindle import agents, embed, fleet, lessons, outreach, spend

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

REAL_EMBEDDING_MODEL = "openai:text-embedding-3-small"


@dataclass
class _FakeEmbedder:
    """Same contract as `embed.OpenAIEmbedder`, no network. Mirrors `test_vector_plans`'s
    fake deliberately rather than importing it — a test helper shared across suites is a
    dependency between suites, and this one needs to vary the vector per text so that two
    lessons are not identical rows."""

    key: str = REAL_EMBEDDING_MODEL
    model: str = REAL_EMBEDDING_MODEL
    model_version: str = ""

    def embed(self, texts):
        return [embed.Vector([(len(t) % 7) / 10 or 0.1] * embed.DIMENSIONS, self.model)
                for t in texts]


def _open_gate() -> spend.Gate:
    return spend.Gate(
        policy=spend.Policy(paid_enabled=True, daily_ceiling_usd=Decimal("999"),
                            per_call_ceiling_usd=Decimal("999"), dry_run=False),
        already_spent_usd=Decimal("0"), refused=[])


class OutcomeTable(unittest.TestCase):
    """Offline. `_OUTCOME` is data, and the properties worth holding are structural."""

    def test_every_closed_state_has_an_outcome_the_agent_understands(self):
        """The one that earns its place. `outreach.CLOSED` is where a thread stops, and
        `advance` queues a `distil_lesson` lead for every member of it. A closed state
        with no `_OUTCOME` entry therefore queues work that can only ever fail — and
        fail *permanently*, parking a lead a human has to find. Adding a terminal state
        should break this test, not the fleet."""
        self.assertEqual(set(agents._OUTCOME), set(outreach.CLOSED))

    def test_silence_is_weaker_than_an_explicit_no(self):
        """Deliberate asymmetry, not an oversight. A decline is evidence about the
        counterparty; silence is as likely to be evidence about us — a bad address, a
        pitch sent in August — so letting it demote as hard as a `no` would teach the
        shortlist to abandon people who never heard from us."""
        no_reply, _, _ = agents._OUTCOME["closed_no_reply"]
        declined, _, _ = agents._OUTCOME["closed_lost"]
        self.assertLess(abs(no_reply), abs(declined))
        self.assertLess(no_reply, 0)

    def test_a_win_is_positive_and_a_loss_is_negative(self):
        self.assertGreater(agents._OUTCOME["closed_won"][0], 0)
        self.assertLess(agents._OUTCOME["closed_lost"][0], 0)

    def test_one_thread_is_never_stated_confidently(self):
        """One observation is one observation. Confidence rises by repetition through
        `supersedes_id`, not by asserting it here."""
        for state, (_, confidence, _) in agents._OUTCOME.items():
            with self.subTest(state=state):
                self.assertLessEqual(confidence, 0.5)
                self.assertGreater(confidence, 0.0)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset")
class AgainstTheCluster(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test."""

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-memory-{self.tenant[:8]}", "memory loop test"))
        self.artist = self._party("Test Artist", "roster")
        self.curator = self._party("Test Curator", "counterparty")
        self._real_load = embed.load
        embed.load = lambda *a, **k: _FakeEmbedder()

    def tearDown(self) -> None:
        embed.load = self._real_load
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    # ------------------------------------------------------------------ fixtures

    def _party(self, name: str, party_class: str) -> str:
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, slug, name, party_class) "
                "VALUES (%s, %s, %s, %s, %s)",
                (party_id, self.tenant, f"{name.lower().replace(' ', '-')}-{party_id[:8]}",
                 name, party_class))
        return party_id

    def _thread_at(self, state: str) -> str:
        """A thread walked to `state` through the real state machine, because a thread
        placed there by an UPDATE would not have exercised the transitions the agent's
        input depends on."""
        campaign = str(outreach.create_campaign(
            self.conn, self.tenant, party_id=self.artist, name="test campaign")["id"])
        thread = str(outreach.open_thread(
            self.conn, self.tenant, campaign_id=campaign,
            counterparty_id=self.curator)["id"])
        for step in self._path_to(state):
            outreach.advance(self.conn, self.tenant, thread, step, reason=f"to {step}")
        return thread

    def _path_to(self, state: str) -> list[str]:
        if state == "closed_no_reply":
            return ["shortlisted", "approved", "drafted", "awaiting_human", "queued",
                    "sent", "awaiting_reply", "closed_no_reply"]
        if state == "closed_won":
            return ["shortlisted", "approved", "drafted", "awaiting_human", "queued",
                    "sent", "awaiting_reply", "replied", "agreed", "delivered",
                    "verified", "closed_won"]
        return ["shortlisted", "closed_lost"]

    def _leads(self, kind: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM lead WHERE tenant_id = %s AND kind = %s",
                        (self.tenant, kind))
            return list(cur.fetchall())

    def _lessons(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM lesson WHERE tenant_id = %s", (self.tenant,))
            return list(cur.fetchall())

    def _run(self, lead: dict) -> fleet.Outcome:
        """`fleet.NetworkAgent`'s two halves, in the order and the transaction shape
        `work_once` uses them: fetch outside, write inside."""
        prepared = agents.distil_lesson.fetch(self.conn, lead, _open_gate())
        with self.conn.transaction():
            return agents.distil_lesson.write(self.conn, lead, _open_gate(), prepared)

    # ---------------------------------------------------------- the loop is closed

    def test_closing_a_thread_queues_the_lesson(self):
        """The bug this whole change exists to fix. Before it, this count was zero for
        every thread ever closed."""
        self.assertEqual(self._leads("distil_lesson"), [])
        thread = self._thread_at("closed_lost")
        queued = self._leads("distil_lesson")
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["target"], thread)
        self.assertEqual(str(queued[0]["party_id"]), self.curator)
        self.assertEqual(queued[0]["state"], "pending")

    def test_a_refused_transition_queues_nothing(self):
        """`advance` raising must not leave a lesson queued for an outcome that did not
        happen. The savepoint is what makes this true and this is the test of it."""
        thread = self._thread_at("closed_lost")
        before = len(self._leads("distil_lesson"))
        with self.assertRaises(outreach.TransitionRefused):
            outreach.advance(self.conn, self.tenant, thread, "closed_won")
        self.assertEqual(len(self._leads("distil_lesson")), before)

    def test_an_open_thread_queues_nothing(self):
        campaign = str(outreach.create_campaign(
            self.conn, self.tenant, party_id=self.artist, name="c")["id"])
        thread = str(outreach.open_thread(
            self.conn, self.tenant, campaign_id=campaign,
            counterparty_id=self.curator)["id"])
        outreach.advance(self.conn, self.tenant, thread, "shortlisted")
        self.assertEqual(self._leads("distil_lesson"), [])

    # ------------------------------------------------------------- the lesson lands

    def test_running_the_agent_writes_a_lesson_scoped_to_the_counterparty(self):
        self._thread_at("closed_lost")
        outcome = self._run(self._leads("distil_lesson")[0])

        written = self._lessons()
        self.assertEqual(len(written), 1)
        row = written[0]
        self.assertEqual(row["scope_kind"], "party")
        self.assertEqual(str(row["scope_id"]), self.curator)
        self.assertLess(row["valence"], 0)
        self.assertIn("Test Curator", row["text"])
        self.assertIsNotNone(row["embedding"])
        self.assertEqual(row["model"], REAL_EMBEDDING_MODEL)
        self.assertEqual(outcome.facts, 1)

    def test_the_lesson_is_scoped_to_the_party_and_not_the_channel(self):
        """A `channel`-scoped lesson applies to every candidate a shortlist returned —
        `lessons.APPLIES_TO_ALL`. One conversation does not license that."""
        self._thread_at("closed_lost")
        self._run(self._leads("distil_lesson")[0])
        self.assertEqual(self._lessons()[0]["scope_kind"], "party")

    def test_a_win_and_a_loss_are_recorded_with_opposite_signs(self):
        self._thread_at("closed_won")
        self._run(self._leads("distil_lesson")[0])
        self.assertGreater(self._lessons()[0]["valence"], 0)

    def test_evidence_names_the_thread_it_came_from(self):
        """A lesson that reorders a shortlist has to be auditable back to what caused
        it, or an operator cannot tell a learned preference from a bug."""
        thread = self._thread_at("closed_lost")
        self._run(self._leads("distil_lesson")[0])
        evidence = self._lessons()[0]["evidence_json"]
        self.assertEqual(evidence["thread_id"], thread)
        self.assertEqual(evidence["final_state"], "closed_lost")

    def test_the_lesson_is_retrievable_the_instant_it_commits(self):
        """`PLATFORM-SPEC §1`'s stated reason for this database over a separate vector
        service: there is no window in which a lesson exists but cannot be found."""
        self._thread_at("closed_lost")
        self._run(self._leads("distil_lesson")[0])

        found = lessons.retrieve_for(
            self.conn, self.tenant,
            query_vector_literal=_FakeEmbedder().embed(["x"])[0].literal(),
            model=REAL_EMBEDDING_MODEL, candidate_ids=[self.curator])
        self.assertTrue(found, "a committed lesson was not retrievable")
        self.assertEqual(str(found[0]["scope_id"]), self.curator)

    def test_a_vanished_thread_fails_permanently_rather_than_retrying(self):
        """Retrying four times against a row that is gone burns the fleet's attempts and
        parks the lead anyway."""
        self._thread_at("closed_lost")
        lead = self._leads("distil_lesson")[0]
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM thread WHERE tenant_id = %s", (self.tenant,))
        with self.assertRaises(fleet.LeadFailed) as caught:
            agents.distil_lesson.fetch(self.conn, lead, _open_gate())
        self.assertTrue(caught.exception.permanent)

    def test_the_write_half_does_no_network_io(self):
        """The `NetworkAgent` contract, and the reason the lesson can share a transaction
        with `record_run` and `complete`. If `write` ever embeds, this fails."""
        self._thread_at("closed_lost")
        lead = self._leads("distil_lesson")[0]
        prepared = agents.distil_lesson.fetch(self.conn, lead, _open_gate())

        def explode(*_a, **_k):
            raise AssertionError("write half reached the embedding provider")

        embed.load = explode
        try:
            with self.conn.transaction():
                agents.distil_lesson.write(self.conn, lead, _open_gate(), prepared)
        finally:
            embed.load = lambda *a, **k: _FakeEmbedder()
        self.assertEqual(len(self._lessons()), 1)


if __name__ == "__main__":
    unittest.main()
