"""Pages 1-3: the system map, and the operator's journey across it."""

from __future__ import annotations

from kit import (ASYNC, DIRECT, EXT, GATE, HOT, INK, MUTED, PROV, RULE, SYNC, W,
                 Canvas, chip, page)

TOTAL = 7


def m(s):
    return f'<span class="m">{s}</span>'


# ==============================================================================
# 1 — the whole system on one sheet
# ==============================================================================
def page_map():
    c = Canvas()

    c.band(24, 70, 210, 620, "who is calling")
    c.band(268, 70, 250, 620, "front doors")
    c.band(552, 70, 560, 620, "one FastAPI process  ·  hexagonal")
    c.band(1146, 70, 300, 620, "adapters — chosen in deps.py")
    c.band(1480, 70, 300, 620, "outside the boundary")
    c.band(24, 748, 1440, 300, "the asynchronous half — nothing slow happens in a request", ASYNC)
    c.band(1490, 748, 290, 300, "how to read the wires", MUTED)

    # -- actors ------------------------------------------------------------
    c.box("op", 38, 108, 182, 118, "Label operator", kind="actor",
          sub="the only role in scope",
          items=("Browser + httpOnly <b>rk_session</b> cookie",
                 "Registers, measures, previews, buys, approves"))
    c.box("pub", 38, 244, 182, 92, "Public visitor", kind="actor",
          sub="no session, ever",
          items=("Reads the landing page", "Checks a file at <b>/verify</b>"))
    c.box("api", 38, 354, 182, 92, "Script / integration", kind="actor",
          sub="Authorization: Bearer",
          items=("Same services, same gates", "JSON in, JSON out"))
    c.box("batchman", 38, 464, 182, 92, "Worker principal", kind="actor",
          sub='subject="worker"',
          items=("Minted inside the job, not by a login",
                 "Carries the tenant off the message"))
    c.note(38, 574, 182,
           "<b>Every</b> console handler asks for a <span class='m'>CurrentPrincipal</span>. "
           "That is the entire gate — no route contains an <span class='m'>if authenticated</span> branch.",
           MUTED, 11)

    # -- front doors -------------------------------------------------------
    c.box("landing", 282, 108, 222, 62, "<b>/</b> &nbsp;the landing page", kind="surface",
          sub="public, always", foot="the one route with no principal in its signature")
    c.box("verify", 282, 182, 222, 62, "<b>/verify</b>", kind="prov",
          sub="public, on purpose",
          foot="reads no tenant data — not under <span class='m'>/console</span> for that reason")
    c.box("login", 282, 256, 222, 62, "<b>/auth/login</b>", kind="surface",
          sub="six digits by email", foot="sets the signed session cookie")
    c.box("console", 282, 330, 222, 148, "<b>/console/…</b>", kind="surface",
          sub="~20 screens · ~70 htmx fragment routes",
          items=("roster · artist · identity builder",
                 "song · catalogue · settings",
                 "<b>generate</b> — the priced dry run",
                 "kit — polls one row every 3s"))
    c.box("apiv1", 282, 492, 222, 96, "<b>/api/v1/…</b>", kind="surface",
          sub="JSON · Bearer",
          items=("artists · identities · songs · sections",
                 "lyrics · kits · verify · auth"))
    c.box("files", 282, 602, 222, 72, "<b>/files/{key}</b>", kind="ghost",
          sub="local storage only",
          foot="never reached on B2 — and refuses anything under <span class='m'>tenants/</span>")

    # -- the process -------------------------------------------------------
    c.box("routes", 566, 100, 532, 56, "ui/routes.py &nbsp;+&nbsp; api/v1.py", kind="service",
          sub="two routers, one set of services",
          foot="a fragment route returns exactly the markup that changed")

    svc = [("artists", "roster · consent"), ("identities", "versions · frames"),
           ("songs", "masters · sections"), ("recipes", "the four formats"),
           ("briefs", "recipe → shots"), ("kits", "request() / run()"),
           ("analysis", "queue a measurement"), ("transcription", "queue a reading"),
           ("recommendations", "rank the hooks"), ("delivery", "embed on download"),
           ("verify", "read it back out"), ("accounts", "codes · sessions"),
           ("catalogue", "what is missing")]
    for i, (name, note) in enumerate(svc):
        col, row = i % 3, i // 3
        c.box(f"svc{i}", 566 + col * 182, 176 + row * 56, 168, 48,
              f"<b>{name}</b>", kind="service", sub=note, size="12.5")

    c.box("ports", 566, 460, 532, 100, "ports/ — the eight protocols", kind="plain",
          sub="services depend on these, never on a vendor",
          items=("Storage · DocumentRepository · Generator · JobQueue",
                 "AudioAnalyzer · Transcriber · Mailer · AuthProvider"))
    c.box("deps", 566, 578, 532, 78, "deps.py — the composition root", kind="gate",
          sub="the only file in the codebase that names a concrete adapter",
          foot="“laptop with no credentials” and “Lambda + SQS + Batch + B2 + GMI” differ by seven environment variables")

    # -- adapters ----------------------------------------------------------
    axes = [
        ("RK_STORAGE_BACKEND", "storage_local", "storage_b2", HOT),
        ("RK_GENERATOR_BACKEND", "mock (ffmpeg pixels)", "genblaze", EXT),
        ("RK_QUEUE_BACKEND", "queue_inline", "queue_sqs", ASYNC),
        ("RK_MAIL_BACKEND", "mailer_console", "mailer_zepto", SYNC),
        ("RK_AUTH_BACKEND", "anonymous", "otp", GATE),
        ("RK_ANALYSIS_BACKEND", "<i>no mock exists</i>", "audio_numpy", PROV),
        ("RK_TRANSCRIPTION_BACKEND", "<i>no mock exists</i>", "lyrics_elevenlabs", PROV),
    ]
    for i, (env, dev, prod, col) in enumerate(axes):
        c.box(f"ax{i}", 1160, 108 + i * 80, 272, 68, f"<span class='m'>{env}</span>",
              kind="plain", size="11",
              items=(f"<span style='color:{MUTED}'>dev</span> &nbsp;{dev}",
                     f"<span style='color:{col}'><b>prod</b></span> &nbsp;<b>{prod}</b>"))

    # -- outside -----------------------------------------------------------
    c.box("b2", 1494, 108, 272, 190, "Backblaze B2", kind="store",
          sub="every durable thing, one bucket",
          items=("<span class='m'>…/tenants/{t}/{collection}/{id}.yaml</span>",
                 "<span class='m'>…/masters/{t}/{song}.{ext}</span>",
                 "<span class='m'>…/tenants/{t}/identities/{id}/frames/…</span>",
                 "<span class='m'>runs/{t}/{date}/{run}/…</span> + manifest"),
          foot="no database tier anywhere in this design")
    c.box("gmi", 1494, 314, 272, 60, "GMI Cloud", kind="ext",
          sub="image today · video when unpinned", foot="seedream-5.0-lite")
    c.box("openai", 1494, 386, 272, 60, "OpenAI", kind="ext",
          sub="video — <b>pinned</b> here today", foot="sora-2")
    c.box("eleven", 1494, 458, 272, 60, "ElevenLabs", kind="ext",
          sub="audio, and Scribe for lyrics", foot="eleven_multilingual_v2 · scribe_v1")
    c.box("zepto", 1494, 530, 272, 56, "ZeptoMail", kind="ext",
          sub="the six-digit codes")
    c.box("ssm", 1494, 598, 272, 58, "AWS SSM Parameter Store", kind="ext",
          sub="read at boot, before settings are cached",
          foot="a literal <span class='m'>PLACEHOLDER …</span> counts as unset")

    # -- wires, top half ---------------------------------------------------
    c.edge("op:right@0.35", "console:left@0.3", color=SYNC, label="GET + htmx", bend=0.55)
    c.edge("op:right@0.72", "login:left", color=SYNC, bend=0.35)
    c.edge("pub:right@0.35", "landing:left", color=SYNC, bend=0.4)
    c.edge("pub:right@0.7", "verify:left", color=PROV, bend=0.6)
    c.edge("api:right", "apiv1:left@0.35", color=SYNC, label="Bearer", bend=0.5)

    for src in ("landing", "verify", "login", "console", "apiv1", "files"):
        c.edge(f"{src}:right", "routes:left@0.5" if src in ("landing", "verify", "login")
               else "routes:left@0.7", color=RULE, bend=0.55, width=1.4, arrow=False)
    c.edge("routes:bottom", "svc4:top", color=SYNC, bend=0.5, arrow=True, width=1.6)
    c.edge("ports:bottom", "deps:top", color=INK, bend=0.5, width=1.6, label="wired once, per process")
    c.edge("deps:right", "ax0:left", color=INK, bend=0.5, width=1.6, label="selects")

    c.edge("ax0:right", "b2:left@0.35", color=HOT, bend=0.5, width=2.4)
    c.edge("ax1:right", "gmi:left", color=EXT, bend=0.6, dashed=True)
    c.edge("ax1:right@0.7", "openai:left", color=EXT, bend=0.5, dashed=True)
    c.edge("ax1:right@0.85", "eleven:left", color=EXT, bend=0.4, dashed=True)
    c.edge("ax3:right", "zepto:left", color=SYNC, bend=0.5, dashed=True)

    # the two rails that go around the map rather than through it
    c.poly([(220, 108), (240, 108), (240, 48), (1630, 48), (1630, 108)],
           color=DIRECT, dashed=True, width=2.6,
           label="a master is uploaded browser → bucket on a presigned PUT — the bytes never enter compute",
           label_at=(935, 42))
    c.poly([(1766, 200), (1786, 200), (1786, 700), (300, 700), (300, 674)],
           color=HOT, dashed=False, width=2.2, arrow=True,
           label="presigned GET back to the browser", label_at=(1000, 690))

    # -- the async lane ----------------------------------------------------
    c.box("enq", 44, 790, 200, 96, "the fast half", kind="service",
          sub="<b>request()</b> — well under 200 ms",
          items=("validate · gate · write one document",
                 "enqueue with <b>dedupe_key</b>"))
    c.box("bus", 288, 790, 190, 96, "JobQueue", kind="queue",
          sub="SQS in prod · inline on a laptop",
          items=("same code either side",
                 "a queue that cannot execute refuses <i>before</i> a row is written"))
    c.box("dlq", 288, 916, 190, 62, "Dead-letter queue", kind="ghost",
          sub="after the redrive policy gives up")
    c.box("wrk", 522, 776, 230, 130, "worker.py", kind="worker",
          sub="AWS Batch · Fargate Spot · <span class='m'>minvCpus: 0</span>",
          items=("deliberately not a web process — Genblaze runs with <span class='m'>timeout=900</span>, "
                 "which <i>is</i> Lambda's ceiling",
                 "non-zero exit so a failure is recorded as one"))
    jobs = [("run-kit", "<span class='m'>--kit-id</span> · minutes · the expensive one"),
            ("analyze-song", "<span class='m'>--song-id</span> · a minute of CPU on a long master"),
            ("transcribe-song", "<span class='m'>--transcribe-song-id</span> · Scribe, direct")]
    for i, (name, note) in enumerate(jobs):
        c.box(f"job{i}", 796, 762 + i * 76, 250, 62, f"<b>{name}</b>", kind="worker",
              sub=note, size="13")
    c.box("wr", 1090, 790, 200, 96, "the document is rewritten", kind="store",
          sub="in place, same id",
          items=("status · assets · manifest_verified",
                 "measurements · lyrics · cost"))
    c.box("poll", 1310, 790, 140, 96, "the console<br>notices", kind="surface",
          sub="polling one row",
          foot="every 3s — no websocket, on purpose")

    c.edge("enq:right", "bus:left", color=ASYNC, width=2.2)
    c.edge("bus:right", "wrk:left", color=ASYNC, width=2.2)
    c.edge("bus:bottom", "dlq:top", color=MUTED, dashed=True, width=1.4)
    for i in range(3):
        c.edge("wrk:right@0.5", f"job{i}:left", color=ASYNC, bend=0.45, width=1.8)
    for i in range(3):
        c.edge(f"job{i}:right", "wr:left@0.5", color=HOT, bend=0.55, width=1.8)
    c.edge("wr:right", "poll:left", color=SYNC, width=1.8)
    c.poly([(1380, 886), (1380, 1010), (144, 1010), (144, 886)], color=MUTED,
           dashed=True, width=1.6,
           label="idempotent on the document id — a redelivered message is a no-op, and a finished kit that arrives twice costs nothing",
           label_at=(762, 1010))

    # -- legend ------------------------------------------------------------
    legend = [
        (SYNC, False, "request / response", "the ordinary path"),
        (HOT, False, "durable bytes", "everything that survives the process"),
        (ASYNC, False, "queued work", "never inside an HTTP request"),
        (DIRECT, True, "browser ↔ bucket", "presigned, never through compute"),
        (EXT, True, "a vendor call", "billed, and possibly billed on failure"),
        (PROV, False, "provenance", "the claim that travels in the file"),
        (GATE, False, "a refusal", "on purpose, and tested"),
    ]
    y = 786
    for col, dashed, name, note in legend:
        d = "6 4" if dashed else "none"
        c.decor.append(f'<path d="M1508,{y + 8} L1560,{y + 8}" stroke="{col}" stroke-width="3" '
                       f'stroke-dasharray="{d}" stroke-linecap="round"/>')
        c.note(1572, y - 2, 200, f"<b style='color:{col}'>{name}</b><br>"
                                 f"<span style='color:{MUTED}'>{note}</span>", MUTED, 10.5)
        y += 36

    return page(
        1, TOTAL, "RemixKit · the artist console",
        "The whole system on one sheet",
        "One process, seven configuration axes, one bucket, no database. "
        "Read left to right for a request; read the purple lane for everything a request refuses to do itself.",
        c,
        "Generated by <span class='m'>docs/architecture/render.py</span> from the running code — node labels carry the real routes, key layouts and settings.")


