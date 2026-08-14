"""The public surface: the landing page, the operator manual, and the system they share.

Four palettes were typed out separately before `_design.html` existed, and they had
already begun to drift — the manual's dark accent and the landing's were the same hex
arrived at twice, and `_public.html`'s accent was the console's `--meas` arrived at a
third time. Extracting them into one file fixes today's drift. These tests are what
stops tomorrow's, because nothing else would: a colour that quietly diverges renders
perfectly and looks fine in isolation, and you only notice when you put two pages side
by side, which nobody does.

None of these need the cluster. They render templates directly through Jinja rather
than through the app, because the suite has no HTTP client and the thing under test is
the template and the token file, not the routing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).resolve().parent.parent / "rtf_platform" / "templates"


class _Principal:
    """Stand-in for `auth.Principal`. The public templates read these two and nothing
    else; if that stops being true these tests fail loudly rather than rendering a
    half-populated page, which is the point."""

    authenticated = False
    may_write = False


@pytest.fixture(scope="module")
def env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES)))


def _render(env: Environment, name: str, **kw: object) -> str:
    return env.get_template(name).render(request=None, principal=_Principal(), **kw)


def _tokens(block: str) -> dict[str, str]:
    """Every `--name:value` declaration in a chunk of CSS."""
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;}]+)", block)}


def _rule_body(css: str, marker: str, *, after: int = 0) -> str:
    """The brace-matched body of the rule whose selector contains `marker`.

    Splitting on the marker and reading to the end of the file is the obvious version
    and it is wrong: these files declare the same token names in three blocks, so a
    dict built from "everything after the marker" is a dict of whichever block came
    last. That mistake passes the light case and fails the dark one for a reason that
    looks like a palette bug, which is a bad hour. Match the braces instead.
    """
    start = css.index(marker, after)
    open_at = css.index("{", start)
    depth, i = 0, open_at
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1:i]
        i += 1
    raise AssertionError(f"unbalanced braces after {marker!r}")


def _design_css() -> str:
    return (TEMPLATES / "_design.html").read_text(encoding="utf-8")


# --------------------------------------------------------------- the manual ---

LIVE = {"counterparties": 14173, "with_genre": 8402}


def test_manual_renders_with_live_figures(env: Environment) -> None:
    html = _render(env, "manual.html", **LIVE)
    assert "14,173" in html
    assert "8,402" in html
    assert "count unavailable" not in html


def test_manual_states_the_cluster_did_not_answer_rather_than_guessing(
    env: Environment,
) -> None:
    """The load-bearing one.

    This document's own section eleven explains that the product refuses to show a
    figure it cannot stand behind. A manual that fell back to the number that was true
    the day it was written would be committing, in the document that explains the
    refusal, exactly the thing it refuses. So when the cluster does not answer there
    must be no number at all — not the live one, not a prose one, not a stale one.
    """
    html = _render(env, "manual.html", counterparties=None, with_genre=None)

    assert "count unavailable" in html, "the failure must be visible, not silent"
    assert "14,173" not in html
    # The figure this manual carried as prose before it was wired to the cluster.
    # If it comes back as a fallback, that is the regression.
    assert "fourteen thousand" not in html.lower()


def test_manual_carries_every_section(env: Environment) -> None:
    html = _render(env, "manual.html", **LIVE)
    for anchor in ("what", "before", "map", "setup", "contacts", "campaign", "marks",
                   "ask", "watch", "expect", "limits", "trouble", "glossary"):
        assert f'id="{anchor}"' in html, f"section {anchor} is missing"
        assert f'href="#{anchor}"' in html, f"section {anchor} is not in the contents"


# ------------------------------------------------- the legend and the instrument ---

PROVENANCE = {"measured": "meas", "inferred": "infr", "asserted": "asrt"}


@pytest.mark.parametrize("ground", ["light", "dark"])
def test_manual_legend_matches_what_the_console_draws(ground: str) -> None:
    """The manual teaches ● ○ ◆ and the console draws them. Same three colours, or the
    manual is a legend for a different instrument.

    `_console.html` abbreviates them (`--meas`) and `_design.html` spells them out
    (`--measured`); the names differ because the console's are read in a 336px pane and
    the system's are read by a person choosing one. The *values* may not differ.
    """
    console = (TEMPLATES / "_console.html").read_text(encoding="utf-8")
    design = _design_css()

    if ground == "light":
        # The console's light values live in its `prefers-color-scheme: light` media
        # query; the system's live in the bare `:root`, which is paper.
        console_block = _rule_body(console, ":root", after=console.index(
            "prefers-color-scheme: light"))
        design_block = _rule_body(design, ":root{")
    else:
        # The console is dark-first, so its dark values are the bare `:root` — the
        # first one in the file. The system's equivalent is the transmitter ground,
        # which is the one the console would pin if it included this file.
        console_block = _rule_body(console, ":root")
        design_block = _rule_body(design, 'data-ground="transmitter"')

    con, des = _tokens(console_block), _tokens(design_block)

    # Prove we grabbed the block we meant to before comparing anything in it.
    # An earlier version of this test matched `data-ground="transmitter"` where it
    # first appears, which was inside a `:not(...)` guard on the dark media query
    # rather than the transmitter rule itself. It passed — the two blocks agree on
    # these three colours — but it was checking the wrong thing, and would have gone
    # on passing if the transmitter block had drifted. A block assertion is cheap
    # and it is what makes the comparison below mean anything.
    expected_ground = "#faf8f4" if ground == "light" else "#080b11"
    assert des["--ground"] == expected_ground, (
        f"{ground}: matched the wrong rule in _design.html — "
        f"--ground is {des['--ground']}, expected {expected_ground}"
    )

    for long_name, short_name in PROVENANCE.items():
        assert f"--{short_name}" in con, f"console lost --{short_name}"
        assert con[f"--{short_name}"] == des[f"--{long_name}"], (
            f"{ground}: the console draws {long_name} as {con[f'--{short_name}']} "
            f"but the manual teaches {des[f'--{long_name}']}"
        )


# ---------------------------------------------------------------- the landing ---

STATS = {"counterparties": 14173, "embedded": 12000, "with_genre": 8402,
         "lessons": 3, "sent": 0, "micro_usd": 1234}


def test_landing_renders(env: Environment) -> None:
    html = _render(env, "landing.html", stats=STATS, roster_sizes=(), form={})
    assert "14,173" in html
    assert 'href="/manual"' in html, "the two public pages must link to each other"


def test_landing_pins_the_transmitter_ground() -> None:
    """The landing page is a room you monitor at night. It does not follow the reader's
    system setting the way the manual does, and the attribute is what says so."""
    html = (TEMPLATES / "landing.html").read_text(encoding="utf-8")
    assert 'data-ground="transmitter"' in html


def test_landing_defines_no_token_the_system_already_defines() -> None:
    """A page-local `--phosphor` would render identically and be a fork.

    The one permitted exception is `--crdb`: it is CockroachDB's trademark, it belongs
    to one page, and it is deliberately kept out of the shared file so nobody reaches
    for it as "the purple one".
    """
    landing = (TEMPLATES / "landing.html").read_text(encoding="utf-8")
    local = set(_tokens(landing.split("{% include")[1]))
    shared = set(_tokens(_design_css()))

    assert local - shared <= {"--crdb"}, f"unexpected page-local tokens: {local - shared}"
    assert local & shared == set(), (
        f"these are redefined locally and will drift: {sorted(local & shared)}"
    )


def test_no_public_template_uses_an_undefined_token() -> None:
    """Catches the rename that half-lands — `var(--void)` outliving `--void`.

    Every `var(--x)` referenced by a page that includes the design system must resolve
    to a declaration in that system or in the page itself. A `var()` that resolves to
    nothing does not error; it silently falls back to the initial value, which for a
    colour is usually black on black.
    """
    design = set(_tokens(_design_css()))
    for name in ("landing.html", "manual.html"):
        page = (TEMPLATES / name).read_text(encoding="utf-8")
        declared = design | set(_tokens(page))
        used = {m.group(1) for m in re.finditer(r"var\((--[a-z0-9-]+)", page)}
        assert used <= declared, f"{name} uses undefined tokens: {sorted(used - declared)}"
