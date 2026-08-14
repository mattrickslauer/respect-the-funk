"""Serving the built React console, and saying so when it has not been built.

The console at `platform/console` is a Vite application that compiles to static
files. This module is what puts them behind `/console`, and it is a separate module
rather than four lines in `main.py` for one reason: the interesting behaviour here is
what happens when the build is *absent*, and that behaviour deserves somewhere to be
explained and somewhere to be tested.

## Why not `if dist.exists(): app.mount(...)`

That is the obvious version and it is wrong in the specific way this codebase keeps
refusing. A conditional mount means that on a checkout where nobody has run
`npm run build`, `/console` answers 404 — the same 404 as a typo, as a deleted route,
as a bad deployment. The operator learns nothing, and the most likely reader of that
404 is a person who has just cloned the repository and has no reason to suspect a
missing build step.

So the mount is unconditional and the *handler* is what changes. With a build, files.
Without one, a page that says the console has not been built and gives the command.
The failure is loud, legible, and tells you what to do, which is the whole of this
project's position on empty states.

## Why a catch-all rather than `StaticFiles(html=True)`

`html=True` serves `index.html` for a directory request, which covers `/console` and
`/console/` and nothing else. The console is a client-routed application: `/console/
campaigns/<id>` is a real address a person will bookmark, paste into Slack, and
reload. Nothing exists on disk at that path, so `StaticFiles` alone answers 404 for
every deep link and the application appears to work until the first refresh.

The rule is the standard one for a single-page application, with one qualification
that matters here: a request for something that *looks like a file* — anything with an
extension — must keep 404ing rather than falling through to `index.html`. Without that
qualification a missing chunk answers 200 with HTML, the browser tries to parse markup
as JavaScript, and the console fails with a syntax error that names a line in a file
that was never served. That is a genuinely horrible hour, and it is one `if` to avoid.

## Deployment

`handler.py` runs this under Mangum in Lambda, which means `dist/` has to be inside
the deployment package for `/console` to serve anything there. It is not today. Until
the packaging step includes it, a deployed `/console` will correctly render the
not-built page rather than pretending. That is the honest state and it is visible
rather than silent, which is the point of the arrangement above.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

#: `platform/console/dist`, resolved from this file rather than the working
#: directory, because the app is started from at least three of them.
DIST = Path(__file__).resolve().parent.parent.parent / "console" / "dist"

_NOT_BUILT = """<!doctype html>
<html lang="en" data-ground="transmitter">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Console not built — Respect the Funk</title>
<style>
  body{{margin:0;background:#080b11;color:#e9eef6;
       font:15px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif;
       display:grid;place-items:center;min-height:100vh}}
  main{{max-width:52ch;padding:2rem}}
  h1{{font:600 19px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;
      letter-spacing:-.02em;margin:0 0 .8rem;color:#ffb340}}
  p{{color:#8493a6;margin:.8rem 0 0}}
  code{{display:block;background:#0f151e;border:1px solid #1c2531;border-radius:6px;
        padding:.7rem .9rem;margin-top:.9rem;color:#35d0c0;
        font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}}
  a{{color:#ffb340}}
</style>
</head>
<body><main>
  <h1>The console has not been built.</h1>
  <p>This address serves a compiled application, and there is nothing compiled at
     <code>{dist}</code></p>
  <p>Build it:</p>
  <code>cd platform/console &amp;&amp; npm install &amp;&amp; npm run build</code>
  <p>The server-rendered console is unaffected and is at
     <a href="/">the front page</a>. Nothing is broken.</p>
</main></body>
</html>
"""


def not_built_page() -> HTMLResponse:
    """The page served in place of the console when `dist/` is not there.

    503 rather than 404: the address is right and the thing is temporarily not
    available, which is exactly what the status code means. A 404 would say the
    console does not exist, and a reader who has just cloned the repository would
    believe it.
    """
    return HTMLResponse(_NOT_BUILT.format(dist=DIST), status_code=503)


def looks_like_a_file(spa_path: str) -> bool:
    """Whether this path names a file rather than a client route.

    An extension on the last segment is the signal. `campaigns/1a2b` is a route;
    `assets/index-CK3A8p9q.js` is a file. Deliberately not a whitelist of known
    extensions: the point is to *never* answer a file request with the application
    shell, and an unknown extension is still a file request.
    """
    return "." in spa_path.rsplit("/", 1)[-1]


def resolve_asset(spa_path: str) -> Path | None:
    """The file `spa_path` names inside `DIST`, or None.

    None covers three cases that must all 404 rather than fall through to the shell:
    the file is not there, the path escaped `DIST`, or it resolved to something that
    is not a regular file.

    The containment check is after `resolve()` and not before, because that is the
    only order that catches `..`. Without it a request for
    `/console/../../../../etc/passwd` is a read of any file this process can open —
    `FileResponse` will happily serve it.
    """
    root = DIST.resolve()
    candidate = (root / spa_path).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


def mount(app: FastAPI, *, path: str = "/console") -> bool:
    """Put the console behind `path`. Returns whether a build was found.

    The return value is for the caller to log or test with; nothing branches on it
    to decide whether to register routes, because the not-built case is served
    rather than omitted.
    """
    built = (DIST / "index.html").is_file()

    if built:
        # Hashed assets under /assets, served by StaticFiles because it does
        # conditional requests and ranges properly and this does not need to.
        app.mount(f"{path}/assets",
                  StaticFiles(directory=DIST / "assets"), name="console-assets")

    @app.get(path, include_in_schema=False)
    @app.get(path + "/{spa_path:path}", include_in_schema=False)
    def console(request: Request, spa_path: str = "") -> Response:  # noqa: ARG001
        if not built:
            return not_built_page()

        # A request that names a file must not be answered with the application
        # shell. See the module docstring: a missing chunk served as HTML fails as a
        # JavaScript syntax error pointing at a file that was never sent.
        if looks_like_a_file(spa_path):
            found = resolve_asset(spa_path)
            return FileResponse(found) if found else Response(status_code=404)

        return FileResponse(DIST / "index.html")

    return built
