"""Pages 4-7: the residue ledger, the pipeline, the deployment, and the trust argument."""

from __future__ import annotations

from kit import (ASYNC, DIRECT, EXT, GATE, HOT, INK, MUTED, PROV, RULE, SYNC,
                 Canvas, esc, page)

TOTAL = 7


def table(cols, rows, widths, cls="tbl"):
    head = "".join(f'<th style="width:{w}%">{c}</th>' for c, w in zip(cols, widths))
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


# ==============================================================================
# 4 — the residue ledger
# ==============================================================================
def page_residue():
    c = Canvas()

    # -- the key space -----------------------------------------------------
    c.band(24, 66, 690, 424, "one bucket · the whole key space", HOT)
    tree = [
        ('<span class="kp">remixkit/</span>', "← <span class='m'>RK_KEY_PREFIX</span>"),
        ('├── <span class="kp">tenants/</span><span class="v">{tenant_id}</span>/',
         "← the partition key, in the path"),
        ('│   ├── artists/<span class="v">{art_…}</span>.yaml', "Artist · consent · approval"),
        ('│   ├── identities/<span class="v">{idn_…}</span>.yaml', "one document <b>per version</b>"),
        ('│   ├── identities/<span class="v">{idn_…}</span>/frames/', ""),
        ('│   │       <span class="v">{sha256[:16]}</span>.png', "the key <b>is</b> the content digest"),
        ('│   ├── songs/<span class="v">{sng_…}</span>.yaml', "sections · analysis · lyrics"),
        ('│   ├── kits/<span class="v">{kit_…}</span>.yaml', "brief · assets · ledger"),
        ('│   ├── recipes/<span class="v">{rcp_…}</span>.yaml', "four, seeded per tenant on first read"),
        ('│   ├── accounts/<span class="v">{id}</span>.yaml', "last_login_at"),
        ('│   └── otp_challenges/<span class="v">{id}</span>.yaml', "code_hash · expires_at · attempts"),
        ('│', ""),
        ('├── <span class="kp">masters/</span><span class="v">{tenant_id}</span>/', ""),
        ('│       <span class="v">{sng_…}</span>.{wav|mp3|flac|m4a}', "browser → bucket, presigned PUT"),
        ('│', ""),
        ('└── <span class="kp">runs/</span><span class="v">{tenant_id}</span>/'
         '<span class="v">{date}</span>/<span class="v">{run_id}</span>/', ""),
        ('        …assets…', "byte-identical to the provider's output"),
        ('        manifest.json', "the claim, and its canonical hash"),
    ]
    tree_html = ('<table class="tree2">' + "".join(
        f'<tr><td class="p">{path}</td><td class="c">{note}</td></tr>' for path, note in tree)
        + "</table>")
    c.html(44, 94, 650, tree_html)
    c.note(44, 418, 650,
           "<b>Three prefixes, one tenant axis.</b> Documents, masters and run output share it, so a tenant is a "
           "path component rather than a column — the thing BUILD-SPEC §2b calls near-impossible to retrofit. "
           "<span class='m'>/files/{key}</span> exists only on local storage, and refuses anything under "
           "<span class='m'>tenants/</span> outright: the YAML is the database, not a file to be served.",
           MUTED, 11.5)

    # -- what a single session leaves --------------------------------------
    c.band(24, 522, 690, 526, "reversal · what each undo can and cannot reach", GATE)
    c.box("d_art", 60, 592, 180, 66, "Delete an artist", kind="gate",
          sub="refused while dependents exist")
    c.box("d_songs", 300, 568, 150, 54, "songs", kind="store", size="12.5")
    c.box("d_idns", 300, 630, 150, 54, "identities", kind="store", size="12.5")
    c.box("d_kits", 300, 692, 150, 54, "kits", kind="store", size="12.5")
    c.box("d_obj", 512, 568, 174, 54, "master objects", kind="store", size="12", sub="one per song")
    c.box("d_obj2", 512, 630, 174, 54, "frame objects", kind="store", size="12",
          sub="only if no sibling shares the key")
    c.box("d_obj3", 512, 692, 174, 54, "assets + manifest", kind="store", size="12",
          sub="the run output the kit owns")
    c.edge("d_art:right", "d_songs:left", color=GATE, label="cascade", bend=0.5)
    c.edge("d_art:right", "d_idns:left", color=GATE, bend=0.5)
    c.edge("d_art:right", "d_kits:left", color=GATE, bend=0.5)
    c.edge("d_songs:right", "d_obj:left", color=HOT, bend=0.5)
    c.edge("d_idns:right", "d_obj2:left", color=HOT, bend=0.5)
    c.edge("d_kits:right", "d_obj3:left", color=HOT, bend=0.5)
    c.note(60, 764, 626,
           "<b>Cascading goes through the owning services, never through the repository.</b> "
           "That is the only reason each dependent cleans up its own bucket objects. Deleting the rows "
           "directly is the cheap implementation and the one that leaves the label paying storage for objects "
           "no screen can reach.", MUTED, 11.5)
    c.html(60, 828, 626, table(
        ["what a user can undo", "what it reaches", "what it cannot"],
        [["Withdraw likeness consent",
          "the <b>next</b> kit — refused at request time",
          "anything already rendered, delivered or downloaded"],
         ["Delete an identity version",
          "objects <i>no surviving version</i> references",
          "a kit that recorded that version in its brief"],
         ["Remove a reference frame",
          "its object, if no sibling frame shares the digest",
          "a still already in the locked-still index"],
         ["Edit a section window",
          "the song, and every future kit",
          "a finished kit — it stored its own copy of the windows"],
         ["Delete a kit",
          "the document, its assets and its manifest",
          "a file already downloaded, which carries its own manifest"],
         ["Rotate <span class='m'>RK_SESSION_SECRET</span>",
          "every session, at once",
          "nothing else — sessions are signed, not stored"]],
        [26, 37, 37]))

    # -- the collections table ---------------------------------------------
    c.band(748, 66, 1032, 452, "every durable record, and the residue on it", INK)
    c.html(768, 96, 992, table(
        ["document", "written when", "the residue that matters", "provenance carried"],
        [["<b>Artist</b>", "registration",
          "slug · name · links · <b>consent</b> · approval",
          "<span class='m'>signed_by</span>, <span class='m'>signed_at</span>, "
          "<span class='m'>document_key</span> of the release"],
         ["<b>Identity</b> <span class='cm'>(one per version)</span>", "every save",
          "line name · version · silhouette enums · wardrobe · negatives · "
          "<b>reference_frames[]</b> · <b>locked_stills[]</b>",
          "version within its line · per-frame <span class='m'>angle</span>, "
          "<span class='m'>lighting</span>, <span class='m'>sha256</span>"],
         ["<b>Song</b>", "attach, then measured / heard",
          "bpm · drop_ms · <b>sections[]</b> · <b>analysis</b> · <b>lyrics</b> · master_key",
          "<span class='m'>bpm_method</span> · per-section <span class='m'>source</span> + "
          "<span class='m'>method</span> · per-line <span class='m'>confidence</span>"],
         ["<b>SongAnalysis</b>", "the worker finishes",
          "bpm · beat_ms · downbeat_ms · drop_ms · riser_beats · plateau_beats · "
          "bar_phase_confidence · bars[] · sheet",
          "<span class='m'>analyzer</span> · <span class='m'>method</span> verbatim · "
          "<span class='m'>source_key</span> · warnings"],
         ["<b>SongLyrics</b>", "the worker finishes",
          "language · lines[] with windows · status",
          "<span class='m'>transcriber</span> · the three grouping thresholds, in the method line · "
          "per-line confidence, dropped on edit"],
         ["<b>Recipe</b>", "seeded per tenant, then copied",
          "prompt_template · negatives · aspect · length policy · face policy · variants",
          "<span class='m'>builtin</span> — the four that refuse to be deleted"],
         ["<b>Kit</b>", "the queue button",
          "status · <b>brief</b> · assets[] · manifest_key · <b>manifest_verified</b> · "
          "total_cost_cents · approval",
          "run_id · hook windows <i>as bought</i> · identities by id + name + version · "
          "estimate at purchase"],
         ["<b>Asset</b> <span class='cm'>(on the kit)</span>", "each provider call returns",
          "modality · key · <b>cost_cents: int | None</b> · params",
          "<span class='m'>provider</span> · <span class='m'>model</span> · "
          "<span class='m'>prompt</span> · <span class='m'>reference_keys</span> · "
          "<span class='m'>derived_from</span>"],
         ["<b>Account</b> / <b>OtpChallenge</b>", "sign-in",
          "email · last_login_at · code_hash · attempts",
          "the allowlist is the user table — there is no registration"]],
        [12, 13, 39, 36]))

    c.band(748, 578, 1032, 470, "what the residue is for", PROV)
    c.html(768, 610, 992, table(
        ["claim", "what it rests on", "and what would defeat it"],
        [["“this window was measured”",
          "<span class='m'>section.source = measured</span> plus the analyser's own method line",
          "an edit form that leaves the label alone — so moving a measured window makes it "
          "<span class='m'>manual</span> and keeps the old method as history"],
         ["“this lyric is what the model heard”",
          "per-line confidence, and lines under 0.6 shown as <b>check by ear</b>",
          "hiding the low-confidence lines, which makes a transcript look finished when the number says it is not"],
         ["“this is the same person as last time”",
          "the identity <i>version</i> in the still digest",
          "a cache keyed on the face alone, which would quietly reuse a frame of how they used to look"],
         ["“this kit was cut to that hook”",
          "the windows copied into the brief at purchase",
          "storing section ids, which lets a later edit change what a finished kit claims"],
         ["“this clip cost 42¢”",
          "one dated price book registered into Genblaze's own registry, read by both the estimate and the ledger",
          "a flat per-modality table — which quoted $1.26 for a run that charged several dollars"],
         ["“this file was made by that model”",
          "the run manifest embedded in the media at delivery, verified before the kit was allowed to be ready",
          "embedding at generation time, which would break the content hash the manifest is built on"],
         ["“nothing was billed for that still”",
          "an index hit: the plan row reads <i>reusing a stored still, free</i> and no step is submitted",
          "a cache that reports a hit and submits anyway"]],
        [19, 36, 45]))
    c.note(768, 928, 992,
           "<b>The pattern:</b> every measured value is stored next to the sentence that says how it was measured, "
           "and every human edit moves the value's provenance to <span class='m'>manual</span> while keeping the "
           "old method as history. A number without its method is refused at the door — that is refusal #2 — "
           "and a number whose method survived an edit that invalidated it would be the same defect, arriving later.",
           MUTED, 11.5)

    return page(4, TOTAL, "residual actions",
                "Everything a user leaves behind, and what it costs to take back",
                "The product keeps no event log. Every claim it makes is a field on a document beside the "
                "sentence explaining where the field came from — so this page is the audit trail.",
                c,
                "Nothing here is derived from a database schema: there is no database. These are YAML documents in one bucket, and the key layout is the index.")


