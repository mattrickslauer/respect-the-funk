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
    """Every custom-property declaration in a chunk of CSS.

    Comments are stripped first, and that is not tidiness. The value pattern runs to
    the next `;` or `}`, so a token name written with a colon after it inside a
    comment — which is the natural way to mention one in prose — matches, and its
    "value" then runs on for hundreds of characters until the next brace, swallowing
    every real declaration in between. That failure is silent and reads as a missing
    token: the file plainly declares `--bg`, the test insists it does not.

    Cost a real half hour. The files these tests guard are heavily commented on
    purpose, so the parser has to cope with prose that talks about CSS.
    """
    css = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;}]+)", css)}


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


def test_the_console_takes_its_palette_from_the_system() -> None:
    """This test used to compare three of the console's own hex values against the
    system's and assert they matched. It passed for as long as somebody kept them in
    step by hand, which is the weaker of the two available guarantees.

    `_console.html` now includes `_design.html` and its short names are aliases, so the
    console cannot drift from the manual that documents it — there is nothing left to
    drift with. What is asserted here is that arrangement, because "include the shared
    system" is exactly the line a future edit deletes on the way to adding one
    convenient local colour.
    """
    console = (TEMPLATES / "_console.html").read_text(encoding="utf-8")

    assert '{% include "_design.html" %}' in console, (
        "the console must take its palette from the shared system, not carry one"
    )
    assert 'data-ground="transmitter"' in console, (
        "the console is a transmitter surface and does not follow the reader's setting"
    )

    root = _rule_body(console, ":root")
    con = _tokens(root)
    for long_name, short_name in PROVENANCE.items():
        assert con.get(f"--{short_name}") == f"var(--{long_name})", (
            f"--{short_name} must alias --{long_name} rather than redeclare it; "
            f"found {con.get(f'--{short_name}')!r}"
        )

    # One literal is how the fork restarts, so none are allowed to survive here.
    assert not re.search(r"#[0-9a-fA-F]{3,8}", root), (
        f"a literal colour is back in the console's root block: {root!r}"
    )


@pytest.mark.parametrize("ground", ["light", "dark"])
def test_the_system_defines_the_legend_in_both_grounds(ground: str) -> None:
    """The manual teaches ● ○ ◆ on paper; the console draws them on the transmitter
    ground. Both sets have to exist, or the manual is a legend for an instrument that
    renders nothing.

    The `--ground` assertion proves we grabbed the block we meant to before trusting
    anything in it. An earlier version matched `data-ground="transmitter"` where it
    first appears, which was inside a `:not(...)` guard on the dark media query rather
    than the transmitter rule itself — it passed, while checking the wrong thing.
    """
    design = _design_css()
    block = (_rule_body(design, ":root{") if ground == "light"
             else _rule_body(design, 'data-ground="transmitter"'))
    des = _tokens(block)

    expected_ground = "#faf8f4" if ground == "light" else "#06080c"
    assert des["--ground"] == expected_ground, (
        f"{ground}: matched the wrong rule in _design.html — "
        f"--ground is {des['--ground']}, expected {expected_ground}"
    )

    for long_name in PROVENANCE:
        assert des.get(f"--{long_name}", "").startswith("#"), (
            f"{ground}: --{long_name} is missing or is not a literal colour"
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
    # `_public.html` and `_console.html` were added here when they stopped carrying
    # palettes of their own. They are the two files most likely to regress: both
    # declare a block of aliases onto the system, and an alias whose target has been
    # renamed resolves to nothing and paints black on black without erroring.
    for name in ("landing.html", "manual.html", "_public.html", "_console.html"):
        page = (TEMPLATES / name).read_text(encoding="utf-8")
        declared = design | set(_tokens(page))
        used = {m.group(1) for m in re.finditer(r"var\((--[a-z0-9-]+)", page)}
        assert used <= declared, f"{name} uses undefined tokens: {sorted(used - declared)}"
