"""Serving the built console: the not-built page, and the two ways a path can lie.

None of these needs the cluster or an HTTP client. What is worth testing here is
path handling and a refusal, and both are functions.
"""

from __future__ import annotations

from rtf_platform import console_assets


# --------------------------------------------------------------- where it looks
#
# The bug these cover is not hypothetical: `/console` 404'd in production for as long
# as the build was only ever looked for at the checkout path, which cannot exist in a
# Lambda that has no `platform/` directory.

def test_the_bundled_build_is_inside_the_package() -> None:
    """The whole reason this path exists.

    `infra/build.sh` vendors `rtf_platform/` and nothing else. A build copied to a
    sibling directory is not in the archive, so the only location that can work in
    Lambda is one under the package itself.
    """
    package = console_assets.Path(console_assets.__file__).resolve().parent
    assert console_assets.BUNDLED.parent == package
    assert console_assets.BUNDLED.name == "console_dist"


def test_the_checkout_build_is_where_vite_writes() -> None:
    assert console_assets.CHECKOUT.parts[-3:] == ("platform", "console", "dist")


def test_the_bundled_build_is_searched_before_the_checkout() -> None:
    """Order matters in exactly one place — a developer who has both. The zip is
    what the deployed function runs, so it is what the deployed function trusts."""
    assert console_assets.SEARCHED == (console_assets.BUNDLED,
                                       console_assets.CHECKOUT)


def test_a_directory_without_a_shell_is_not_a_build(tmp_path, monkeypatch) -> None:
    """An interrupted build leaves the directory and no `index.html`. Serving out of
    it would 404 the shell, which reads as a broken deployment rather than a broken
    build."""
    empty, real = tmp_path / "empty", tmp_path / "real"
    empty.mkdir()
    real.mkdir()
    (real / "index.html").write_text("<!doctype html>")

    monkeypatch.setattr(console_assets, "SEARCHED", (empty, real))
    assert console_assets._dist() == real


def test_with_no_build_anywhere_it_names_the_checkout(tmp_path, monkeypatch) -> None:
    """`npm run build` writes to the checkout, so that is the path the instruction on
    the not-built page is about."""
    monkeypatch.setattr(console_assets, "SEARCHED", (tmp_path / "a", tmp_path / "b"))
    assert console_assets._dist() == console_assets.CHECKOUT


# ------------------------------------------------------------- the missing build

def test_not_built_page_is_503_and_says_what_to_run() -> None:
    """A 404 would tell a fresh checkout the console does not exist, and it would be
    believed. 503 says the address is right and the thing is not there yet."""
    page = console_assets.not_built_page()
    body = page.body.decode()

    assert page.status_code == 503
    assert "npm run build" in body
    assert str(console_assets.DIST) in body
    # It must not read as a general outage: the server-rendered console is fine.
    assert "Nothing is broken" in body


def test_not_built_page_names_every_place_it_looked() -> None:
    """A deployment and a checkout fail here for different reasons — a packaging step
    that did not run, against a build step that did not. Printing one path sends half
    the readers to fix the wrong thing."""
    body = console_assets.not_built_page().body.decode()
    for path in console_assets.SEARCHED:
        assert str(path) in body


def test_not_built_page_uses_the_design_system_ground() -> None:
    """It is the first page a new contributor sees at this address, so it should
    look like the product rather than like a stack trace."""
    body = console_assets.not_built_page().body.decode()
    assert 'data-ground="transmitter"' in body
    assert "#ffb340" in body  # phosphor


# ---------------------------------------------------------------- file vs route

def test_a_path_with_an_extension_is_a_file_request() -> None:
    assert console_assets.looks_like_a_file("assets/index-CK3A8p9q.js")
    assert console_assets.looks_like_a_file("favicon.ico")
    assert console_assets.looks_like_a_file("assets/index.COzAT5uq.css")


def test_a_client_route_is_not_a_file_request() -> None:
    """These must reach the application shell, or every deep link 404s on reload."""
    for route in ("", "campaigns", "campaigns/1a2b3c4d", "new"):
        assert not console_assets.looks_like_a_file(route), route


def test_a_dotted_route_segment_that_is_not_the_last_stays_a_route() -> None:
    """Only the last segment decides. A dot earlier in the path is not an extension."""
    assert not console_assets.looks_like_a_file("v1.2/campaigns")


# ----------------------------------------------------------------- containment

def test_resolve_asset_refuses_to_escape_dist(tmp_path) -> None:
    """The one that matters.

    `FileResponse` serves whatever `Path` it is given. Without the containment check
    a request for `/console/../../../../etc/passwd` is a read of any file this
    process can open, and it arrives looking like a request for a stylesheet.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("not for you")

    assert console_assets.resolve_asset("../" * 12 + "etc/passwd") is None
    assert console_assets.resolve_asset(f"../{secret.name}") is None
    assert console_assets.resolve_asset("../../../../../../etc/hosts") is None


def test_resolve_asset_returns_none_for_a_file_that_is_not_there() -> None:
    assert console_assets.resolve_asset("assets/never-built.js") is None


def test_resolve_asset_finds_a_real_file_when_the_console_is_built() -> None:
    """Skipped rather than failed on a checkout with no build — the not-built path is
    covered above, and this one is about the happy case being wired correctly."""
    index = console_assets.DIST / "index.html"
    if not index.is_file():
        import pytest
        pytest.skip("console is not built; run npm run build in platform/console")

    found = console_assets.resolve_asset("index.html")
    assert found is not None
    assert found.name == "index.html"