# ==============================================================================
# 5 — the pipeline in depth
# ==============================================================================
def page_pipeline():
    c = Canvas()

    c.band(24, 70, 340, 720, "inputs", MUTED)
    c.band(390, 70, 470, 720, "resolution — one function, two callers", SYNC)
    c.band(886, 70, 460, 720, "execution", EXT)
    c.band(1372, 70, 408, 720, "output, and the gate", PROV)

    # inputs
    c.box("i_recipe", 42, 96, 304, 132, "<b>Recipe</b> — a format is a row", kind="plain",
          sub="four ship; copies are editable, built-ins refuse deletion",
          items=("prompt template · negatives · aspect · length policy",
                 "face policy · which reference class the framing wants · variant spread",
                 "<b>default is <span class='m'>performance</span></b>, not the backdrop — "
                 "the backdrop deliberately has nobody in it"))
    c.box("i_song", 42, 236, 304, 116, "<b>Song</b>", kind="plain",
          sub="measured once, reused forever",
          items=("the chosen sections' windows — each loop is the length of <i>its own</i> window",
                 "videos are dealt <b>round-robin</b> across hooks, not multiplied by them"))
    c.box("i_id", 42, 360, 304, 152, "<b>Identity lines</b>", kind="plain",
          sub="a band is one artist with four faces",
          items=("<span class='m'>compile()</span> → ≤ 220 chars, ordered by salience, clauses dropped <b>whole</b>",
                 "<span class='m'>plates()</span> → duplicates dropped by hash, one frame per lighting setup",
                 "each face composes under its own name — two unlabelled silhouettes read as one contradictory person"))
    c.box("i_edit", 42, 520, 304, 130, "<b>What the operator typed</b>", kind="surface",
          sub="stored as <span class='m'>brief[\"shot_overrides\"]</span>",
          items=("per shot: prompt · model · seconds · whether the face is locked at all",
                 "kept as a list by index — JSON has no integer keys, and <span class='m'>{0:…}</span> "
                 "comes back as <span class='m'>{\"0\":…}</span> matching nothing"))
    c.box("i_budget", 42, 658, 304, 130, "<b>The ceilings</b>", kind="gate",
          sub="checked before anything is submitted",
          items=("<span class='m'>RK_MAX_RUN_CENTS</span> · the kit's own budget · "
                 "<span class='m'>RK_MAX_SHOTS_PER_KIT</span>",
                 "Genblaze documents no spend guardrail, and a failed step is observably billed — "
                 "so a submitted step is a charged step"))

    # resolution
    c.box("r_plan", 408, 110, 434, 92, "<b>briefs.plan_from_recipe()</b> → ShotSpec[]", kind="service",
          sub="then <span class='m'>apply_overrides()</span>",
          items=("one planner for the preview and for the run — not two implementations kept in step by hand",))
    c.box("r_resolve", 408, 222, 434, 172, "<b>GenblazeGenerator._resolve()</b>", kind="service",
          sub="the single resolution path",
          items=("<b>provider</b> — pins first, then keyed availability: GMI → OpenAI → Google (video/image), "
                 "ElevenLabs → OpenAI (audio). A modality nobody serves is skipped with a warning",
                 "<b>model</b> — follows the provider, from <span class='m'>DEFAULT_MODELS</span>. "
                 "Defaulting to GMI slugs was a guaranteed 404 the moment anything else answered",
                 "<b>duration</b> — snapped, so the quote prices the clip that will actually render",
                 "<b>price</b> — one dated book, registered into Genblaze's own ModelRegistry"))
    c.box("r_ref", 408, 414, 434, 158, "<b>Will the reference actually be read?</b>", kind="gate",
          sub="two checks, at two levels",
          items=("<span class='m'>accepts_chain_input</span> — the <i>provider</i> takes an image at all. "
                 "Genblaze raises before any step runs, so an ungated plate fails the whole kit",
                 "<span class='m'>honours_reference()</span> — the <i>model</i> reads it. "
                 "<b>An unknown model answers no</b>: wrongly assuming image-capable renders a plausible "
                 "stranger and bills for it",
                 "A shot that wants the face <b>swaps the model</b> to <span class='m'>RK_IMAGE_EDIT_MODEL</span> "
                 "— it does not drop the reference. There is no default, on purpose"))
    c.box("r_fam", 408, 592, 434, 92, "<b>adapters/model_families.py</b>", kind="plain",
          sub="payload shapes the connector does not ship",
          items=("every entry is a rejection that named its own missing parameter — "
                 "<span class='m'>bria-fibo-edit → 400: images (Required parameter is missing)</span>",))
    c.box("r_out", 408, 700, 434, 62, "<b>plan()</b> displays them · <b>generate()</b> submits them",
          kind="prov", sub="one function, two callers — which is the whole cost-control argument")

    # execution
    c.box("x_pipe", 904, 110, 424, 86, "<b>Genblaze Pipeline</b>", kind="ext",
          sub="one Run · steps fan out to <span class='m'>RK_MAX_CONCURRENCY</span> · timeout 900 s",
          items=("the compiled identity is prepended to every prompt; negatives ride as "
                 "<span class='m'>negative_prompt</span>",))
    c.box("x_still", 904, 216, 424, 130, "<b>Stage one — the still</b>", kind="ext",
          sub="conditioned on the artist's reference plates",
          items=("roughly a fifteenth of the clip's cost",
                 "the expensive, uncertain part happens once, cheaply, where a person can look at it",
                 "a video shot takes exactly <b>one</b> plate — a second lands in "
                 "<span class='m'>last_frame</span> and reads as “end the clip here”"))
    c.box("x_index", 904, 366, 424, 128, "<b>The still index</b>", kind="prov",
          sub="<span class='m'>still_digest(identities, prompt)</span> on the identity line",
          items=("a hit skips the image call entirely — nothing submitted, nothing billed",
                 "it lives on the <i>line</i>, not the kit, because its whole value is outliving the kit that paid for it",
                 "identity <b>versions</b> are in the digest, so a new face misses rather than reusing an old one"))
    c.box("x_clip", 904, 508, 424, 122, "<b>Stage two — the clip</b>", kind="ext",
          sub="generated from that still via <span class='m'>input_from</span>",
          items=("a locked clip carries <b>no</b> plates of its own — positional slots would put two different "
                 "images in first_frame and last_frame and ask the model to interpolate",
                 "an index hit conditions through <span class='m'>external_inputs</span> instead"))
    c.box("x_degrade", 904, 640, 424, 150, "<b>It degrades, it does not fail</b>", kind="gate",
          sub="and the plan row says which applies",
          items=("no image provider · no reference frames · a provider that refuses images → "
                 "a plain plate-conditioned clip, with the reason on the row",
                 "local storage with a live backend → refused, because the provider fetches the image "
                 "from its own network and <span class='m'>/files/…</span> means nothing off-host",
                 "a duo today sends one plate and <i>says so</i> — silent conditioning on one member "
                 "is the failure that must not be invisible"))

    # output
    c.box("o_sink", 1390, 110, 372, 100, "<b>ObjectStorageSink</b> · HIERARCHICAL", kind="store",
          sub="<span class='m'>runs/{tenant}/{date}/{run_id}/…</span>",
          items=("the tenant is a path component, so the bucket, the documents and the manifests "
                 "share one tenant axis",))
    c.box("o_man", 1390, 230, 372, 110, "<b>The manifest</b>", kind="prov",
          sub="every step: provider · model · prompt · params · cost",
          items=("<span class='m'>manifest.verify()</span> is the gate, not a decoration",
                 "assets <b>and</b> a verified manifest, or the kit is <span class='m'>failed</span>"))
    c.box("o_ledger", 1390, 360, 372, 124, "<b>The ledger</b>", kind="store",
          sub="<span class='m'>cost_cents: int | None</span>",
          items=("unknown pricing estimates at <span class='m'>UNKNOWN_CALL_USD</span>, never at zero — "
                 "coercing <span class='m'>None</span> to 0 is how a multi-dollar run reported $0.00",
                 "<span class='m'>cost_is_complete</span> shows a total as a floor rather than as the invoice",
                 "both stages are priced: one shot bought, two calls charged"))
    c.box("o_kit", 1390, 504, 372, 118, "<b>The kit document, rewritten</b>", kind="store",
          sub="in place, same id",
          items=("run_id · assets[] · manifest_key · manifest_verified · total_cost_cents · error",
                 "<span class='m'>queued → running → ready | failed</span>",
                 "a redelivered message finds it <span class='m'>ready</span> and does nothing"))
    c.box("o_still", 1390, 646, 372, 128, "<b>And back onto the identity</b>", kind="prov",
          sub="<span class='m'>LockedStill</span> appended to the line",
          items=("digest · key · prompt · identity_ids · approved",
                 "this is the one write in the whole run that lands outside the kit — and it is why the "
                 "<i>second</i> kit for an artist is cheap"))

    c.edge("i_recipe:right", "r_plan:left@0.3", color=SYNC, bend=0.6)
    c.edge("i_song:right", "r_plan:left@0.6", color=SYNC, bend=0.5)
    c.edge("i_id:right", "r_resolve:left@0.25", color=SYNC, bend=0.4)
    c.edge("i_edit:right", "r_resolve:left@0.7", color=SYNC, bend=0.35)
    c.edge("i_budget:right", "r_ref:left@0.8", color=GATE, bend=0.3)
    c.edge("r_plan:bottom", "r_resolve:top", color=SYNC, width=1.8)
    c.edge("r_resolve:bottom", "r_ref:top", color=SYNC, width=1.8)
    c.edge("r_ref:bottom", "r_fam:top", color=SYNC, width=1.8)
    c.edge("r_fam:bottom", "r_out:top", color=SYNC, width=1.8)
    c.edge("r_resolve:right", "x_pipe:left", color=EXT, bend=0.5, width=2.2, label="submit")
    c.edge("x_pipe:bottom", "x_still:top", color=EXT, width=2)
    c.edge("x_still:bottom", "x_index:top", color=PROV, width=2, label="store")
    c.edge("x_index:bottom", "x_clip:top", color=PROV, width=2, label="or reuse, free")
    c.edge("x_clip:bottom", "x_degrade:top", color=RULE, width=1.5, arrow=False)
    c.edge("x_clip:right", "o_sink:left@0.7", color=HOT, bend=0.5, width=2.2)
    c.edge("x_still:right", "o_sink:left@0.3", color=HOT, bend=0.4, width=1.8)
    c.edge("o_sink:bottom", "o_man:top", color=PROV, width=2)
    c.edge("o_man:bottom", "o_ledger:top", color=PROV, width=2)
    c.edge("o_ledger:bottom", "o_kit:top", color=HOT, width=2)
    c.edge("o_kit:bottom", "o_still:top", color=PROV, width=2)
    c.poly([(1576, 774), (1576, 806), (1116, 806), (1116, 790)], color=PROV, width=1.8,
           label="the index the next kit hits", label_at=(1350, 806))

    # bottom strip
    c.band(24, 828, 1756, 230, "what actually answers, and what it costs", EXT)
    c.html(44, 862, 830, table(
        ["modality", "deployed today", "order when unpinned", "why"],
        [["Video", "<b>Sora</b> <span class='cm'>sora-2</span> — <b>pinned</b>",
          "GMI Cloud → OpenAI → Google",
          "GMI's <span class='m'>seedance-2</span> failed twice after ~174 s and billed for output it never "
          "produced. The fix had to be a deploy-time setting: a provider misbehaving in production is an "
          "operational event, not a code change"],
         ["Image", "<b>GMI Cloud</b> <span class='cm'>seedream-5.0-lite</span>",
          "GMI Cloud → OpenAI → Google",
          "cheap, and it works. But it is <i>text-to-image</i> — so any shot that wants the face swaps to "
          "<span class='m'>RK_IMAGE_EDIT_MODEL</span> rather than sending a reference nothing consumes"],
         ["Audio", "<b>ElevenLabs</b> <span class='cm'>eleven_multilingual_v2</span>",
          "ElevenLabs → OpenAI",
          "the same key Scribe reads for transcription — a keyed deployment gains lyrics without gaining a vendor"]],
        [8, 22, 22, 48]))
    c.html(908, 862, 872, table(
        ["a refusal you will meet", "what it means"],
        [["<span class='m'>seededit-3-0-i2i-250628</span> estimates at $2.00/call",
          "it is unpriced, so it takes the unknown-model rate. Three locked clips quote at 783¢ and are refused "
          "by the 500¢ ceiling — reconcile the real rate against an invoice and register it. Until then, "
          "<b>the refusal is the system working</b>"],
         ["“no likeness held — set <span class='m'>RK_VIDEO_EDIT_MODEL</span>”",
          "both models between a photograph and a clip can silently discard the reference, and the connector "
          "declares <span class='m'>first_frame</span> for every <span class='m'>seedance-*</span> slug — true of "
          "1.x, not of 2.0. Nothing is guessed: a hardcoded slug 404'd once already"],
         ["“2 faces — 1 reference sent”",
          "Genblaze routes any number of images, but GMI ships no per-model payload schema and extras are dropped. "
          "One reference is the only count this code can honestly claim, so it is the default"],
         ["a silent soundtrack that is not silent",
          "<span class='m'>negative_prompt</span> is a conditioning signal; no provider here documents it as binding "
          "on audio. The deterministic fix is dropping the track on delivery, and it is specified, not built"]],
        [30, 70]))

    return page(5, TOTAL, "the expensive path",
                "One resolution function, and the two-stage lock around a face",
                "Everything between a brief and a billed provider call. The load-bearing property is that "
                "the dry run and the real run are the same function — so the payload on screen is the payload "
                "on the wire.",
                c,
                "Prices, provider order and payload shapes are facts about vendors, not about this code: each one here arrived as a rejection or an invoice, and is registered rather than inferred.")


