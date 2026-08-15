"""What just changed, said once, so every region that draws it can re-read itself.

The console is a set of fragments that each render one region, and a mutation used to
repaint only the region its own form targeted. Everything else on the page went stale
until somebody navigated. Attaching a song left the nav tree, the artist page's Songs
count, the tab strip and the catalogue all describing a roster that no longer existed —
each fragment individually correct, the screen as a whole wrong.

So a mutation now says which *collection* it touched, and the regions drawn from that
collection re-read themselves. The names below are the entire vocabulary.

They name records rather than widgets, deliberately. A widget is a thing that gets
redesigned, and an event named after one either outlives it as a lie or has to be renamed
in the twenty places that fire it. `SONGS` will still mean "the songs changed" after the
song list becomes something else.

`SONGS` and `SONG` are separate for the same reason the routes are: adding or renaming a
song changes what every *other* screen lists, while measuring one changes only the page
you are standing on. Firing the broad one for an edit to a hook window would repaint the
nav tree on every keystroke-sized save, which is how a live console becomes a slow one.

Delivery is the `HX-Trigger` response header — one header on a response the browser was
already waiting for. No socket, no poll, no client-side state. This does **not** revisit
BUILD-SPEC §2b's deferral of real-time websockets: nothing here pushes to a browser that
did not just ask for something, so a second person's edit still arrives on your next
navigation. What it fixes is the acting user's own screen, which is where every one of
these staleness bugs was actually seen.

The job events already in the console (`rk:analysis-done`, `rk:lyrics-done`) are left
alone. They announce a queued job reaching a terminal state, which is a different fact
from a person editing a record, and the regions that care are not the same ones.
"""

from __future__ import annotations

from starlette.responses import Response

#: The roster changed — an artist added, removed, renamed, or their consent or approval
#: moved. Consent belongs here rather than in a event of its own because it is the one
#: fact the roster, the nav dot and the catalogue's blocked count all draw.
ARTISTS = "rk:artists"

#: Which songs exist, or what one is called. Not what is *in* one — see `SONG`.
SONGS = "rk:songs"

#: The content of one song: its measurement, hook window, sections, lyrics or master.
#: Scoped to the song page's own regions, which is the only screen that draws them.
SONG = "rk:song"

#: A kit was requested, deleted, or approved.
KITS = "rk:kits"

#: An identity version, a reference frame, or an approval on one.
IDENTITY = "rk:identity"

#: Every event this module defines, in the order a page should listen for them. The
#: layout renders this into one `hx-trigger` list, so a new event becomes live on every
#: screen by being added here rather than by being added to five templates.
ALL = (ARTISTS, SONGS, SONG, KITS, IDENTITY)


def announce(response: Response, *changed: str) -> Response:
    """Tell the browser which collections this response changed.

    Returns the response so it can be stamped on the way out of a handler
    (`return announce(_render(...), events.SONGS)`) rather than needing a temporary.

    Merges rather than assigns. Two routes already set `HX-Trigger` for their own job
    events, and a handler that both finishes a job and edits a record would otherwise
    silently drop one of the two — the kind of bug that shows up as "it updates except
    when…" months later.

    Only ever called on success. A refusal changed nothing, so announcing on the error
    path would send every listening region to re-read state that did not move, which on
    the roster is three repository scans to redraw an identical screen.
    """
    existing = [name.strip() for name in response.headers.get("HX-Trigger", "").split(",")]
    names = [name for name in existing if name]
    for event in changed:
        if event not in names:
            names.append(event)
    if names:
        response.headers["HX-Trigger"] = ", ".join(names)
    return response