# ==============================================================================
# 2 & 3 — the journey, as lanes
# ==============================================================================
LANES = [
    ("what the operator<br>touches", "the surface, and the control on it", "surface", 116, 176),
    ("what the server<br>does", "service, and the gate it applies", "service", 300, 222),
    ("what is left<br>behind", "the residue — documents, objects, fields", "store", 532, 212),
    ("what is recorded<br>about <i>how</i>", "provenance: the claim, and its basis", "prov", 754, 148),
    ("what undoing it<br>costs", "reversibility, and what it cannot reach", "gate", 910, 142),
]

COL_X0, COL_W, COL_GAP = 190, 164, 176
HDR_X, HDR_W = 24, 150


def _journey(stations, num, eyebrow, title, standfirst, foot, rail_label):
    c = Canvas()
    for name, note, kind, y, h in LANES:
        c.box(f"lane{y}", HDR_X, y, HDR_W, h, name, kind="ghost", sub=note, size="14")

    for i, s in enumerate(stations):
        x = COL_X0 + i * COL_GAP
        c.box(f"h{i}", x, 30, COL_W, 72,
              f"<b>{s['name']}</b>", kind="actor", badge=s["n"], size="12.5",
              sub=f"<span class='m' style='font-size:9.6px'>{s.get('route', '')}</span>")
        for key, (_n, _note, kind, y, h) in zip(
                ("action", "server", "residue", "prov", "undo"), LANES):
            body = s[key]
            title_txt = body[0] if isinstance(body, tuple) else body
            items = body[1] if isinstance(body, tuple) and len(body) > 1 else ()
            c.box(f"{key}{i}", x, y, COL_W, h, title_txt, kind=kind, items=items,
                  size="11.5")
        if i:
            c.edge(f"h{i-1}:right", f"h{i}:left", color=INK, width=1.6)

    for _n, _note, kind, y, h in LANES:
        for i in range(len(stations)):
            x = COL_X0 + i * COL_GAP
            if y != LANES[0][3]:
                prev = LANES[[l[3] for l in LANES].index(y) - 1]
                c.link(x + COL_W / 2, prev[3] + prev[4], x + COL_W / 2, y,
                       aside="bottom", bside="top", color=RULE, width=1.2, arrow=True)

    c.poly([(COL_X0 - 14, 60), (COL_X0 - 14, 16), (COL_X0 + (len(stations) - 1) * COL_GAP + COL_W + 14, 16)],
           color=MUTED, dashed=True, width=1.4, arrow=False, label=rail_label,
           label_at=(COL_X0 + (len(stations) - 1) * COL_GAP / 2 + COL_W / 2, 20))

    return c, page(num, TOTAL, eyebrow, title, standfirst, c, foot)


