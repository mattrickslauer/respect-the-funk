"""R2 — what have we learned that applies here — and the rerank that spends it.

`PLATFORM-SPEC §6` names this the query that makes campaign n+1 cheaper than campaign n,
which is `SCOPE-RESET §1`'s entire justification for the party being the root of the
spine. Until migration 010 there was no table to ask, so every shortlist was computed as
though the label had never pitched anybody.

## Why the scoring is arithmetic and not a model call

The obvious version asks a model to rank the candidates given the lessons. It is worse in
three separate ways: it is unexplainable, so the console's inspector has nothing to
render; it is unmeasurable, so §6a's before-and-after cannot be computed; and it puts a
paid, rate-limited, occasionally-wrong call in the middle of the hottest query in the
system.

So the shift is a documented formula over stored numbers. A lesson has a `valence` (which
way it points) and a `confidence` (how sure we are), and the product of the two, times a
weight, moves the candidate's distance. Every shift is returned alongside the lesson id
that produced it.

## Why the shift is small

`LESSON_WEIGHT` is deliberately a fraction of the distances involved. Cosine distances
between genuinely similar and genuinely dissimilar parties differ by tenths; a weight
that could reorder the whole list would mean lessons had replaced the search rather than
informed it, and the first badly-written lesson would outrank musical fit forever.
"""

from __future__ import annotations

from typing import Any

import psycopg

from rtf_platform import embed, spend

#: How far one fully-confident, fully-signed lesson moves a candidate's cosine distance.
#:
#: 0.05 against distances that typically run 0.1–0.6: enough to reorder neighbours that
#: were close anyway, not enough to lift a poor match over a good one. This is a starting
#: value chosen before there were lessons to tune it against — spec §10 item 1 — and it
#: is a named constant so that changing it is a visible change rather than a silent one.
LESSON_WEIGHT = 0.05

#: Scopes that apply to every candidate a retrieval returned. A lesson at one of these
#: scopes was already filtered by channel and kind when it was fetched; if it came back,
#: it is in scope for the whole run.
GENERAL_SCOPES = ("party_kind", "channel", "global")

#: How far outside its legal range a number may stray before `write` calls it a bug
#: rather than rounding. A ratio that lands at 1.0000001 is arithmetic and gets clamped;
#: a confidence of 50 is a caller working in percent, and storing 1.0 for it hides the
#: mistake until somebody asks why every lesson is maximally confident.
CLAMP_EPSILON = 1e-6


