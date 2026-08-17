"""When the screen panel is up, and what is on it. One source of truth.

Both halves of the composite read this file:

  * `compose.py` uses it to decide, per frame, whether a scene animation belongs
    full-frame in the centre or shrunk into the left third — a scene moves aside
    exactly when a panel is up, and never on its own schedule.
  * `screen-layer.html` reads the same list (written out as `out/screen/cues.js`)
    to know what to draw.

Keeping one list is not tidiness. The failure it prevents is a panel appearing over
a full-frame diagram, which is the one composition that makes the frame unreadable,
and it is invisible until a full render finishes.

## Times are absolute, and they are the take's own clock

Unlike the scenes — which are stretched onto whichever paragraphs they belong to —
a panel appears at the moment the words and the picture agree, which is not always
a paragraph edge. The numbers below were read off `out/edit/align.json`.

## The rule the cue sheet follows

A panel earns its place by showing something the voice claims and a diagram cannot
prove. Where there is no such evidence the panel stays down and the presenter is the
picture — 24.8-30.6, 48.7-52.8, 80.6-87.8 and 137.7-144.4 are deliberately bare, and
so is the whole residency passage, because `SHOW REGIONS` on this cluster returns one
region and a panel there would disprove the narration rather than support it.
"""

from __future__ import annotations

# (start, end, kind, ref, caption)
#   kind "sql"     -> ref is a slug in screen/proofs.json
#   kind "shot"    -> ref is a PNG under out/app/
#   kind "stack"   -> ref is a key in PLATES below
#
# ## The shots are the public site, not the console, and that is not the plan
#
# The console behind the sign-in wall is the richer footage — roster, threads, inbox,
# approvals — and `capture_app.py --cookie` exists to film it. It is not here because
# **sign-in is currently down**: `POST /signin/code` returns 502 with `AccessDenied
# when calling the SendEmail operation`, and an emailed code is the only credential
# this product has by design. See `docs/runbooks/ses-sign-in-mail.md`.
#
# So these cues point at real screenshots of the deployed public landing page, which
# need no account and are genuinely the running product. When sign-in is restored,
# re-run `capture_app.py --cookie <token>` and swap `hero.png` for `console-today.png`
# and `send.png` for `console-outbox.png`; nothing else here has to change.
CUES: list[tuple[float, float, str, str, str]] = [
    # "Spindle does it. Every night, for every artist on the roster."
    # The first sight of the product, at 21s — inside the 20-30s the judging
    # guidance asks for, and the reason paragraph 2 gets a panel at all.
    (21.00, 24.50, "shot", "hero.png", "spindle.mattrickslauer.com"),

    # "the filters — tenant, model, contactable — living inside the index itself"
    (30.90, 40.90, "sql", "search", "EXPLAIN, on the live cluster"),

    # "And that index starts with tenant_id."  The output says it better than a
    # diagram can, so scene 04 is dropped here and the panel carries the beat.
    (41.00, 47.90, "sql", "prefix", "the index prefix"),

    # "The index is never finished, either. Agents are adding to these records all day."
    (52.90, 61.50, "sql", "memory", "what the agents have accumulated"),

    # "a changefeed on the memory tables wakes the next one"
    (61.60, 72.90, "sql", "bus", "a write, landing on the bus"),

    # "The send claims its lease before the network call."
    (73.30, 80.30, "shot", "send.png", "the send gate"),

    # Paragraph 14 — "It spends the label's money too, up to a ceiling" — wants the
    # approvals screen and that is behind the sign-in wall, so it gets no panel and
    # 11-money plays full frame instead. A public-site screenshot standing in for an
    # approvals queue would be the one kind of substitution this edit does not make.

    # "the ledger row stored the exact logical timestamp it decided at"
    # The money shot. Deliberately ends at 137.1 so "Postgres cannot do that at any
    # price" is said to camera with nothing on screen but him.
    (122.90, 137.30, "sql", "replay", "AS OF SYSTEM TIME"),

    # "One database doing the index, the scheduler, the outbox, the event bus and the
    # ledger." He is naming the features here, so this is where they get named on
    # screen too — the judging guidance asks for the services and CockroachDB
    # features in text a judge can confirm at a glance, and this is the one line the
    # list is not an interruption of.
    (145.50, 154.60, "stack", "stack", "what this is built on"),

    # the close
    (155.00, 161.00, "shot", "hero.png", "live today"),

    # The end card. It starts AFTER the take runs out — `compose.py` appends
    # TITLE_SECS of black past the end of BaseLayer.mov for it — so it is the only
    # thing here that never competes with him. A card laid over the last seconds of
    # the closing line would cover the face on the line that asks for the click.
    (161.30, 166.20, "card", "endcard", ""),
]

# Named on screen because a judge is asked to confirm them quickly, and read off the
# Terraform in `infra/terraform` and the embedding provider actually configured — not
# off an architecture diagram, which is where this kind of list usually goes stale.
PLATES: dict[str, dict[str, list[str]]] = {
    "stack": {
        "CockroachDB": [
            "VECTOR INDEX, cosine",
            "index prefix = tenant_id",
            "AS OF SYSTEM TIME",
            "CHANGEFEED",
            "REGIONAL BY ROW",
        ],
        "AWS": [
            # Kept short on purpose: at this column width the full model id
            # (cohere.embed-english-v3) wraps and knocks the two columns out of
            # alignment, which reads as a broken layout rather than a long name.
            "Bedrock — Cohere embed v3",
            "Lambda + Function URLs",
            "API Gateway v2, SQS",
            "S3, CloudFront, Route 53",
            "SES, ECR, CloudWatch",
        ],
    },
}

# How long a panel takes to come up and go down. Matches the scene edge fade so a
# panel and a diagram never cross-fade at different rates in the same frame.
FADE = 0.40


def active(t: float) -> bool:
    """Is a panel up at time t — including its fades, which is what the scene
    placement has to respond to."""
    return any(s - FADE <= t <= e + FADE for s, e, _, _, _ in CUES)


# How long the film runs past the end of the take, for the end card. `compose.py`
# owns the arithmetic; this is the number both it and the card's own timing use.
TITLE_SECS = 5.0


def shots() -> list[str]:
    return sorted({ref for _, _, kind, ref, _ in CUES if kind == "shot"})


def slugs() -> list[str]:
    return sorted({ref for _, _, kind, ref, _ in CUES if kind == "sql"})