def page_journey_a():
    stations = [
        dict(n="01", name="Sign in", route="POST /ui/auth/request-code → …/verify-code",
             action=("Types an email, reads a six-digit code, enters it.",
                     ("The console and the API share one token — the cookie is just where a browser keeps it.",)),
             server=("<b>accounts</b> — the allowlist <i>is</i> the user table",
                     ("<span class='m'>RK_ALLOWED_EMAILS</span> checked on request, on verify, "
                      "<b>and on every session read</b>",
                      "A refused address is told so — membership leaks, deliberately",
                      "Hashed (HMAC, keyed by email) · single-use · 10 min · 5 attempts")),
             residue=("<b>Written</b>",
                      ("<span class='m'>…/otp_challenges/{id}.yaml</span> — code_hash, expires_at, attempts",
                       "<span class='m'>…/accounts/{id}.yaml</span> — last_login_at",
                       "<b>No session row.</b> The cookie is a signed, stateless token")),
             prov=("Who, and when.",
                   ("<span class='m'>last_login_at</span> on the account",
                    "Removing an address ends the session at the next request, not at its expiry")),
             undo=("No revocation before expiry.",
                   ("TTL is a week. The panic button is rotating <span class='m'>RK_SESSION_SECRET</span>, "
                    "which signs everybody out at once",))),
        dict(n="02", name="Register an artist", route="POST /ui/artists · POST /api/v1/artists",
             action=("Name, slug, bio, links.",
                     ("An artist is a first-class entity, not a string on a song.",)),
             server=("<b>artists</b>.create",
                     ("The tenant is created by the first request that mentions it",
                      "Four built-in formats are seeded on first read — there is no migration step to hang a fixture on")),
             residue=("<b>Written</b>",
                      ("<span class='m'>…/tenants/{t}/artists/{art_…}.yaml</span>",
                       "<span class='m'>approval: draft</span>, empty consent",
                       "<span class='m'>…/recipes/*.yaml</span> ×4, once per tenant")),
             prov=("<span class='m'>tenant_id</span> is on the document, in the object key, and on the job payload.",
                   ("One tenant axis, everywhere — the thing BUILD-SPEC calls near-impossible to retrofit",)),
             undo=("Delete refuses while anything points at them.",
                   ("It names the counts. <span class='m'>cascade</span> is the caller saying yes — "
                    "and cascading runs <i>through</i> the owning services so each cleans its own objects",))),
        dict(n="03", name="Record likeness consent", route="POST /ui/artists/{id}/consent",
             action=("Ticks <i>granted</i>, records who signed and when, links the release.",
                     ("Refusal #1 in the product lives here.",)),
             server=("<b>artists</b>.set_consent",
                     ("This is not validation — it is a product rule, in the service layer, with a test",
                      "PRODUCT.md gap #3 inverted: a label wants explicit, auditable rights so the face <i>can</i> be held")),
             residue=("<b>Embedded on the artist</b>",
                      ("<span class='m'>consent.granted / signed_by / signed_at</span>",
                       "<span class='m'>consent.document_key</span> — the signed release in the bucket",
                       "<span class='m'>consent.notes</span>")),
             prov=("The audit record is the two fields.",
                   ("<span class='m'>signed_by</span> + <span class='m'>signed_at</span> are what a rights question is answered from",)),
             undo=("Withdrawal is as easy as granting —",
                   ("and takes effect on the <b>next</b> kit, never retroactively. Kits already rendered keep their assets",))),
        dict(n="04", name="Build the identity", route="/console/artists/{id}/identity",
             action=("Three columns at once: what the model is told, what it is shown, what was said before.",
                     ("chips for presentation · build · height, then face structure and wardrobe",
                      "reference frames, classed by six angles × lighting")),
             server=("<b>identities</b>.create_version",
                     ("Saving <b>mints a version</b> — within its own <i>line</i>, so a band's second member starts at v1",
                      "<span class='m'>compile()</span> renders ≤ <b>220 chars</b> by salience and drops clauses <b>whole</b>",
                      "<span class='m'>plates()</span> picks which frames a run may send")),
             residue=("<b>Written</b>",
                      ("<span class='m'>…/identities/{idn_…}.yaml</span> — one document <b>per version</b>",
                       "<span class='m'>…/identities/{id}/frames/{sha256[:16]}.ext</span> — the key <i>is</i> the content digest",
                       "The full <span class='m'>sha256</span> is kept on the frame, because an unhashed plate makes the manifest move on every render")),
             prov=("Version, angle, lighting, digest.",
                   ("<span class='m'>approval</span> per version",
                    "The compiled string, its length against the budget, and a count of what was refused are all on screen")),
             undo=("Full CRUD, with two rules that are easy to get wrong.",
                   ("Removing a frame deletes its object <b>only if</b> no sibling shares the key",
                    "Deleting a version deletes only unreferenced objects",
                    "Restore <b>copies forward</b>; it never rewinds in place"))),
        dict(n="05", name="Attach a song", route="POST /ui/artists/{id}/songs",
             action=("Title, slug, ISRC, Spotify link. A BPM only if it can say where it came from.",
                     ("Refusal #2 lives here.",)),
             server=("<b>songs</b>.create",
                     ("<b>No BPM without a method.</b> A field that silently accepts an unsourced <span class='m'>128</span> "
                      "hides the real bottleneck — catalogue onboarding — instead of measuring it",)),
             residue=("<b>Written</b>",
                      ("<span class='m'>…/songs/{sng_…}.yaml</span>",
                       "<span class='m'>bpm</span> + <span class='m'>bpm_method</span>, or neither")),
             prov=("The method line is the price of the number.",
                   ("FORMAT-SPEC requires the provenance of a measurement; this is that requirement in code",)),
             undo=("Delete removes the master object too,",
                   ("and refuses while a kit still references the song",))),
        dict(n="06", name="Upload the master", route="…/master/upload-url → PUT → …/master",
             action=("Drops an MP3, WAV, FLAC or M4A on the song page.",
                     ("The one console route that is not htmx — a presigned URL is JSON, and the browser does the PUT",)),
             server=("<b>songs</b>.presign_master / register_master",
                     ("The key is <b>derived</b>, never accepted: <span class='m'>{prefix}/masters/{t}/{song}.{ext}</span>",
                      "<span class='m'>register_master</span> re-checks the prefix — a caller naming any key could otherwise reach another tenant's object",
                      "and checks the bytes are actually there")),
             residue=("<b>Written</b>",
                      ("The master object — <b>uploaded browser → bucket</b>, never through compute",
                       "<span class='m'>song.master_key</span> + <span class='m'>master_content_type</span>, "
                       "recorded <i>after</i> the bytes land")),
             prov=("An abandoned upload leaves no claim.",
                   ("The key is deliberately not recorded at presign time — a song claiming a master it does not have "
                    "reads as “ready to analyse” on every screen that asks",)),
             undo=("Re-uploading reuses the same key.",
                   ("Deleting the song deletes the object",))),
        dict(n="07", name="Measure the master", route="POST …/songs/{id}/analysis",
             action=("Presses <i>Measure</i>. The panel polls until it lands.",
                     ("Tempo, the drop, the riser, the plateau, and the sections — from the audio, not from a form",)),
             server=("<b>analysis</b> → queued job <span class='m'>analyze-song</span>",
                     ("Two gates before the enqueue: a master whose bytes exist, and an analyser that can actually run",
                      "<b>There is no mock analyser.</b> A fabricated measurement is a float nothing downstream could tell from a real one",
                      "<span class='m'>worker.py --song-id …</span>")),
             residue=("<b>Written onto the song</b>",
                      ("<span class='m'>analysis</span> — bpm · beat_ms · downbeat_ms · drop_ms · riser_beats · "
                       "plateau_beats · bar_phase_confidence",
                       "<span class='m'>analysis.bars[]</span> — per-bar spectra, and a printable sheet",
                       "<span class='m'>sections[]</span> with <span class='m'>source: measured</span>")),
             prov=("Which adapter, how, and on which bytes.",
                   ("<span class='m'>analyzer</span>, <span class='m'>method</span> verbatim from the adapter, "
                    "<span class='m'>source_key</span>, and warnings")),
             undo=("Re-running replaces the measured sections.",
                   ("Idempotent in the way that matters — a redelivered message costs a minute of CPU and changes nothing",))),
        dict(n="08", name="Read the words off it", route="POST …/songs/{id}/transcription",
             action=("Presses <i>Transcribe</i>, then fixes what the model misheard — one line, or the whole sheet in a textarea.",
                     ("Lines under 0.6 confidence are flagged <b>check by ear</b> rather than hidden",)),
             server=("<b>transcription</b> → <span class='m'>transcribe-song</span>",
                     ("ElevenLabs Scribe, direct — no generator step to hang it on",
                      "Words become lines on three stated thresholds: a 900 ms gap, 42 characters, sentence-final punctuation",
                      "Re-transcribing <b>replaces</b>; a song with hand-corrected lines refuses and says how many would be lost")),
             residue=("<b>Written onto the song</b>",
                      ("<span class='m'>lyrics</span> — language, confidence, status",
                       "<span class='m'>lyrics.lines[]</span> — text, window, per-line confidence",
                       "<b>There is no mock transcriber</b> — a fabricated lyric is words attributed to an artist")),
             prov=("The thresholds are in the stored method line.",
                   ("Each line carries the geometric mean of its words' probabilities",
                    "Editing a line makes it <span class='m'>manual</span>, keeps the old method as history, "
                    "and <b>drops the confidence</b>")),
             undo=("Editing is free; re-reading is not.",
                   ("<span class='m'>force</span> is the caller saying yes anyway, and the console asks first",))),
        dict(n="09", name="Set the hooks", route="POST …/sections · …/sections/{id}/primary",
             action=("Adds, moves and labels sections. Picks the primary hook. Reads the ranked recommendations and their basis.",
                     ("A song has as many hooks as it has — one of them is primary",)),
             server=("<b>songs</b> + <b>recommendations</b>",
                     ("<span class='m'>song.hook</span> is mirrored onto the primary section, so the two surfaces cannot disagree",
                      "Rules from RHYTHM-STUDY §9 report pass, fail, or <b>not checkable</b> — the third state matters",
                      "No weighted total — a score would read as a quality judgement")),
             residue=("<b>Written onto the song</b>",
                      ("<span class='m'>sections[]</span> — role · window · bar_start/end · evidence",
                       "<span class='m'>primary_section_id</span>",
                       "Nothing is written by asking for a recommendation — it is computed on read")),
             prov=("Every window records where it came from.",
                   ("Moving a <span class='m'>measured</span> window makes it <span class='m'>manual</span> and keeps the old method as history",
                    "A dragged window still reading “measured” is that rule defeated by the edit form")),
             undo=("Freely editable —",
                   ("but a kit already bought stores <i>its own copy</i> of the windows, so editing cannot move what a finished kit claims",))),
    ]
    _c, html = _journey(
        stations, 2, "the journey · part one",
        "Onboarding, and everything the master is asked",
        "Nine touchpoints. Read down a column for one action and everything it leaves behind; "
        "read across a lane to compare what the system keeps.",
        "Two of these stations refuse by design — consent (03) and an unsourced BPM (05) — and both refusals live in the service layer, not in a form validator.",
        "each station is reached from the one before it, but nothing forces the order — the console shows what is missing instead")
    return html