# ==============================================================================
# 6 — deployment
# ==============================================================================
def page_deploy():
    c = Canvas()

    c.band(24, 74, 1340, 470, "production — AWS runs the logic, B2 holds the bytes", INK)
    c.box("dns", 44, 190, 150, 92, "the operator", kind="actor", sub="one browser",
          items=("HTTPS", "httpOnly cookie"))
    c.box("http", 236, 190, 176, 92, "HTTP API", kind="surface",
          sub="the reachable front door",
          items=("alongside the Function URL",))
    c.box("lam", 454, 166, 216, 140, "<b>Lambda</b> — web", kind="service",
          sub="FastAPI via Lambda Web Adapter",
          items=("JSON and HTML, nothing slow",
                 "no provisioned concurrency, so no compute floor",
                 "secrets read from SSM <i>before</i> settings are cached"))
    c.box("sqs", 712, 190, 176, 92, "<b>SQS</b>", kind="queue",
          sub="one job queue + DLQ",
          items=("pay-per-request", "1M/mo free"))
    c.box("batch", 930, 158, 194, 156, "<b>AWS Batch</b><br>on Fargate Spot", kind="worker",
          sub="one container: Genblaze <i>and</i> ffmpeg",
          items=("<span class='m'>minvCpus: 0</span> — nothing at rest",
                 "no 15-minute ceiling, which is the whole reason it is not Lambda",
                 "providers baked into the image, not installed at deploy"))
    c.box("ssm", 236, 366, 176, 96, "<b>SSM</b> Parameter Store", kind="ext",
          sub="standard tier is free",
          items=("Terraform seeds a literal <span class='m'>PLACEHOLDER …</span>, and never holds the value — "
                 "it would land in state in plaintext",))
    c.box("ecr", 454, 366, 216, 96, "<b>ECR</b> + <b>CloudWatch</b>", kind="ghost",
          sub="along for the ride",
          items=("two images, web and worker · logs at 14-day retention · cents",))
    c.box("b2", 712, 342, 412, 130, "<b>Backblaze B2</b>", kind="store",
          sub="everything durable — $6/TB-mo",
          items=("documents · masters · reference frames · run output · manifests",
                 "presigned PUT and GET straight to the browser, so bytes never traverse compute",
                 "<b>there is no database tier</b>, and that is the whole simplification"))
    c.box("vendors", 1160, 158, 186, 156, "the vendors", kind="ext",
          sub="called only from the worker",
          items=("GMI Cloud — image", "OpenAI / Sora — video", "ElevenLabs — audio + Scribe",
                 "ZeptoMail — from the web tier"))

    c.edge("dns:right", "http:left", color=SYNC, width=2.2)
    c.edge("http:right", "lam:left", color=SYNC, width=2.2)
    c.edge("lam:right", "sqs:left", color=ASYNC, width=2.2, label="enqueue", label_dy=-13)
    c.edge("sqs:right", "batch:left", color=ASYNC, width=2.2)
    c.edge("batch:right", "vendors:left", color=EXT, width=2, dashed=True, label="billed",
           label_dy=-13)
    c.edge("ssm:top", "lam:bottom@0.25", color=MUTED, width=1.6, dashed=True, label="at boot", bend=0.5)
    c.edge("ssm:right", "ecr:left", color=RULE, width=1.2, arrow=False)
    c.edge("lam:bottom@0.75", "b2:top@0.1", color=HOT, width=2.2, bend=0.5, label="documents")
    c.edge("batch:bottom", "b2:top@0.8", color=HOT, width=2.4, bend=0.5, label="run output")
    c.poly([(119, 190), (119, 108), (1010, 108), (1010, 342)], color=DIRECT, dashed=True,
           width=2.4, label="presigned PUT / GET — the master and every asset move browser ↔ bucket, "
                           "never through Lambda",
           label_at=(560, 108))

    c.note(44, 476, 1300,
           "<b>Realistic idle cost: under $1/month</b> — B2 storage and a container image. There is no compute "
           "floor anywhere, and the reason is that the relational schema this product would otherwise need exists "
           "almost entirely to serve <i>fans</i>: attribution joins, leaderboards, click ledgers, reward rows. "
           "Those are deferred, and the pressure for a database tier goes with them. What the label actually stores "
           "is documents, and a bucket is excellent at documents.", MUTED, 12)

    c.band(1396, 74, 384, 470, "the laptop, with no credentials", DIRECT)
    c.html(1414, 112, 348, table(
        ["axis", "dev", "production"],
        [["<span class='m'>RK_STORAGE_BACKEND</span>", "local", "b2"],
         ["<span class='m'>RK_GENERATOR_BACKEND</span>", "mock", "genblaze"],
         ["<span class='m'>RK_QUEUE_BACKEND</span>", "inline", "sqs"],
         ["<span class='m'>RK_MAIL_BACKEND</span>", "console", "zeptomail"],
         ["<span class='m'>RK_AUTH_BACKEND</span>", "none", "otp"],
         ["<span class='m'>RK_ANALYSIS_BACKEND</span>", "<i>refuses</i>", "auto (numpy)"],
         ["<span class='m'>RK_TRANSCRIPTION_BACKEND</span>", "<i>refuses</i>", "auto (Scribe)"]],
        [46, 22, 32]))
    c.note(1414, 322, 348,
           "<b>Not one line of application code differs</b> between the two columns — services take ports, and "
           "only <span class='m'>deps.py</span> names an adapter.", MUTED, 11.5)
    c.note(1414, 380, 348,
           "<b>Two axes have no mock, deliberately.</b> A mocked <i>measurement</i> is a float nothing downstream "
           "could tell from a real one; a mocked <i>lyric</i> is words attributed to an artist. Both refuse and "
           "name what is missing — on the song page and in <span class='m'>/healthz</span>.", MUTED, 11.5)
    c.note(44, 552, 1300,
           "<b>What “mock” does not mean.</b> Real: the Pipeline, the sink, the key layout, content hashing, the "
           "manifest, <span class='m'>manifest.verify()</span>, the cost ledger and embed-on-delivery. Synthetic: "
           "the pixels, which ffmpeg draws, and the unit costs, which come from a table. So the manifest verified "
           "on a laptop is produced by the same code that produces the one in B2 — which is the only reason a "
           "zero-credential demo is worth having.", MUTED, 11.5)

    # -- boot and config ---------------------------------------------------
    c.band(24, 606, 1756, 442, "boot, configuration, and the two guards that refuse to start", GATE)
    c.box("b_env", 44, 646, 250, 108, "<b>app/.env</b> or the task environment", kind="plain",
          sub="pydantic-settings, <span class='m'>RK_</span>-prefixed",
          items=("prefixed so they never collide with the provider keys Genblaze reads directly",))
    c.box("b_ssm", 336, 646, 250, 108, "<b>bootstrap.load_secrets()</b>", kind="service",
          sub="reads <span class='m'>RK_SSM_PATH</span>",
          items=("two reads of <span class='m'>get_settings()</span>, deliberately: the <i>location</i> of the "
                 "secrets is itself a setting, and the cache must not remember the empty environment",))
    c.box("b_export", 628, 646, 250, 108, "<b>export_for_libs()</b>", kind="service",
          sub="into the process environment",
          items=("Genblaze's connectors read vendor keys from the environment, not from our settings object",))
    c.box("b_cont", 920, 646, 250, 108, "<b>deps.get_container()</b>", kind="gate",
          sub="one instance of each adapter, per process",
          items=("<span class='m'>describe()</span> is what <span class='m'>/healthz</span> returns and what the "
                 "banner on every page says",))
    c.box("b_warn", 1212, 646, 250, 108, "<b>startup warnings</b>", kind="prov",
          sub="logged, and shown",
          items=("a keyed-looking provider whose key is the Terraform placeholder is reported as <b>unset</b>, "
                 "not as live",))
    c.box("b_guard", 1504, 646, 256, 108, "<b>two guards</b>", kind="gate",
          sub="refuse to start, rather than warn",
          items=("<span class='m'>RK_REQUIRE_AUTH</span> with <span class='m'>AUTH_BACKEND=none</span>",
                 "<span class='m'>RK_REQUIRE_AUTH</span> with an empty session secret — which would otherwise "
                 "sign sessions with a key that changes every restart"))
    for a, b in (("b_env", "b_ssm"), ("b_ssm", "b_export"), ("b_export", "b_cont"),
                 ("b_cont", "b_warn"), ("b_warn", "b_guard")):
        c.edge(f"{a}:right", f"{b}:left", color=INK, width=1.8)

    c.html(44, 794, 850, table(
        ["turning authentication on", "why it is shaped this way"],
        [["<span class='m'>RK_AUTH_BACKEND=otp</span>", "the default <span class='m'>none</span> is a complete "
          "implementation that admits everyone, so a fresh checkout has no login to get past"],
         ["<span class='m'>RK_ALLOWED_EMAILS=…</span>", "<b>this is the user table.</b> No registration. Checked on "
          "request, on verify, and on every session read"],
         ["<span class='m'>RK_SESSION_SECRET=…</span>", "sessions are a signed token, not a row — so there is no "
          "revocation before expiry, and rotating this is the panic button"],
         ["<span class='m'>RK_MAIL_BACKEND=zeptomail</span>", "with no mailer the code goes to the log, and back to "
          "the caller <i>only</i> in <span class='m'>RK_ENV=dev</span> — either condition alone would be a bypass"]],
        [26, 74]))
    c.html(908, 794, 872, table(
        ["what adding auth cost", "because"],
        [["one provider, one service, one page, one exception handler",
          "every request already resolved a <span class='m'>Principal</span>, and every document, object key and "
          "job payload already carried its <span class='m'>tenant_id</span>"],
         ["no service changed",
          "services take ports; routes take services; only <span class='m'>deps.py</span> names an adapter"],
         ["no template and no storage key changed shape",
          "the tenant axis was already in the path — “componentized, and we add auth later” became a configuration "
          "change rather than a refactor"],
         ["no route grew an <span class='m'>if authenticated</span> branch",
          "one <span class='m'>AuthError</span> handler decides what a failure looks like: a browser is redirected "
          "with <span class='m'>?next=</span> preserved, an htmx request gets <span class='m'>HX-Redirect</span>, "
          "a script gets 401 JSON"]],
        [34, 66]))

    return page(6, TOTAL, "deployment",
                "Five services, one durable store, and no database tier",
                "The same code runs on a laptop with zero credentials and on Lambda + SQS + Batch against B2 and "
                "three vendors. Seven environment variables are the entire difference.",
                c,
                "Terraform holds the shape but never the secrets — each parameter is seeded with a literal placeholder, and the provider table treats that placeholder as unset.")