def applies(lesson: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Does this lesson bear on this candidate?

    A `party`-scoped lesson names one party and applies to it alone. Everything else
    applies to the whole run, because the retrieval that produced it did the scoping.
    """
    if lesson["scope_kind"] == "party":
        return str(lesson["scope_id"]) == str(candidate["id"])
    return lesson["scope_kind"] in GENERAL_SCOPES


def _bounded(value: float, low: float, high: float, name: str) -> float:
    """Clamp rounding error into range; refuse anything further out.

    The distinction this draws is the whole point: absorbing noise keeps a good lesson,
    and absorbing a scale error keeps a wrong one forever.
    """
    number = float(value)
    if number < low - CLAMP_EPSILON or number > high + CLAMP_EPSILON:
        raise ValueError(
            f"{name}={number} is outside [{low}, {high}] by more than rounding error")
    return max(low, min(high, number))


def rerank(candidates: list[dict[str, Any]], lessons: list[dict[str, Any]], *,
           weight: float = LESSON_WEIGHT) -> list[dict[str, Any]]:
    """Adjust R1's candidates by what we have learned, and say what did the adjusting.

    Returns new rows — the caller's are not touched — each carrying the original keys
    plus `adjusted` and `applied`. Sorted ascending, because these are distances and
    nearer is better.

    A positive valence *reduces* the distance. That inversion is the one place this
    function is easy to get backwards, which is why the tests assert the ordering rather
    than the arithmetic.
    """
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        shifts: list[dict[str, Any]] = []
        total = 0.0
        for lesson in lessons:
            if not applies(lesson, candidate):
                continue
            shift = float(lesson["valence"]) * float(lesson["confidence"]) * weight
            if shift == 0.0:
                continue
            total += shift
            shifts.append({"lesson_id": str(lesson["id"]),
                           "text": lesson["text"],
                           # Signed. A consumer rendering this has to be able to say
                           # "sank by 0.05 — ghosted twice" rather than only "0.05".
                           # Positive lifted the candidate, negative sank it.
                           "shift": shift})

        # Clamped at zero: a cosine distance is non-negative, and anything downstream
        # reading a negative one as a distance is now quietly wrong.
        adjusted = max(0.0, float(candidate["distance"]) - total)
        out.append({**candidate, "adjusted": adjusted, "applied": shifts})

    out.sort(key=lambda row: row["adjusted"])
    return out


def heads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop every row that another row in this result supersedes.

    Resolved within the result set rather than by a query, because retrieval is a top-k
    and the superseded row may not have scored at all. A row whose `supersedes_id` points
    outside the set is still current — it replaced something that simply is not here.
    """
    replaced = {str(row["supersedes_id"]) for row in rows if row.get("supersedes_id")}
    return [row for row in rows if str(row["id"]) not in replaced]


def write(conn: psycopg.Connection, tenant_id: str, *, scope_kind: str, scope_id: str,
          text: str, valence: float, confidence: float, evidence: dict[str, Any],
          gate: spend.Gate, supersedes_id: str | None = None) -> str:
    """Record something learned, embedded and searchable at commit.

    The embedding and the row are written in one transaction, which is
    `PLATFORM-SPEC §1`'s stated reason for this database over a separate vector service:
    there is no window in which a lesson exists but cannot be retrieved. An agent that
    writes a lesson and immediately reranks sees it.

    `confidence` and `valence` are bounded by `_bounded`, not simply clamped: within
    `CLAMP_EPSILON` of their legal range they are rounded into it, because a caller
    computing one from a ratio can land at 1.0000001 and refusing the whole lesson over
    float noise would lose the lesson. Further out than that, `_bounded` raises — a
    confidence of 50 is a caller working in percent, not noise, and clamping it to 1.0
    would hide the mistake instead of surfacing it.
    """
    provider = embed.load()
    vectors, _ = embed.embed_batch(gate, provider, [text])

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       evidence_json, confidence, valence,
                                       embedding, model, model_version, supersedes_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::VECTOR(1024), %s, %s, %s)
                RETURNING id""",
                (tenant_id, scope_kind, scope_id, text,
                 psycopg.types.json.Jsonb(evidence),
                 _bounded(confidence, 0.0, 1.0, "confidence"),
                 _bounded(valence, -1.0, 1.0, "valence"),
                 vectors[0].literal(), provider.model,
                 getattr(provider, "model_version", ""), supersedes_id),
            )
            return str(cur.fetchone()["id"])


def retrieve_for(conn: psycopg.Connection, tenant_id: str, *,
                 query_vector_literal: str, model: str,
                 candidate_ids: list[str], limit: int = 10) -> list[dict[str, Any]]:
    """Every lesson bearing on this shortlist: the general ones and the named ones.

    Two queries, because they are two different questions and only one of them is a
    similarity question.

      * **General** — `party_kind`, `channel` and `global` lessons, by ANN over
        `lesson_semantic`. `scope_kind IN (…)` is accelerated on a prefix column the same
        way equality is; `docs/reference/COCKROACHDB-AI.md` has the constraint.
      * **Named** — lessons about the specific parties R1 returned, by id over
        `lesson_by_scope`. We already know who we mean; asking the vector index to
        rediscover them would be slower and could miss one.

    Superseded rows are dropped from each result separately, so a correction retrieved
    without its predecessor still counts.
    """
    general: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, scope_kind, scope_id, text, valence, confidence,
                      supersedes_id, embedding <=> %s::VECTOR(1024) AS distance
                 FROM lesson
                WHERE tenant_id = %s AND model = %s
                  AND scope_kind IN ('party_kind', 'channel', 'global')
                ORDER BY embedding <=> %s::VECTOR(1024)
                LIMIT %s""",
            (query_vector_literal, tenant_id, model, query_vector_literal, limit),
        )
        general = list(cur.fetchall())

    named: list[dict[str, Any]] = []
    if candidate_ids:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, scope_kind, scope_id, text, valence, confidence,
                          supersedes_id, NULL::FLOAT AS distance
                     FROM lesson
                    WHERE tenant_id = %s AND scope_kind = 'party'
                      AND scope_id = ANY(%s)
                    ORDER BY created_at DESC""",
                (tenant_id, [str(cid) for cid in candidate_ids]),
            )
            named = list(cur.fetchall())

    return heads(general) + heads(named)
