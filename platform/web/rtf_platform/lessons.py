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


def applies(lesson: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Does this lesson bear on this candidate?

    A `party`-scoped lesson names one party and applies to it alone. Everything else
    applies to the whole run, because the retrieval that produced it did the scoping.
    """
    if lesson["scope_kind"] == "party":
        return str(lesson["scope_id"]) == str(candidate["id"])
    return lesson["scope_kind"] in GENERAL_SCOPES


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
                           "shift": abs(shift)})

        # Clamped at zero: a cosine distance is non-negative, and anything downstream
        # reading a negative one as a distance is now quietly wrong.
        adjusted = max(0.0, float(candidate["distance"]) - total)
        out.append({**candidate, "adjusted": adjusted, "applied": shifts})

    out.sort(key=lambda row: row["adjusted"])
    return out