def page_journey_b():
    stations = [
        dict(n="10", name="See the run before buying it", route="GET …/songs/{id}/generate",
             action=("Picks the format, the faces, which hooks, how many, and edits any shot's prompt, model, length or lock.",
                     ("Every control re-prices the page on change — htmx, still a GET to the same handler",
                      "The estimate sits outside the scroll — it must not scroll away from the button")),
             server=("<b>kits</b>.plan → <span class='m'>_resolve()</span>",
                     ("<b>One resolution path.</b> <span class='m'>plan()</span> displays the payloads and "
                      "<span class='m'>generate()</span> submits them — one function, two callers",
                      "Resolves the model that would answer, the composed prompt as the provider receives it, "
                      "the negatives, the snapped duration and the price",
                      "Sends nothing")),
             residue=("<b>Nothing.</b>",
                      ("The only station in the product that writes no document and no object",
                       "That is what makes it safe to open — and why three defects stayed invisible while no screen showed the payload")),
             prov=("The wire payload, on screen, before the money.",
                   ("A truncated mood fragment once reached the provider naming no song, no artist and no constraint against audio — "
                    "and nothing had “failed”",)),
             undo=("Nothing to undo.",
                   ("Clearing an edited field restores the generated default rather than sending an empty prompt",))),
        dict(n="11", name="Press the queue button", route="POST /ui/kits · POST /api/v1/kits → 202",
             action=("Commits the plan that is on the screen.",
                     ("Everything that alters the plan rides the button — a control that changes what is bought "
                      "without changing what was priced is the divergence this screen exists to prevent",)),
             server=("<b>kits</b>.request — four gates, in order",
                     ("<b>1</b> No recorded likeness consent → refused",
                      "<b>2</b> A queue that cannot execute → refused before a row is written",
                      "<b>3</b> Estimate over the kit budget or <span class='m'>RK_MAX_RUN_CENTS</span> → refused",
                      "<b>4</b> A brief that produces no shots → refused")),
             residue=("<b>Written</b>",
                      ("<span class='m'>…/kits/{kit_…}.yaml</span>, <span class='m'>status: queued</span>",
                       "and the <b>brief</b> — recipe_slug · hook windows <i>as bought</i> · shot_overrides by index · "
                       "identities by id, name <i>and</i> version · estimate_cents · which generator",
                       "One SQS message, <span class='m'>dedupe_key = kit.id</span>")),
             prov=("The brief freezes what was quoted.",
                   ("Windows are copied, not referenced — an edited section cannot change what a finished kit claims it was cut to",
                    "Faces by id <b>and</b> name, so “which member” needs no lookup")),
             undo=("Deleting the kit removes the bucket objects it owns,",
                   ("not just the document. Orphaned assets are storage nobody can reach",))),
        dict(n="12", name="The queue, and the worker", route="SQS → AWS Batch (Fargate Spot)",
             action=("Nothing. The operator is on another page.",
                     ("The kit page polls one row every three seconds",)),
             server=("<b>worker.py</b> — one job, then exit",
                     ("Deliberately not a web process: Genblaze runs with <span class='m'>timeout=900</span>, "
                      "which is exactly Lambda's ceiling with no headroom",
                      "<span class='m'>minvCpus: 0</span> — nothing runs, and nothing is billed, at rest",
                      "Exits non-zero, so a failure is recorded as one")),
             residue=("<b>Rewritten</b>",
                      ("<span class='m'>kit.status: queued → running</span>, then <span class='m'>ready</span> or <span class='m'>failed</span>",
                       "A message that outlives its retries lands in the DLQ",
                       "CloudWatch logs, 14-day retention")),
             prov=("Idempotent on the document id.",
                   ("A finished kit that is redelivered is a logged no-op and costs nothing",)),
             undo=("A failed kit keeps its error on the document",
                   ("and can be deleted; there is no partial resume",))),
        dict(n="13", name="The run", route="Genblaze Pipeline → providers",
             action=("Nothing — but this is where the money goes.",
                     ("A submitted step is a charged step. The only safe place to refuse was station 11",)),
             server=("<b>generator_genblaze</b>",
                     ("The compiled identity is <b>prepended to every prompt</b>; negatives ride as <span class='m'>negative_prompt</span>",
                      "<b>Two-stage identity lock:</b> a plate-conditioned still, then the clip generated from it via <span class='m'>input_from</span>",
                      "A still already in the index is <b>reused free</b> — nothing submitted, nothing billed")),
             residue=("<b>Written</b>",
                      ("Assets under <span class='m'>runs/{t}/{date}/{run_id}/…</span> — HIERARCHICAL, tenant as a path component",
                       "<span class='m'>LockedStill</span> appended to the <i>identity line</i>, not to the kit — its whole value is outliving the kit that paid for it",
                       "Every step's provider, model, prompt, params and cost")),
             prov=("Identity <b>versions</b> are inside the still digest.",
                   ("Saving a new version must <i>miss</i> the index rather than reuse a frame of how that person used to look",
                    "The scene is in the digest too, so a night-drive still is not offered for a golden-hour shot")),
             undo=("Assets are content-addressed.",
                   ("A re-run mints a new <span class='m'>run_id</span>; it does not overwrite the last one",))),
        dict(n="14", name="The manifest, and the gate", route="manifest.verify()",
             action=("Nothing.",
                     ("This is the step that decides whether the kit is allowed to exist",)),
             server=("<b>kits</b>.run — the readiness rule",
                     ("<span class='m'>ready</span> requires assets <b>and</b> a verified manifest",
                      "An unverified manifest means the provenance claim is unsupported — so the kit <b>fails</b> "
                      "rather than shipping a claim that cannot be backed")),
             residue=("<b>Written</b>",
                      ("The manifest object, beside the assets",
                       "<span class='m'>kit.run_id · manifest_key · manifest_verified · assets[] · total_cost_cents</span>",
                       "The stored object stays <b>byte-identical</b> to what the provider returned")),
             prov=("Unknown and free are different claims.",
                   ("<span class='m'>cost_cents: int | None</span> — an unpriced model estimates at "
                    "<span class='m'>UNKNOWN_CALL_USD</span>, never at zero",
                    "<span class='m'>cost_is_complete</span> shows a floor, not an invoice")),
             undo=("A failed manifest is not retried silently.",
                   ("The error is on the document and on the screen",))),
        dict(n="15", name="Review and approve", route="POST /ui/kits/{id}/approval",
             action=("Watches the row fill in, opens the assets, and decides.",
                     ("A label does not auto-publish AI content about its own artists",)),
             server=("<b>kits</b>.set_approval",
                     ("The human gate — a field, moved deliberately, by a person",
                      "The same gate exists on artists and on identity versions")),
             residue=("<b>Rewritten</b>",
                      ("<span class='m'>approval: draft → approved</span>",
                       "<span class='m'>updated_at</span>")),
             prov=("Approval is a state on the record,",
                   ("not an event log — this scope keeps no audit trail beyond the fields on the documents",)),
             undo=("Freely moved back.",
                   ("Approval gates publication, not generation — the money was already spent at 13",))),
        dict(n="16", name="Download", route="GET …/assets/{id}/download",
             action=("Takes the file.",
                     ("This is the moment the content leaves the system",)),
             server=("<b>delivery</b> — embed on delivery, not before",
                     ("The run's manifest is written <b>into the media itself</b>",
                      "<span class='m'>X-RemixKit-Provenance</span> says which happened",
                      "A media type with no embedding handler is delivered <i>unmodified</i> and the caller is told — "
                      "silently handing back an undisclosed file is the one failure this service exists to prevent")),
             residue=("<b>Nothing new in the bucket.</b>",
                      ("The stored object is untouched — that is what makes its content hash mean anything",
                       "The residue leaves with the operator: a file carrying its own record of which model made it, "
                       "from which prompt, for which release")),
             prov=("Disclosure now travels inside the file.",
                   ("Nothing has to be looked up, and nothing can be quietly stripped without the check failing",)),
             undo=("Unreachable.",
                   ("A downloaded file is gone. Deleting the kit later removes the stored copy, not the delivered one",))),
        dict(n="17", name="Verify — the public door", route="POST /api/v1/verify · POST /ui/verify",
             action=("Anyone drops the file back in. No account.",
                     ("A judge with no session can check a claim end to end",)),
             server=("<b>verify</b>",
                     ("Reads the manifest <i>out of the bytes</i>",
                      "Returns <span class='m'>verified: true</span>, <span class='m'>source: \"embedded\"</span>, "
                      "and the provider, model and prompt of every step",
                      "<b>Nothing is looked up.</b> It reads no tenant data at all")),
             residue=("<b>Nothing.</b>",
                      ("No document, no object, no session",
                       "Which is exactly why the route is not under <span class='m'>/console</span> — that prefix promises "
                       "a session check this handler does not perform")),
             prov=("This is the loop closing.",
                   ("Generate → verify → deliver embedded → read back. Tested as a loop, not as three assertions",)),
             undo=("n/a",
                   ("Strip the manifest and the check fails. That is the point",))),
        dict(n="18", name="Delete, and withdraw", route="DELETE /ui/… or /api/v1/…",
             action=("Removes an artist, a song, an identity version, a frame, a kit — or withdraws consent.",
                     ("The console asks by <i>name and count</i> rather than behind a generic “are you sure”",)),
             server=("Refuse, then cascade on request.",
                     ("Deleting an artist with dependents is refused and the counts are named",
                      "<span class='m'>cascade</span> runs <b>through the owning services</b>, so each dependent cleans up its own bucket objects",
                      "Silent orphaning is the worst of the three options")),
             residue=("<b>What survives is what something else references.</b>",
                      ("A frame's object survives if a sibling frame shares its key",
                       "A version's objects survive if any surviving version references them",
                       "Withdrawn consent leaves rendered assets alone")),
             prov=("Neither rule errors when it is wrong.",
                   ("Get them backwards and the rows remain while the images 404 — the shape of data loss that is hardest to notice",)),
             undo=("Nothing here is undoable.",
                   ("Identity <i>versions</i> are the exception — restoring an old one copies it forward as a new one",))),
    ]
    _c, html = _journey(
        stations, 3, "the journey · part two",
        "Buying a run, and what leaves the building",
        "Nine more touchpoints. Two of them write nothing at all — the dry run and the public verifier — "
        "and one of them is where every dollar in the system is spent.",
        "Station 10 is the whole cost-control argument: there is one resolution path, so the payload displayed before the button is the payload submitted after it.",
        "the operator is only present for five of these nine — the rest is a worker, a manifest, and a file that carries its own record")
    return html
