"""The `index_podcasts` stage, offline and without a cluster.

`test_podcastindex.py` covers the adapter — the block arithmetic, the signature, the
readers. This file covers the *stage*: the fetch half's translation of an adapter failure
into something the fleet can act on, and the write half's shape on the way into the
database.

Neither half is tested against a real anything. There are no Podcast Index credentials in
this checkout and there is not going to be one issued to a test runner, so every HTTP call
is mocked; `conftest.py` refuses to let the suite near a cluster it has not been told is
disposable, so the write half runs against a `Ledger` below. That is a deliberate second
choice, not a compromise dressed up: a stage whose only test is a DB-gated one is a stage
that is untested on the machine where nobody notices, which is the argument
`test_podcastindex.py` opens with and it applies twice as hard here.

**What the `Ledger` is, and what it is not.** It is a stand-in for the four unique
constraints this stage's correctness actually rests on — `party (tenant_id, slug)`,
`party_document (tenant_id, content_hash)`, `party_identifier (tenant_id, kind, value)`
and `fact_one_live_per_dimension` — and for `cur.rowcount`, which the stage reads as a
decision three separate times. It is not a SQL engine and cannot be: it dispatches on the
statement's table and shape. What that buys is the ability to assert the properties that
break silently, and the properties this stage breaks silently are all conflict semantics:

  * **the three provenance classes**, because a fact that lands `measured` when it was
    inferred is a shortlist ranking a podcast on its own advertising copy and never
    saying so;
  * **a re-run writing no second party**, because a duplicated show is the same
    broadcaster twice on one shortlist, and `--requeue index_podcasts` is a documented
    thing to do;
  * **two shows composing to identical prose both getting a document**, because the
    version of this that hashes the body alone leaves the second one unembeddable and
    reports success — the defect that stranded 63 parties the last time it shipped;
  * **a missing credential raising**, because the whole argument for `podcastindex.py`
    raising rather than returning empty is that an outage must not be indistinguishable
    from a world with no music podcasts in it.

What the `Ledger` deliberately does not check is that the SQL is valid SQL. Nothing here
could. `test_tenant_scoping.py` proves every statement carries its `tenant_id` predicate,
and the cluster proves the rest on the first real run.
"""

from __future__ import annotations

import io
import json
import re
import unittest
import urllib.error
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

from rtf_platform import agents, fleet, podcastindex

CREDS = {podcastindex.KEY_VAR: "TESTKEY0000000000000",
         podcastindex.SECRET_VAR: "testsecret##0000"}

TENANT = "11111111-1111-1111-1111-111111111111"

SCHEMA = Path(__file__).resolve().parents[2] / "schema"


# --------------------------------------------------------------------------- fixtures

def _feed(feed_id: int = 100, **over: Any) -> dict[str, Any]:
    """A music feed shaped like the API's, carrying every field the stage reads."""
    base: dict[str, Any] = {
        "id": feed_id,
        "title": "Deep House Weekly",
        "url": "https://example.com/feed.xml",
        "link": "https://example.com",
        "description": "<p>New deep house and garage every Friday.</p>",
        "author": "Example Media",
        "ownerName": "Jo Presenter",
        "language": "en",
        "categories": {"55": "Music", "77": "Commentary"},
        "medium": "podcast",
        "dead": 0,
        "episodeCount": 212,
        "lastHttpStatus": 200,
        "itunesId": 12345,
        "podcastGuid": "917393e3-1b1e-5cef-ace4-edaa54e1f810",
    }
    base.update(over)
    return base


def _lead(target: str = "0") -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "tenant_id": TENANT, "target": target,
            "kind": "index_podcasts"}


class _Response:
    """The little of `urlopen`'s context manager that `podcastindex._get` uses."""

    def __init__(self, payload: Any) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _answering(payload: Any):
    def fake(request, timeout=None):          # noqa: ANN001 — mirrors urlopen's shape
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload)

    return mock.patch.object(podcastindex.urllib.request, "urlopen", fake)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.podcastindex.org/x", code, "err", {},
                                  io.BytesIO(b"nope"))


