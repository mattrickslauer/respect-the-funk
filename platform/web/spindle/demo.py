"""The console's view vocabulary — blocks, navigation and the row selector.

Every view in the product is described with the four dataclasses below: a `View` carries
its columns, rows and stats; a `Section` is one block in the inspector; a `Field` is one
input in a form; a `Col` is one column. The templates render these and nothing else, so
adding a view costs a builder in `research.py` and a route, not a screen.

**This file used to be the fixtures, and the name is a leftover.** Thirteen console
views were built against invented data first, deliberately: a layout, an information
hierarchy and an inspector have to be judged before the tables behind them exist, and
judging them from a spec does not work. The promise made at the time was that when a
table landed the fixture would become a query and the templates would not change.

That promise was kept and then collected. Every view now reads the cluster — `research.py`
builds all thirteen — so the fixture data was deleted rather than left to rot beside the
queries that replaced it. A fixture nothing renders is worse than no fixture: it looks
maintained, it drifts from the schema silently, and it is a standing invitation to wire a
screen back to it the next time a query is inconvenient.

Read the deleted fixtures with `git show 14073d9:platform/web/spindle/demo.py` if
you want the design record. They are worth reading for the *shapes* — column names,
provenance classes, lead kinds, thread states, lease fields and budget units all came
from `docs/PLATFORM-SPEC.md` and the agent contract design, which is why the switch to
real queries moved no templates.

What stayed is what was never fiction: the block vocabulary, the four nav groups, and
`select`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------- navigation

#: (group, [(key, label, href, badge)]). Four groups because the operator arrives in
#: one of four moods: something needs me, what do we know, what are we running, is it
#: healthy. Nav that mirrors the mood is faster than nav that mirrors the schema.
NAV: tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...] = (
    ("Work", (
        ("today",     "Today",          "/",               ""),
        ("approvals", "Approvals",      "/approvals",      "3"),
        ("inbox",     "Inbox",          "/inbox",          "7"),
    )),
    ("Knowledge", (
        ("artists",   "Artists",        "/artists",        ""),
        ("tracks",    "Tracks",         "/tracks",         ""),
        ("imports",   "Statements",     "/imports",        ""),
        ("facts",     "Facts",          "/facts",          ""),
        ("counter",   "Counterparties", "/counterparties", ""),
        # Under Knowledge and not System, because what it answers is the catalogue
        # rather than the machinery — an operator asking "how many curators do we have"
        # is doing the same thing the four screens above it are for, in a sentence.
        ("ask",       "Ask",            "/ask",            ""),
    )),
    ("Campaigns", (
        ("campaigns", "Campaigns",      "/campaigns",      ""),
        ("threads",   "Threads",        "/threads",        ""),
    )),
    ("System", (
        ("fleet",     "Fleet",          "/fleet",          ""),
        ("queue",     "Queue",          "/queue",          ""),
        ("runs",      "Runs & errors",  "/runs",           "4"),
        ("budgets",   "Budgets",        "/budgets",        "!"),
    )),
)

#: The scope switcher. Renders `scope_kind` from the contract design §7 as a control:
#: tenant-wide work genuinely has no artist, so "All artists" is a real scope and not a
#: filter that happens to be empty.
SCOPES: tuple[tuple[str, str, str], ...] = (
    ("all",           "All artists",   "tenant"),
    ("hallow-youth",  "Hallow Youth",  "artist"),
    ("amanda-kurt",   "Amanda Kurt",   "artist"),
    ("just-one-branch", "Just One Branch", "artist"),
)


# -------------------------------------------------------------- view plumbing

@dataclass(frozen=True)
class Col:
    """One column. `cls` drives alignment and typeface, not colour."""
    key: str
    label: str
    cls: str = ""
    width: str = ""


@dataclass(frozen=True)
class Field:
    """One input in a `form` section.

    Deliberately not a Pydantic model or a WTForms field: this describes what to
    *render*, and the server validates the post independently against `domain`.
    A field descriptor that also claimed to validate would invite trusting it, and
    the browser is not where the closed sets are enforced.
    """
    name: str
    label: str
    kind: str = "text"                  # text | url | select | static | file | check
    value: str = ""
    #: Grouped `((group, ((value, label), ...)), ...)`. A group of "" renders with no
    #: <optgroup>, so a flat select and a grouped one are the same structure.
    options: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    hint: str = ""
    required: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class Section:
    """One block in the inspector.

    `kind` decides rendering: `kv` is a definition list, `chain` is the provenance
    walk with its indent rails, `quote` is verbatim evidence, `note` is prose,
    `actions` is a row of buttons, `form` is an editor that posts. The inspector is
    the only place in the product where an operator can act on a single object, so
    the actions — and now the writes — belong to it.
    """
    title: str
    kind: str
    items: tuple[Any, ...] = ()
    #: `form` only. Where it posts, what the submit button says, and whether it is
    #: styled as destructive. Empty on every other kind.
    action: str = ""
    submit: str = "Save"
    tone: str = ""                      # "" | "danger"
    #: `form` only. Rendered above the fields when the last post was rejected, so the
    #: operator sees why next to what they typed rather than on a separate error page.
    error: str = ""
    #: `form` only. Set when the form carries a file — a multipart form with a text
    #: encoding sends the filename and drops the bytes, which looks like an empty
    #: upload rather than a mistake.
    multipart: bool = False
    #: `form` only. Rendered above the fields as neutral prose — a preview, a result,
    #: anything that is neither an error nor a field.
    note: str = ""


@dataclass(frozen=True)
class View:
    key: str
    title: str
    blurb: str
    stats: tuple[tuple[str, str, str], ...]      # (label, value, bar-or-spark)
    cols: tuple[Col, ...]
    rows: tuple[dict[str, Any], ...]
    empty: str = "Nothing here."
    dense: bool = True


def _bar(pct: int) -> str:
    """A ten-cell bar. Rendered as text so it copies out of the page intact and
    needs no chart library in a Lambda that ships as a 24MB zip."""
    filled = round(pct / 10)
    return "█" * filled + "░" * (10 - filled)


def select(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]],
           sel: str | None) -> dict[str, Any] | None:
    """The row the inspector is showing. Defaults to the first, so the third pane is
    never empty on arrival — an empty inspector teaches nothing about the layout."""
    if not rows:
        return None
    for row in rows:
        if row["id"] == sel:
            return row
    return rows[0]