# ==============================================================================
# 7 — the trust argument
# ==============================================================================
def page_trust():
    c = Canvas()

    c.band(24, 74, 940, 560, "the provenance loop — tested as a loop, not as three assertions", PROV)
    c.box("t_gen", 60, 130, 250, 104, "<b>1 · Generate</b>", kind="ext",
          sub="assets + manifest land in the bucket",
          items=("the stored object is byte-identical to what the provider returned",))
    c.box("t_ver", 60, 268, 250, 104, "<b>2 · Verify, before ready</b>", kind="gate",
          sub="<span class='m'>manifest.verify()</span> gates the kit",
          items=("an unverified manifest fails the kit rather than shipping a claim that cannot be backed",))
    c.box("t_del", 60, 406, 250, 118, "<b>3 · Embed on delivery</b>", kind="prov",
          sub="the manifest is written into the media",
          items=("the first moment the thing being embedded is complete <i>and</i> verified",
                 "<span class='m'>X-RemixKit-Provenance</span> says which happened"))
    c.box("t_out", 400, 268, 250, 104, "<b>4 · The file leaves</b>", kind="actor",
          sub="carrying its own record",
          items=("which model, from which prompt, for which release",))
    c.box("t_chk", 740, 268, 202, 104, "<b>5 · <span class='m'>/verify</span></b>", kind="prov",
          sub="public, no session",
          items=("<span class='m'>verified: true</span>",
                 "<span class='m'>source: \"embedded\"</span>"))
    c.edge("t_gen:bottom", "t_ver:top", color=PROV, width=2.2)
    c.edge("t_ver:bottom", "t_del:top", color=PROV, width=2.2)
    c.edge("t_del:right", "t_out:left", color=PROV, width=2.2, bend=0.28)
    c.edge("t_out:right", "t_chk:left", color=PROV, width=2.2)
    c.poly([(890, 268), (890, 108), (185, 108), (185, 130)], color=PROV, width=2.0,
           dashed=True, label="nothing is looked up — strip the manifest and the check fails, which is the point",
           label_at=(537, 108))
    c.note(400, 400, 542,
           "<b>Why embedding happens at delivery rather than at generation.</b> The manifest describes the whole "
           "run, and the run is not finished while its steps are still producing — so delivery is the first point "
           "where the thing being embedded is complete. And the stored object has to stay byte-identical to the "
           "provider's output, because that is what makes the content hash in the manifest mean anything.<br><br>"
           "<b>An asset whose media type has no embedding handler is delivered unmodified</b> — an MP3 without "
           "<span class='m'>mutagen</span>, say — and the caller is told which happened. Silently handing back an "
           "undisclosed file is the one failure mode this service exists to prevent.", MUTED, 12)

    c.band(24, 690, 940, 358, "what is deliberately not here", MUTED)
    c.html(44, 726, 900, table(
        ["absent", "why"],
        [["attribution links · <span class='m'>/r/{code}</span> · the disclosure gate · leaderboards · rewards · "
          "composite sessions · K-factor",
          "all of it serves the <i>fan</i> role, which this scope defers. They are also what would force a "
          "relational database — their absence is why there is no database tier"],
         ["a websocket", "the kit page polls one row every three seconds; a viral release exercises storage reads, "
          "not sockets"],
         ["a CDN", "there is no read path hot enough to justify one at this scope"],
         ["a “which archetype goes viral” recommender", "research calls that folklore. The song page ranks hooks "
          "against stated rules and reports <b>not checkable</b> when it cannot — there is no weighted total "
          "anywhere, because a single score would read as a quality judgement the material cannot support"],
         ["an event log or audit trail", "provenance is a field on the document beside the value it describes. "
          "That is a deliberate trade: it answers “how was this measured” perfectly and “who changed it last "
          "Tuesday” not at all"]],
        [30, 70]))

    c.band(1000, 74, 780, 974, "every refusal in the product, and what it is protecting", GATE)
    c.html(1020, 112, 740, table(
        ["#", "the refusal", "protecting"],
        [["01", "No recorded likeness consent → <b>no kit</b>",
          "a kit holds the artist's face invariant across every shot, and that needs auditable rights. "
          "Withdrawal takes effect on the next kit, not retroactively"],
         ["02", "A BPM with no method → <b>refused</b>",
          "a field that silently accepts an unsourced <span class='m'>128</span> hides catalogue onboarding, "
          "which is the actual bottleneck"],
         ["03", "Estimate over <span class='m'>RK_MAX_RUN_CENTS</span> or the kit budget → <b>refused before "
          "submission</b>",
          "Genblaze documents no spend guardrail and a failed step is observably billed, so the only safe "
          "assumption is that a submitted step is a charged step"],
         ["04", "A queue that cannot execute → <b>refused before a row is written</b>",
          "otherwise a kit sits at <span class='m'>queued</span> forever with nothing coming for it"],
         ["05", "Assets without a verified manifest → <b>the kit fails</b>",
          "the disclosure argument <i>is</i> the product; shipping an unbacked claim is worse than shipping nothing"],
         ["06", "No numpy → <b>the analyser refuses and names what is missing</b>",
          "a mocked measurement is a float nothing downstream could tell from a real one"],
         ["07", "No credentialed transcriber → <b>refused</b>",
          "a fabricated lyric is words attributed to an artist, offered to the generate form and burned into a clip"],
         ["08", "Re-transcribing over hand-corrected lines → <b>refused unless forced</b>",
          "a lyric is one continuous reading; interleaving old corrections with new lines produces a transcript "
          "that is neither"],
         ["09", "Deleting an artist with dependents → <b>refused, with the counts named</b>",
          "silent orphaning is the worst of the three options — a roster that looks clean while the catalogue "
          "counts songs nobody can open"],
         ["10", "A live generator on local storage → <b>the plate is refused</b>",
          "the provider fetches the image from its own network, and <span class='m'>/files/…</span> means nothing "
          "off-host. Sending a dead link would look like it worked"],
         ["11", "An unknown image model → <b>treated as not reading references</b>",
          "the safe direction: a model wrongly treated as image-capable renders a plausible stranger and bills "
          "full price for it"],
         ["12", "<span class='m'>RK_IMAGE_EDIT_MODEL</span> unset → <b>the plan says no likeness will be held</b>",
          "there is no default because a hardcoded slug 404'd once already, and GMI ships no catalog endpoint to "
          "check one against"],
         ["13", "A master key that is not this song's → <b>refused</b>",
          "it arrives from a browser; a caller who could name any key could point a song at another tenant's object"],
         ["14", "An email outside the allowlist → <b>told so</b>",
          "this leaks membership, deliberately: with a handful of known colleagues the real failure is somebody "
          "mistyping their own address"],
         ["15", "<span class='m'>RK_REQUIRE_AUTH</span> without a backend or a secret → <b>the process refuses "
          "to start</b>",
          "a warning here would mean a deployment that looks authenticated and is not"]],
        [4, 34, 62]))

    return page(7, TOTAL, "the trust argument",
                "Fifteen refusals, and one loop that closes",
                "The claim is that disclosure travels inside the file. Everything on this page exists so that the "
                "claim is either true or loudly absent — never quietly assumed.",
                c,
                "Each refusal is in the service layer with a test, not in a form validator — these are product rules, and a rule enforced only by a screen is a rule the API does not have.")