class ExplodingGate:
    """A `spend.Gate` stand-in that fails on any use at all.

    The stage's docstring claims it never consults the gate because Podcast Index is
    free. A claim in a docstring is the kind of thing that quietly stops being true, so
    it is asserted instead: every test below passes one of these, and any gate call
    anywhere in either half fails the suite by name rather than by a spend that shows up
    on a bill later.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f"index_podcasts consulted the spend gate ({name!r}). Podcast Index is free "
            "and `023_podcast_source.sql` records it as `cost_class = 'free'`; a gate "
            "call here means either the stage started paying for something or the "
            "docstring saying it does not has gone stale.")


# ----------------------------------------------------------------------- the fake DB

class Ledger:
    """Enough of a database to make the stage's conflict semantics observable.

    Statements are matched on their table and shape rather than parsed. `rowcount` is
    maintained per statement because the stage reads it as a decision — "did this
    document already exist", "was there a lead to requeue" — and a fake that always
    reported 1 would make both of those branches untestable.
    """

    def __init__(self) -> None:
        self.party: dict[tuple[str, str], dict[str, Any]] = {}
        self.party_role: set[tuple[str, str, str]] = set()
        self.party_identifier: set[tuple[str, str, str]] = set()
        self.party_fact: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.presence: set[tuple[str, str, str]] = set()
        self.party_document: dict[tuple[str, str], dict[str, Any]] = {}
        #: `target_hash` values of leads that exist and are not already pending — the
        #: only state the requeue UPDATE can distinguish.
        self.requeueable: set[str] = set()
        self.requeued: list[str] = []
        self.statements: list[str] = []

    # -- the psycopg surface the stage touches ------------------------------------
    def cursor(self) -> "Cursor":
        return Cursor(self)


class Cursor:

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.rowcount = 0
        self._pending: dict[str, Any] | None = None

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def fetchone(self) -> dict[str, Any] | None:
        row, self._pending = self._pending, None
        return row

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        flat = re.sub(r"\s+", " ", sql).strip()
        self.ledger.statements.append(flat)
        self._pending = None
        self.rowcount = 0

        if flat.startswith("INSERT INTO party ("):
            tenant, slug, name = params[0], params[1], params[2]
            key = (tenant, slug)
            if key in self.ledger.party:
                return
            row = {"id": str(uuid.uuid4()), "tenant_id": tenant, "slug": slug,
                   "name": name}
            self.ledger.party[key] = row
            self._pending = {"id": row["id"]}
            self.rowcount = 1
            return

        if flat.startswith("SELECT id FROM party WHERE"):
            row = self.ledger.party.get((params[0], params[1]))
            self._pending = {"id": row["id"]} if row else None
            self.rowcount = 1 if row else 0
            return

        if flat.startswith("INSERT INTO party_role"):
            self._add(self.ledger.party_role, (params[0], params[1], "role"))
            return

        if flat.startswith("INSERT INTO party_identifier"):
            # UNIQUE (tenant_id, kind, value) — deliberately not keyed on party_id, so
            # a guid another show already claimed conflicts here as it would live.
            self._add(self.ledger.party_identifier, (params[0], params[2], params[3]))
            return

        if flat.startswith("INSERT INTO party_fact"):
            tenant, party, dimension = params[0], params[1], params[2]
            # `fact_one_live_per_dimension`: unique on (tenant, party, dimension,
            # provenance) where status = 'live'.
            key = (tenant, party, dimension, params[5])
            if key in self.ledger.party_fact:
                return
            self.ledger.party_fact[key] = {
                "value_text": params[3], "value_json": params[4],
                "provenance": params[5], "confidence": params[6]}
            self.rowcount = 1
            return

        if flat.startswith("INSERT INTO presence"):
            self._add(self.ledger.presence, (params[0], params[1], "web"))
            return

        if flat.startswith("INSERT INTO party_document"):
            tenant, party, content_hash = params[0], params[1], params[6]
            key = (tenant, content_hash)
            if key in self.ledger.party_document:
                return
            self.ledger.party_document[key] = {
                "party_id": party, "url": params[3], "title": params[4],
                "body": params[5]}
            self.rowcount = 1
            return

        if flat.startswith("UPDATE lead"):
            target_hash = params[1]
            if target_hash in self.ledger.requeueable:
                self.ledger.requeueable.discard(target_hash)
                self.ledger.requeued.append(target_hash)
                self.rowcount = 1
            return

        raise AssertionError(
            f"the ledger does not model this statement, so the test would be silently "
            f"asserting nothing about it: {flat[:160]}")

    def _add(self, store: set[Any], key: Any) -> None:
        if key in store:
            return
        store.add(key)
        self.rowcount = 1


def _write(ledger: Ledger, feeds: list[dict[str, Any]], *,
           start: int = 0, seen: int | None = None) -> fleet.Outcome:
    prepared = {"start": start, "seen": len(feeds) if seen is None else seen,
                "feeds": feeds}
    return agents._write_index_podcasts(ledger, _lead(str(start)), ExplodingGate(),
                                        prepared)


# ------------------------------------------------------------------------ the fetch

class Fetching(unittest.TestCase):

    def test_a_missing_credential_fails_the_lead_permanently_with_the_remedy(self) -> None:
        """The strongest rule in this codebase, at the one place a stage could break it.

        A stage that skipped quietly here would report success and write nothing, and the
        console would show a healthy `index_podcasts` indexing a world with no podcasts
        in it. `permanent=True` because four more attempts against an unset environment
        variable only delay somebody reading the message.
        """
        with mock.patch.dict("os.environ",
                             {podcastindex.KEY_VAR: "", podcastindex.SECRET_VAR: ""}):
            with self.assertRaises(fleet.LeadFailed) as caught:
                agents._fetch_index_podcasts(Ledger(), _lead("0"), ExplodingGate())

        self.assertTrue(caught.exception.permanent,
                        "a missing key is not fixed by retrying")
        message = str(caught.exception)
        self.assertIn(podcastindex.KEY_VAR, message)
        self.assertIn(podcastindex.SECRET_VAR, message)
        self.assertIn("api.podcastindex.org/signup", message,
                      "the message has to say how to get one, not just that it is absent")

    def test_a_rate_limit_comes_back_transient_and_a_bad_key_does_not(self) -> None:
        """`Refused.permanent` exists to separate these two, and the fetch half's only
        job with it is to forward it. Collapsing them either parks a lead the service
        merely asked us to slow down for, or retries a wrong key four times."""
        for code, permanent in ((429, False), (401, True)):
            with self.subTest(code=code):
                with mock.patch.dict("os.environ", CREDS, clear=False), \
                        _answering(_http_error(code)):
                    with self.assertRaises(fleet.LeadFailed) as caught:
                        agents._fetch_index_podcasts(Ledger(), _lead("0"),
                                                     ExplodingGate())
                self.assertIs(caught.exception.permanent, permanent)

    def test_a_target_that_is_not_a_block_start_is_parked_not_retried(self) -> None:
        for target in ("", "  ", "WA", "-1", "12.5"):
            with self.subTest(target=target):
                with self.assertRaises(fleet.LeadFailed) as caught:
                    agents._fetch_index_podcasts(Ledger(), _lead(target),
                                                 ExplodingGate())
                self.assertTrue(caught.exception.permanent,
                                "a malformed target never becomes well-formed")

    def test_only_pitchable_feeds_reach_the_write_and_the_rest_are_counted(self) -> None:
        """The filter runs in the fetch half so the write half stays purely
        transactional. `seen` keeps the whole block visible in the summary — a stage that
        reported only what it wrote could not be told apart from one reading an empty
        index."""
        payload = {"status": "true", "feeds": [
            _feed(1),                                            # kept
            _feed(2, dead=1),                                    # not publishing
            _feed(3, episodeCount=0),                            # parses, empty
            _feed(4, categories={"16": "News"}),                 # not a music show
            _feed(5, medium="music"),                            # an album, not a show
            _feed(6, lastHttpStatus=404),                        # feed is gone
            _feed(900),                                          # outside the block
        ]}
        with mock.patch.dict("os.environ", CREDS, clear=False), _answering(payload):
            prepared = agents._fetch_index_podcasts(Ledger(), _lead("0"),
                                                    ExplodingGate())

        self.assertEqual([f["id"] for f in prepared["feeds"]], [1])
        self.assertEqual(prepared["seen"], 6,
                         "the block's own rows are seen; feed 900 belongs to another "
                         "block and is dropped by the adapter, not by this stage")
        self.assertEqual(prepared["start"], 0)


# ------------------------------------------------------------------------- the write

class Provenance(unittest.TestCase):

    def test_the_three_classes_land_exactly_as_podcastindex_argues_them(self) -> None:
        """`SCOPE-RESET §2a` rule 1 in the only place it can actually be enforced.

        The adapter's header splits a feed record three ways and gives an argument for
        each boundary. That argument is worth nothing if the stage writing the rows
        flattens it, and flattening it is invisible from every other view in the system —
        the fact is there, it reads plausibly, and it is wearing the wrong clothes.
        """
        ledger = Ledger()
        _write(ledger, [_feed(1)])

        classes = {dimension: row["provenance"]
                   for (_, _, dimension, _), row in ledger.party_fact.items()}
        self.assertEqual(classes, {
            # what Podcast Index's crawler observed
            "role": "measured",
            "episode_count": "measured",
            # what the publisher wrote about themselves
            "host": "asserted",
            "description": "asserted",
            "genre": "asserted",
            "language": "asserted",
            # what this module decided, from the weakest input in the file
            "show_kind": "inferred",
        })

    def test_confidence_follows_the_class_and_never_contradicts_it(self) -> None:
        ledger = Ledger()
        _write(ledger, [_feed(1)])
        for (_, _, dimension, _), row in ledger.party_fact.items():
            with self.subTest(dimension=dimension):
                self.assertEqual(row["confidence"],
                                 agents.PODCAST_CONFIDENCE[row["provenance"]])
        self.assertGreater(agents.PODCAST_CONFIDENCE["measured"],
                           agents.PODCAST_CONFIDENCE["asserted"])
        self.assertGreater(agents.PODCAST_CONFIDENCE["asserted"],
                           agents.PODCAST_CONFIDENCE["inferred"])

    def test_the_role_fact_is_written_so_a_rebuild_cannot_call_it_a_radio_station(self) -> None:
        """`profiles.from_facts` raises without a `role` dimension, and before migration
        026 removed the default it would instead have described the show as a radio
        station. Writing the fact at the source is what stops the next
        `--recompose-profiles` needing a backfill for every podcast."""
        ledger = Ledger()
        _write(ledger, [_feed(1)])
        roles = [row["value_text"] for (_, _, dim, _), row in ledger.party_fact.items()
                 if dim == "role"]
        self.assertEqual(roles, ["podcast"])

    def test_the_rebuild_path_can_find_every_input_it_composes_from(self) -> None:
        """The three dimension names `profiles.from_facts` reads back, pinned by name.

        `from_facts` requires `role`, fills `compose`'s prose slot from `description` and
        excises `host` from that prose. It looks them up by dimension string, so a
        rename here is a rename that compiles, passes every other test, and silently
        rebuilds eighty-five thousand podcasts without their blurbs the next time
        `--recompose-profiles` runs. Nothing else in the suite would notice, which is
        exactly why this assertion is by literal name rather than by behaviour.
        """
        ledger = Ledger()
        _write(ledger, [_feed(1)])
        stored = {dim: row["value_text"]
                  for (_, _, dim, _), row in ledger.party_fact.items()}

        self.assertEqual(stored.get("role"), "podcast")
        self.assertEqual(stored.get("host"), "Jo Presenter")
        self.assertEqual(stored.get("description"),
                         podcastindex.blurb(_feed(1)),
                         "the stored blurb has to be the adapter's, markup stripped and "
                         "capped there — not a second copy capped again here")

    def test_the_stored_blurb_is_not_truncated_a_second_time(self) -> None:
        """`podcastindex.BLURB_CHARS` is the one place that decides how much of a
        publisher's prose reaches a vector, and it argues for its number. A tighter cap
        applied on the way into `party_fact` would overrule it invisibly and only on the
        rebuild path, where the two would then disagree."""
        long_feed = _feed(1, description="deep house " * 200)
        ledger = Ledger()
        _write(ledger, [long_feed])
        stored = {dim: row["value_text"]
                  for (_, _, dim, _), row in ledger.party_fact.items()}
        self.assertEqual(len(stored["description"]),
                         len(podcastindex.blurb(long_feed)))
        self.assertGreater(len(stored["description"]), 500)

    def test_a_feed_that_names_no_owner_gets_no_host_fact_rather_than_a_blank_one(self) -> None:
        """The absence is the honest answer and a shortlist is entitled to see it —
        `enrich_genre` writes nothing on exactly the same principle."""
        ledger = Ledger()
        _write(ledger, [_feed(1, ownerName="", author="", description="")])
        dimensions = {dim for (_, _, dim, _) in ledger.party_fact}
        self.assertNotIn("host", dimensions)
        self.assertNotIn("description", dimensions,
                         "a show that wrote no blurb gets no empty one; `from_facts` "
                         "reading '' back is the same as reading nothing")
        self.assertIn("genre", dimensions, "the rest of the facts still land")

    def test_the_host_is_a_fact_and_never_a_second_party(self) -> None:
        """Turning `ownerName` into a person-party is an identity claim, and it is the
        claim that produced thirteen junk parties from a name-match harvest in August."""
        ledger = Ledger()
        _write(ledger, [_feed(1)])
        self.assertEqual(len(ledger.party), 1)
        names = {row["name"] for row in ledger.party.values()}
        self.assertEqual(names, {"Deep House Weekly"})
        self.assertNotIn("Jo Presenter", names)


class Idempotency(unittest.TestCase):

    def test_a_rerun_of_a_block_creates_no_second_party(self) -> None:
        """`--requeue index_podcasts` is a documented thing to do, and the index changes
        under the blocks, so a block being read twice is normal rather than exceptional.
        A second party for the same show is the same broadcaster twice on one shortlist.
        """
        ledger = Ledger()
        first = _write(ledger, [_feed(1)])
        second = _write(ledger, [_feed(1)])

        self.assertEqual(len(ledger.party), 1)
        self.assertEqual(len(ledger.party_document), 1)
        self.assertIn("1 new show(s)", first.summary)
        self.assertIn("0 new show(s), 1 refreshed", second.summary)

    def test_an_unchanged_rerun_queues_no_second_embedding(self) -> None:
        """The document is the trigger for the embedding, so an unchanged document has to
        mean an unchanged vector. Re-queueing here is paying a second time for the same
        answer, over eighty-five thousand shows."""
        ledger = Ledger()
        first = _write(ledger, [_feed(1)])
        second = _write(ledger, [_feed(1)])

        self.assertEqual(len(first.follow_on), 1)
        self.assertEqual(first.follow_on[0]["kind"], "embed_party")
        self.assertEqual(second.follow_on, [])
        self.assertEqual(second.documents, 0)

    def test_a_changed_feed_writes_a_new_document_and_requeues_the_embedding(self) -> None:
        """The other half of the same rule. A show that has retagged itself has a stale
        vector, and a genre in a column that never reached an embedding leaves the
        shortlist exactly as wrong as it was — `index_streams` is where that lesson was
        learned."""
        ledger = Ledger()
        first = _write(ledger, [_feed(1)])
        target_hash = first.follow_on[0]["target_hash"]
        # The embedding lead now exists and has been worked, which is the state a real
        # requeue has to find.
        ledger.requeueable.add(target_hash)

        second = _write(ledger, [_feed(1, categories={"55": "Music", "88": "Reviews"})])

        self.assertEqual(len(ledger.party), 1, "still one show")
        self.assertEqual(len(ledger.party_document), 2, "a new document for new prose")
        self.assertEqual(ledger.requeued, [target_hash])
        self.assertEqual(second.follow_on, [],
                         "the lead was requeued in place; inserting a second would "
                         "conflict on target_hash and silently do nothing")

    def test_two_shows_composing_to_identical_prose_both_get_a_document(self) -> None:
        """The defect that stranded 63 parties as unembeddable, in the one stage most
        exposed to it.

        `profiles.compose` is terse by design, so two music shows with the same
        categories, the same language and no blurb produce a byte-identical body. Under
        `UNIQUE (tenant_id, content_hash)` a hash of the body alone would give the first
        show its document and every show after it none — silently, reporting success.
        Hashing the party id alongside the body is what makes the two distinguishable.
        """
        twins = [_feed(1, title="Show One", description="", link=""),
                 _feed(2, title="Show Two", description="", link="")]
        ledger = Ledger()
        outcome = _write(ledger, twins)

        bodies = {row["body"] for row in ledger.party_document.values()}
        self.assertEqual(len(bodies), 1,
                         "the fixture is only meaningful if the prose really does "
                         "collide — if this fails, `profiles.compose` changed")
        self.assertEqual(len(ledger.party_document), 2)
        self.assertEqual(outcome.documents, 2)
        self.assertEqual(len(outcome.follow_on), 2,
                         "both shows are queued for embedding, not just the first")

    def test_a_show_with_no_title_is_skipped_rather_than_named_from_its_url(self) -> None:
        ledger = Ledger()
        outcome = _write(ledger, [_feed(1, title="   ")])
        self.assertEqual(ledger.party, {})
        self.assertEqual(outcome.follow_on, [])


class WhatIsWritten(unittest.TestCase):

    def test_the_feed_id_is_the_key_the_slug_is_built_on(self) -> None:
        """Podcast Index never reuses a feed ID, so a show that renames itself keeps its
        row — the same property `facility_id` gives a station."""
        ledger = Ledger()
        _write(ledger, [_feed(4242)])
        self.assertEqual({slug for _, slug in ledger.party}, {"pi-4242"})

        _write(ledger, [_feed(4242, title="Deep House Weekly (Rebooted)")])
        self.assertEqual(len(ledger.party), 1)

    def test_every_identifier_is_measured_and_keyed_for_a_later_source(self) -> None:
        ledger = Ledger()
        _write(ledger, [_feed(1)])
        kinds = {kind for _, kind, _ in ledger.party_identifier}
        self.assertEqual(kinds, {"podcastindex_feed_id", "podcast_guid", "itunes_id"})

    def test_a_feed_without_a_guid_or_an_itunes_id_still_gets_its_own_key(self) -> None:
        ledger = Ledger()
        _write(ledger, [_feed(1, podcastGuid="", itunesId=None)])
        self.assertEqual({kind for _, kind, _ in ledger.party_identifier},
                         {"podcastindex_feed_id"})

    def test_the_website_is_a_presence_and_no_contact_route_is_invented(self) -> None:
        """`023` names this as the point rather than a gap: Podcast Index does not publish
        the owner's address, and 018's header calls a `music@<domain>` guess the fastest
        way to earn a spam complaint. A homepage is a surface you may read."""
        ledger = Ledger()
        _write(ledger, [_feed(1)])
        self.assertEqual(len(ledger.presence), 1)
        self.assertFalse(
            [s for s in ledger.statements if "contact_route" in s],
            "this stage must not write a route it has no evidence for")

    def test_the_document_body_is_what_the_adapter_composed(self) -> None:
        """The document is the evidence the vector is computed from. A body that drifted
        from `profile_text` would leave a vector nobody can argue with when a shortlist
        looks wrong."""
        feed = _feed(1)
        ledger = Ledger()
        _write(ledger, [feed])
        body = next(iter(ledger.party_document.values()))["body"]
        self.assertEqual(body, podcastindex.profile_text(feed))
        self.assertNotIn("Deep House Weekly", body,
                         "`profiles.py` rule 2 — the show's name is not in the vector, "
                         "or an artist called Deep House shortlists to it by name")

    def test_the_stage_never_consults_the_spend_gate(self) -> None:
        """Asserted rather than documented. `ExplodingGate` raises on any attribute
        access, and every call in this file passes one, so this test is really a
        statement about all of them — kept as its own case so the reason has somewhere
        to live."""
        ledger = Ledger()
        outcome = _write(ledger, [_feed(1), _feed(2)])
        self.assertEqual(outcome.calls, 1, "one HTTP call per block, and nothing paid")


class Registration(unittest.TestCase):

    def test_the_registry_can_run_the_kind_the_manifest_declares(self) -> None:
        """023 shipped its manifest row ahead of the agent and said why. This is the
        pair being closed: `research.fleet` renders a kind with a manifest row and no
        registry entry as `declared, not running`, which was accurate and is no longer."""
        agent = agents.REGISTRY["index_podcasts"]
        self.assertIsInstance(agent, fleet.NetworkAgent)
        self.assertTrue(callable(agent.fetch))
        self.assertTrue(callable(agent.write))

    def test_migration_027_flips_the_row_023_shipped_disabled(self) -> None:
        """The two files have to agree on the kind, or the flip enables nothing and
        nobody finds out until somebody wonders where the podcasts are."""
        declared = (SCHEMA / "023_podcast_source.sql").read_text()
        enabled = (SCHEMA / "027_enable_podcasts.sql").read_text()

        self.assertIn("'index_podcasts'", declared)
        self.assertIn("false, now()", declared,
                      "023 is the migration that ships the row disabled")
        self.assertRegex(
            re.sub(r"\s+", " ", enabled),
            r"UPDATE agent_manifest SET enabled = true, updated_at = now\(\) "
            r"WHERE kind = 'index_podcasts' AND NOT enabled;")

    def test_027_does_not_restate_the_columns_an_operator_can_tune(self) -> None:
        """`routes.py` exposes a toggle that writes `enabled`, and the console exposes
        the batch size. A migration that re-asserts its neighbours undoes an operator's
        work without saying so, which is why this one is a single-column UPDATE."""
        body = "\n".join(line for line in
                         (SCHEMA / "027_enable_podcasts.sql").read_text().splitlines()
                         if not line.strip().startswith("--"))
        for column in ("batch_size", "lease_seconds", "writes", "adapters",
                       "max_attempts", "backoff_seconds"):
            self.assertNotIn(column, body,
                             f"027 must not restate {column}")


if __name__ == "__main__":
    unittest.main()
