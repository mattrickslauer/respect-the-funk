#!/usr/bin/env python3
"""Resolve an rtf.edit/v2 instance against its format and its song.

An instance says *which joke*. A format says *what shape a video of this kind takes*.
A song says *where the beats and the drop actually are*. Three files, three rates of
change: the format almost never moves, a song is measured once, an instance is written
per video.

    edit, errs = rtf.resolve(Path("edits/california-test.edit.yaml"))

`resolve` returns the flat dict the renderers already consume — the same shape a hand-
written rtf.edit/v1 had — so the format layer is a compiler, not a new runtime. A v1 edit
with no `format:` key passes straight through unchanged.

`errs` carries the FORMAT-SPEC invariants: the ones you can only check once you know
which format an edit claims to be. Structural errors, not taste. See FORMAT-SPEC.md §Rules.
"""

from pathlib import Path

import yaml

SCHEMA = "rtf.edit/v2"


def load(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def find_root(start: Path) -> Path:
    """Walk up to the content root — the directory holding lib/ and videos/.

    Not a fixed number of `.parent` hops. Those were what broke every time the tree moved,
    and the tree will move again.
    """
    for d in [start, *start.parents]:
        if (d / "lib").is_dir() and (d / "videos").is_dir():
            return d
    raise SystemExit(f"no content root above {start} — expected a dir with lib/ and videos/")


def find_edit(arg: str) -> Path:
    """Accept a video id, a video directory, or a path to an edit file.

        render_edit.py california-test
        render_edit.py videos/california-test
        render_edit.py videos/california-test/edit.yaml
    """
    p = Path(arg)
    if p.is_file():
        return p.resolve()
    if p.is_dir() and (p / "edit.yaml").is_file():
        return (p / "edit.yaml").resolve()
    for base in [Path.cwd(), *Path.cwd().parents]:
        cand = base / "videos" / arg / "edit.yaml"
        if cand.is_file():
            return cand.resolve()
    raise SystemExit(f"no edit found for {arg!r} — pass a video id, a video dir, or an edit.yaml")


def characters(root: Path, video: Path, cast: list) -> dict:
    """Resolve a cast to character files. Video-local definitions beat shared ones.

    The cast is DECLARED, never globbed. Globbing a shared characters/ directory silently
    drags every character in the project into every prompt — fine while there was one
    cast, wrong the moment there are two.
    """
    out = {}
    for cid in cast or []:
        for d in (video / "characters", root / "lib" / "characters"):
            f = d / f"{cid}.character.yaml"
            if f.is_file():
                c = load(f)
                c["_dir"] = str(d)
                out[cid] = c
                break
        else:
            raise SystemExit(f"cast member {cid!r}: no {cid}.character.yaml in "
                             f"{video/'characters'} or {root/'lib'/'characters'}")
    return out


def merge(base: dict, over: dict) -> dict:
    """Dicts merge key-wise; anything else the override replaces outright.

    Deliberately shallow-ish: a list in an instance REPLACES the format's list rather
    than appending to it. An `animate` cycle that half-inherits is not a thing anyone
    can reason about.
    """
    out = dict(base or {})
    for k, v in (over or {}).items():
        if v is None:
            continue
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ---------------------------------------------------------------- cadence


def cadence_grid(cad: dict, beat_ms: float, cut_ms: int) -> list[dict]:
    """Turn a cadence's beats-before-the-drop into absolute milliseconds.

    The hook runs on the same grid as the carousel, counted backward from the cut instead
    of forward from it. Positions are absolute on that grid and durations are their
    differences, so rounding never accumulates — identical to how beat_grid() handles the
    carousel, and for the same reason.
    """
    out = []
    slots = sorted(cad.get("slots", []), key=lambda s: -float(s["at"]))
    for s, nxt in zip(slots, slots[1:] + [None]):
        t_in = cut_ms - round(float(s["at"]) * beat_ms)
        t_out = cut_ms - round(float(nxt["at"]) * beat_ms) if nxt else cut_ms
        if t_out > t_in:
            out.append({"role": s["role"], "t_in": t_in, "t_out": t_out,
                        "at": float(s["at"]), "what": s.get("what", "")})
    return out


def cadence_drift(desc: dict, grid: list[dict], beat_ms: float) -> list[tuple]:
    """How far each of a clip's own beat boundaries sits from the nearest grid position.

    Reported, never enforced. A filmed clip cannot be re-cut to the grid after the fact —
    the performance is where it is — so this is a measurement you use when choosing which
    take to shoot against, not a gate that rejects one you already have.
    """
    marks = sorted({g["t_in"] for g in grid} | {grid[-1]["t_out"]}) if grid else []
    rows = []
    for b in desc.get("beats", []):
        t = b["t_in"]
        if not marks:
            continue
        near = min(marks, key=lambda m: abs(m - t))
        rows.append((t, near, t - near, b.get("shot", "")))
    return rows


# ---------------------------------------------------------------- resolution


def resolve(edit_path: Path) -> tuple[dict, list[str]]:
    """Instance + format + song → the flat edit the renderers consume."""
    edit_path = Path(edit_path).resolve()
    inst = load(edit_path)
    if "format" not in inst:
        return inst, []                       # legacy rtf.edit/v1, hand-written flat

    video = edit_path.parent                  # content/videos/<id>/
    root = find_root(video)                   # content/
    fmt_path = root / "lib" / "formats" / f"{inst['format']}.format.yaml"
    song_path = root / "lib" / "songs" / f"{inst['song']}.song.yaml"
    for p in (fmt_path, song_path):
        if not p.exists():
            return inst, [f"missing: {p}"]
    fmt, song = load(fmt_path), load(song_path)

    cad = None
    cad_id = inst.get("cadence") or fmt.get("defaults", {}).get("cadence")
    if cad_id:
        cad_path = root / "lib" / "cadences" / f"{cad_id}.cadence.yaml"
        if not cad_path.exists():
            return inst, [f"missing cadence: {cad_path}"]
        cad = load(cad_path)

    dflt = fmt.get("defaults", {})
    tuning = merge(dflt.get("tuning", {}), song.get("tuning", {}))
    tuning = merge(tuning, inst.get("tuning", {}))

    # ---- output ----------------------------------------------------------
    out = merge(dflt.get("output", {}), inst.get("output", {}))
    out.pop("dir", None)
    out.setdefault("file", str(video / f"{inst['id']}.mp4"))

    # ---- music: song facts + per-song tuning, never typed in the instance --
    hook = inst["hook"]
    meas = song.get("measured", {})
    music = {
        "track_id": song["id"],
        "title": song.get("title"),
        "artist": song.get("artist"),
        "file": str((song_path.parent / song["file"]).resolve()),
        "bpm": meas.get("bpm"),
        "drop_ms": meas.get("drop_ms"),
        "align_to": f"cut@{hook['t_out']}",
        "pre_drop_gain": tuning.get("pre_drop_gain", 0.0),
        "license": song.get("rights", {}).get("license"),
    }
    if tuning.get("pre_roll"):
        music["pre_roll"] = tuning["pre_roll"]

    # ---- timeline: the hook, then the carousel ---------------------------
    timeline = [{
        "clip": hook["clip"],
        "t_in": hook.get("t_in", 0),
        "t_out": hook["t_out"],
        "audio": hook.get("audio", "source"),
        "speech_lufs": hook.get("speech_lufs", tuning.get("speech_lufs")),
        "transition_out": "hard_cut",
    }]

    car = merge(dflt.get("carousel", {}), inst.get("carousel", {}))
    src_items = car.get("items", [])
    items = [{"still": it["still"], "prompt_ref": i + 1, "beats": it.get("beats", 1)}
             for i, it in enumerate(src_items)]
    # The last picture is the end card: it holds while the ear catches up. Only applied
    # when the instance didn't say otherwise, so an explicit `beats:` always wins.
    if items and "beats" not in src_items[-1]:
        items[-1]["beats"] = car.get("end_card_beats", 1)

    timeline.append({
        "sequence": inst["id"],
        "source": str(video / car.get("source", "stills")),
        "style": "title_sequence",
        "hold": "beats",
        "flash_frames": car.get("flash_frames", 0),
        "look": car.get("look", "none"),
        "animate": car.get("animate") or [],
        "items": items,
    })

    # ---- prompts: format doctrine + instance body ------------------------
    pr = merge(fmt.get("prompt", {}), inst.get("prompt", {}))
    still_prompts = {
        "shared_suffix": pr.get("shared_suffix", ""),
        "identity": pr.get("identity", ""),
        "characters_block": pr.get("characters_block", ""),
        "items": {i + 1: it["prompt"] for i, it in enumerate(src_items)},
    }

    resolved = {
        "schema": "rtf.edit/v1",
        "id": inst["id"],
        "title": inst.get("title"),
        "intent": inst.get("intent"),
        "output": out,
        "music": music,
        "beat_grid": {"origin": f"cut@{hook['t_out']}",
                      "quantize": fmt.get("beat_grid", {}).get("quantize", "strict")},
        "timeline": timeline,
        "still_prompts": still_prompts,
    }
    beat_ms = 60000.0 / float(meas["bpm"]) if meas.get("bpm") not in (None, "TODO") else None
    if cad and beat_ms:
        resolved["cadence"] = {
            "id": cad["id"], "title": cad.get("title"),
            "beat_ms": round(beat_ms, 2),
            "hook_beats": cad.get("hook_beats"),
            "grid": cadence_grid(cad, beat_ms, hook["t_out"]),
        }
    resolved["cast"] = list(inst.get("cast") or [])
    resolved["_video_dir"] = str(video)
    resolved["_root"] = str(root)
    return resolved, check(inst, fmt, song, tuning, car, video, cad, beat_ms)


# ---------------------------------------------------------------- invariants


def check(inst: dict, fmt: dict, song: dict, tuning: dict, car: dict, video: Path,
          cad: dict = None, beat_ms: float = None) -> list[str]:
    """The format's own rules. Everything here is mechanical — none of it is taste.

    Taste lives in the prompts and in whether the joke is funny. These are the things
    that make a video of this format *work as this format*, and they are the things a
    person retuning a new song gets wrong first.
    """
    e: list[str] = []
    slots = fmt.get("slots", {})
    hook, hspec = inst["hook"], slots.get("hook", {})

    # ---- the hook is one clip, one line, one legal exit -------------------
    hpath = video / f"{hook['clip']}.clip.yaml"
    if not hpath.exists():
        return [f"hook clip descriptor not found: {hpath}"]
    hd = load(hpath)

    if hspec.get("requires_can_lead", True) and not hd.get("role", {}).get("can_lead"):
        e.append(f"hook {hook['clip']}: role.can_lead is not true — it may not open a video")
    if hspec.get("requires_speech", True):
        au = hd.get("audio", {})
        if not au.get("has_speech") or not (au.get("quote") or "").strip():
            e.append(f"hook {hook['clip']}: format needs one spoken line — audio.quote is empty")

    kinds = {c["t"]: c.get("kind") for c in hd.get("cut_points", [])}
    want = hspec.get("must_end_on", "hard_cut")
    if kinds.get(hook["t_out"]) != want:
        e.append(f"hook {hook['clip']}: t_out {hook['t_out']} is kind {kinds.get(hook['t_out'])!r}, "
                 f"format requires a {want!r} cut_point — the drop has nowhere to land")

    dur = hook["t_out"] - hook.get("t_in", 0)
    floor = hd.get("role", {}).get("min_useful_ms")
    if floor and dur < floor:
        e.append(f"hook {hook['clip']}: {dur}ms is under its own min_useful_ms {floor} — "
                 f"the setup breaks before the drop arrives")
    if (cap := hspec.get("max_ms")) and dur > cap:
        e.append(f"hook: {dur}ms exceeds format max {cap}ms — a promise this long stops "
                 f"being a promise")

    # ---- the cadence ------------------------------------------------------
    # Length is BINDING for a generated hook and ADVISORY for a filmed one. A generated
    # clip can be made any length, so there is no excuse for it to sit off the grid. A
    # filmed clip is where the performance put it, and re-cutting it to the beat after
    # the fact would just clip the line — so its drift is reported, not rejected.
    if cad and beat_ms:
        want_ms = round(float(cad["hook_beats"]) * beat_ms)
        if hd.get("rights", {}).get("source") == "ai" and abs(dur - want_ms) > 20:
            e.append(f"hook is {dur}ms but cadence {cad['id']} is {cad['hook_beats']} beats "
                     f"= {want_ms}ms at {beat_ms:.0f}ms/beat — a generated hook has no "
                     f"reason to be off its own grid")

        req, au = cad.get("requires", {}), hd.get("audio", {})
        words = (au.get("quote") or "").split()
        if req.get("speech") and not words:
            e.append(f"cadence {cad['id']} has a `line` slot but the clip declares no "
                     f"audio.quote — there is nothing to say in it")
        if words and (mx := req.get("max_line_words")) and len(words) > mx:
            e.append(f"the line is {len(words)} words; cadence {cad['id']} gives it "
                     f"{mx} at most before it has to be rushed to fit the beats")
        if words and (mn := req.get("min_line_words")) and len(words) < mn:
            e.append(f"the line is {len(words)} words; cadence {cad['id']} wants at "
                     f"least {mn} to fill its slot")
        if req.get("single_take"):
            mid = [c for c in hd.get("cut_points", [])
                   if c.get("kind") not in ("in", "hard_cut")]
            if mid:
                e.append(f"cadence {cad['id']} is a single take but the clip declares "
                         f"{len(mid)} internal cut_point(s) — one of the two is a lie")

    # A generated hook must carry the prompt that made it. `rights.source: ai` is only a
    # legal value when the provenance exists (CLIP-SPEC rule 3), and for a hook the
    # provenance starts as the prompt — otherwise the clip is unreproducible the moment
    # the model version moves under it.
    if hd.get("rights", {}).get("source") == "ai":
        g = hook.get("generate") or {}
        # Either a hand-written prompt, or a style preamble that the cadence grid and the
        # descriptor's own beats get composed into. Both are provenance; neither may be absent.
        if not ((g.get("prompt") or "").strip() or (g.get("style") or "").strip()):
            e.append(f"hook {hook['clip']}: rights.source is 'ai' but neither "
                     f"hook.generate.prompt nor hook.generate.style is set — a generated "
                     f"clip with no recorded prompt has no provenance")
        elif hd.get("audio", {}).get("has_speech") and not g.get("dialogue"):
            e.append(f"hook {hook['clip']}: descriptor promises a spoken line but "
                     f"hook.generate.dialogue is unset — the model will invent one")

    # ---- the song was measured, not typed ---------------------------------
    meas = song.get("measured", {})
    for k in ("bpm", "drop_ms"):
        if meas.get(k) in (None, "TODO"):
            e.append(f"song {song['id']}: measured.{k} is unset — run measure_beat.py")
    if not meas.get("method"):
        e.append(f"song {song['id']}: measured.method is empty — a BPM with no provenance "
                 f"is a guess, and a guess drifts every cut after it")

    beat_ms = 60000.0 / float(meas["bpm"]) if meas.get("bpm") not in (None, "TODO") else None
    if beat_ms and (pre := tuning.get("pre_roll")):
        pre_ms = float(pre.get("bars", 2)) * 4 * beat_ms
        if pre_ms > dur:
            e.append(f"pre_roll {pre_ms:.0f}ms is longer than the {dur}ms hook — the swell "
                     f"would start before the video does")

    # ---- the carousel ------------------------------------------------------
    cspec = slots.get("carousel", {})
    src = car.get("items", [])
    if (lo := cspec.get("min_items")) and len(src) < lo:
        e.append(f"carousel has {len(src)} items, format minimum is {lo} — below that it "
                 f"reads as a slideshow, not a carousel")

    beats = sum(it.get("beats", 1) for it in src[:-1]) + (
        src[-1].get("beats", car.get("end_card_beats", 1)) if src else 0)
    if (want_beats := tuning.get("carousel_beats")) and beats != want_beats:
        e.append(f"carousel is {beats} beats, song tuning says {want_beats} — one of the two "
                 f"is wrong, and the renderer will believe the items")

    moves = car.get("animate") or []
    if moves:
        seq = [moves[i % len(moves)] for i in range(len(src))]
        for i, (a, b) in enumerate(zip(seq, seq[1:])):
            if a == b:
                e.append(f"carousel items {i + 1}/{i + 2} both move '{a}' — two identical "
                         f"moves back to back read as one long shot across the cut")

    # ---- escalation: does the payoff actually build? ----------------------
    # "Make it funnier" is not checkable. "The biggest picture is near the end, and the
    # end card is quieter than the peak" is. A carousel that opens at full volume has
    # nowhere to go, and 22 pictures at one level is a mood board however good each one is.
    esc = cspec.get("escalation") or {}
    if esc.get("require") and src:
        body, card = src[:-1], src[-1]
        levels = [it.get("intensity") for it in body]
        if any(v is None for v in levels):
            missing = [src[i]["still"] for i, v in enumerate(levels) if v is None]
            e.append(f"escalation is required but {len(missing)} item(s) have no "
                     f"`intensity`: {', '.join(missing[:5])}")
        elif len(levels) >= 6:
            peak = max(levels)
            peak_at = len(levels) - 1 - levels[::-1].index(peak)
            tail_from = int(len(levels) * (1 - esc.get("peak_within_last", 0.34)))
            if peak_at < tail_from:
                e.append(f"the biggest picture ({src[peak_at]['still']}, intensity {peak}) "
                         f"is at {peak_at + 1}/{len(levels)} — the peak has to land in the "
                         f"last {esc.get('peak_within_last', 0.34):.0%}, or the sequence "
                         f"deflates before the end card")
            if (cv := card.get("intensity")) is not None and cv >= peak:
                e.append(f"end card {card['still']} is intensity {cv} against a peak of "
                         f"{peak} — the end card is the release, not the climax")
            n3 = max(1, len(levels) // 3)
            lift = sum(levels[-n3:]) / n3 - sum(levels[:n3]) / n3
            if lift < esc.get("min_lift", 0.5):
                e.append(f"the carousel does not build: last third averages "
                         f"{sum(levels[-n3:]) / n3:.2f} against a first third of "
                         f"{sum(levels[:n3]) / n3:.2f} (lift {lift:+.2f}, needs "
                         f"{esc.get('min_lift', 0.5):+.2f}). Reorder it — the pictures are "
                         f"fine, the running order is flat.")

    # ---- congruence: same figure, inverted world --------------------------
    con = inst.get("congruence", {})
    axes = list(con.get("inverted", []))
    if not axes:
        e.append("congruence.inverted is empty — nothing has been declared to flip between "
                 "the hook and the payoff, which is the whole form")
    hits = [it.get("hits") or [] for it in src]
    for i, h in enumerate(hits):
        for a in h:
            if a not in axes:
                e.append(f"carousel item {i + 1} ({src[i]['still']}) hits undeclared axis {a!r} "
                         f"— declared: {axes}")
    for a in axes:
        if not any(a in h for h in hits):
            e.append(f"congruence axis {a!r} is declared but no carousel item hits it — "
                     f"either shoot it or stop claiming it")
    # One complaint per run, not one per picture in it — a six-long run is one mistake.
    run_cap = cspec.get("max_axis_run", 2)
    primary = [h[0] if h else None for h in hits] + [None]
    start = 0
    for i in range(1, len(primary)):
        if primary[i] == primary[i - 1]:
            continue
        run, p = i - start, primary[i - 1]
        if p and run > run_cap:
            e.append(f"carousel items {start + 1}–{i} all lead on axis {p!r} — {run} in a "
                     f"row, cap is {run_cap}. The payoff stops surprising.")
        start = i

    # ---- rights, restated at the format boundary --------------------------
    if hd.get("rights", {}).get("minors_in_frame") and not song.get("rights", {}).get("license"):
        e.append("hook has a minor in frame and the song has no declared license — "
                 "this cannot be published")
    return e
