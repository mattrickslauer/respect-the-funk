#!/usr/bin/env python3
"""Found dialogue hook -> a complete rtf.edit/v2 instance with a congruent payoff.

    .venv/bin/python bin/scaffold_edit.py lib/hooks/dialogue/storytime.clip.yaml \
        --id storytime-test

The hook half is found footage: somebody, somewhere, said a line that happens to make a
promise. The payoff half has to be built, and building it is the part
[FORMAT-SPEC](../FORMAT-SPEC.md) calls *the actual creative work*: declare what is
invariant (the figure) and what inverts (the ground), then write a picture per beat.

This drafts that, validates it against the format's rules, and fixes the one thing that is
mechanically fixable — the running order. What it cannot do is decide whether the joke is
funny.

**On likeness.** The speaker in a found clip is a real, identifiable person who did not
agree to appear in anything. So the default `invariant` is deliberately *not* their face:
it holds the room, the object, the wardrobe, the framing — everything that makes the
payoff read as the same world — and leaves the person out of the generated frames
entirely. That is a choice, not a limitation; `--invariant-person` overrides it, and if
you use it you own that decision.

Requires: pyyaml, ADC (see vertex.py).
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml

import rtf
import vertex

BRIEF = """You are writing the payoff half of a short vertical music video.

THE FORMAT. One found clip makes a promise in ONE spoken line. The music drop cuts the
line off on a hard cut. Then a carousel of still images, one per musical beat, pays the
promise back in an INVERTED world.

THE GEOMETRY, and it is the whole job. The payoff is the hook under a reflection:
  * The FIGURE is preserved exactly. Same place, same objects, same wardrobe, same
    framing language. This is what makes it read as a payoff and not as stock footage.
  * The GROUND is inverted along declared axes: cold->hot, small->huge, still->moving,
    ordinary->impossible, empty->crowded, day->night. Pick 3 to 6 axes.
Change the figure and it stops being a payoff. Leave the ground alone and there was never
a joke to pay off.

THE SPOKEN LINE (this is the promise you are paying off):
    "{quote}"

THE ATTACHED IMAGES ARE FRAMES FROM THE HOOK ITSELF. Look at them before you write
anything. They are the world you are inverting, and the payoff has to be recognisably the
SAME WORLD or it is not a payoff at all:
  * Match the MEDIUM exactly. If the hook is a 2D cartoon, every payoff image is a 2D
    cartoon in that same drawing style, with that same line weight and palette. If the
    hook is photographic, every payoff image is photographic. Getting this wrong is the
    single most visible way this format fails.
  * Match the LOCATION, the wardrobe, the props and the framing language you can actually
    see. Do not invent a room the hook does not contain.
  * If the frames are mostly empty background, that emptiness is part of the figure.

(Measured off the pixels, for reference: {looks})

HARD CONSTRAINTS:
1. Exactly {n_items} items. Each is ONE legible idea — a viewer gets {beat_ms}ms and will
   not parse two. Write it as a photograph, not as a story.
2. `hits` names the axes that item flips; the FIRST entry is its primary. Every axis you
   declare in `inverted` must be the primary of at least one item.
3. `intensity` is 1-5: how far past what a viewer would have guessed this goes. The
   sequence must CLIMB. Open around 1-2. The 5s belong in the last third.
4. The FINAL item is the end card: it is the release, not the climax, so its intensity
   must be strictly below the peak. Both worked examples end on someone asleep.
5. {person_rule}

Reply as JSON only:
{{"congruence": {{"from": "<the hook's world in one line>",
                  "to": "<the payoff's world in one line>",
                  "invariant": ["<what must not change>", ...],
                  "inverted": {{"<axis>": "<before> -> <after>", ...}}}},
  "identity": "<one sentence naming what must stay consistent across every image>",
  "anchor": "<describe the recurring subject(s) AND the exact visual style of the attached
             frames — medium, line weight, shading, palette — as it must be written into
             every single image prompt>",
  "items": [{{"still": "pay-01", "hits": ["axis"], "intensity": 2,
              "prompt": "<the full image prompt>"}}, ...]}}
"""

# What the hook's own frames are being used to hold constant. Both anchor the medium —
# that is the part words lose to the image model's defaults — but only one of them asks
# for the people back. Anchoring a real face is a decision about somebody who did not
# agree to any of this, so it is never the default.
ANCHOR_LABEL = {
    False: ("Frames from the ORIGINAL clip. Match their MEDIUM exactly (photographic vs "
            "drawn), and match the location, the light, the palette, the lens feel and "
            "the framing. Do NOT reproduce, redraw or approximate any PERSON visible in "
            "them — no faces, no likenesses. People may appear only as distant "
            "silhouettes, from behind, or out of focus, or not at all:"),
    True: ("Frames from the ORIGINAL clip this image must match. Reproduce their exact "
           "visual style — the same medium, the same line weight, the same flat colour "
           "and shading, the same character designs and wardrobe. Do not re-render them "
           "in a more polished or more realistic style:"),
}

PERSON_RULES = {
    False: ("NO recognisable person may appear. The speaker is a real individual who did "
            "not consent to being depicted. Hold the WORLD invariant, not the face: the "
            "room, the objects, the wardrobe, the light, the framing. People may appear "
            "only as silhouettes, from behind, out of focus, or not at all. Do not "
            "describe anyone's face."),
    True: ("The same person from the hook appears throughout, matched to the reference "
           "frames, same wardrobe and same age."),
}


def hook_frames(media: Path, dur_ms: int, work: Path, n: int = 3) -> list[Path]:
    """Stills from the hook, to be looked at rather than described.

    The first version of this tool passed the model the spoken line and two adjectives
    measured off the pixels ("a bright, static shot"). It drafted a photorealistic living
    room, in detail, for a hook that turned out to be a flat 2D cartoon on a white
    background — a payoff with nothing whatever in common with the thing it was paying
    off. You cannot preserve a figure you have never seen.
    """
    out = []
    for i in range(n):
        t = dur_ms / 1000.0 * (0.15 + 0.7 * (i / max(1, n - 1)))
        p = work / f"hookframe-{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(media),
                        "-frames:v", "1", "-vf", "scale=512:-2", str(p)],
                       capture_output=True)
        if p.exists():
            out.append(p)
    return out


def ask(prompt: str, images: list[Path] | None = None,
        model: str = "gemini-2.5-flash") -> dict:
    parts: list[dict] = [{"text": prompt}]
    for p in images or []:
        parts.append({"inline_data": {"mime_type": "image/jpeg",
                                      "data": base64.b64encode(p.read_bytes()).decode()}})
    body = json.dumps({
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.85},
    }).encode()
    req = urllib.request.Request(vertex.endpoint(model), data=body,
                                 headers=vertex.headers())
    with urllib.request.urlopen(req, timeout=240) as r:
        txt = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(txt)


# ---------------------------------------------------------------- ordering


def primary(it: dict) -> str:
    h = it.get("hits") or []
    return h[0] if h else "?"


def cost(items: list[dict], axes: list[str], max_run: int) -> float:
    """The format's own carousel rules, priced. Same shape as meme_engine's."""
    c = 0.0
    run, prev = 1, None
    for it in items:
        a = primary(it)
        run = run + 1 if a == prev else 1
        prev = a
        if run > max_run:
            c += 300.0
    led = {primary(it) for it in items[:-1]}
    c += 200.0 * len(set(axes) - led)

    n3 = max(1, len(items) // 3)
    first = sum(i["intensity"] for i in items[:n3]) / n3
    last = sum(i["intensity"] for i in items[-n3:]) / n3
    lift = last - first
    c += 150.0 * max(0.0, 0.5 - lift)
    # And keep rewarding lift past the floor. With only the penalty above, every order
    # clearing +0.50 scored identically on this term, so the search would happily trade a
    # drafted +1.57 down to +1.14 to buy a fractional improvement elsewhere.
    c -= 4.0 * min(lift, 2.5)

    top = max(i["intensity"] for i in items)
    if not any(i["intensity"] == top for i in items[int(len(items) * 2 / 3):]):
        c += 150.0
    if items[-1]["intensity"] >= top:
        c += 200.0                      # the end card is the release, not the climax
    c += 20.0 * max(0, items[0]["intensity"] - 2)   # do not open at full volume
    return c


def reorder(items: list[dict], axes: list[str], max_run: int, seed: int) -> list[dict]:
    """Hill-climb the running order. Same pictures, same cost, better sequence.

    The end card is positional and never moves; everything before it is free, which is
    exactly why authoring in the order the ideas arrived is not worth defending.
    """
    rng = random.Random(seed)
    body, end = items[:-1], items[-1]
    cur = cost(body + [end], axes, max_run)
    for _ in range(9000):
        i, j = rng.randrange(len(body)), rng.randrange(len(body))
        if i == j:
            continue
        body[i], body[j] = body[j], body[i]
        new = cost(body + [end], axes, max_run)
        if new <= cur:
            cur = new
        else:
            body[i], body[j] = body[j], body[i]
    return body + [end]


def report(items: list[dict], axes: list[str]) -> list[str]:
    n3 = max(1, len(items) // 3)
    first = sum(i["intensity"] for i in items[:n3]) / n3
    last = sum(i["intensity"] for i in items[-n3:]) / n3
    run, mx, prev = 1, 1, None
    for it in items:
        a = primary(it)
        run = run + 1 if a == prev else 1
        prev, mx = a, max(mx, run)
    led = {primary(it) for it in items}
    top = max(i["intensity"] for i in items)
    late = any(i["intensity"] == top for i in items[int(len(items) * 2 / 3):])
    return [
        f"  items        {len(items)}",
        f"  axes led     {len(led & set(axes))}/{len(axes)} ({', '.join(sorted(axes))})",
        f"  max axis run {mx}",
        f"  lift         {last - first:+.2f} (floor +0.50)",
        f"  peak         {top} in final third: {'yes' if late else 'NO'}; "
        f"end card {items[-1]['intensity']}",
    ]


# ---------------------------------------------------------------- main


def reorder_only(edit_path: Path, args) -> int:
    """Re-run the order pass on an edit that already exists.

    Reordering is free and rerolling the draft is not — a good congruence block should not
    have to be regenerated (and risk being replaced by a worse one) just to re-sort the
    pictures underneath it.
    """
    if edit_path.is_dir():
        edit_path = edit_path / "edit.yaml"
    edit = yaml.safe_load(edit_path.read_text())
    items = edit["carousel"]["items"]
    axes = list(edit["congruence"]["inverted"].keys())
    print("before:")
    for ln in report(items, axes):
        print(ln)
    items = reorder(items, axes, args.max_axis_run, args.seed)
    for i, it in enumerate(items):
        it["still"] = f"pay-{i + 1:02d}"
    edit["carousel"]["items"] = items
    print("after:")
    for ln in report(items, axes):
        print(ln)
    edit_path.write_text(yaml.safe_dump(edit, sort_keys=False, width=100,
                                        allow_unicode=True))
    print(f"\n-> {edit_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hook", help="path to a hook .clip.yaml")
    ap.add_argument("--id", help="video id (default: the hook's id + '-test')")
    ap.add_argument("--song", default="losing-sleep")
    ap.add_argument("--max-axis-run", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-anchor", action="store_true",
                    help="do not use hook frames as style references on every prompt")
    ap.add_argument("--reorder-only", action="store_true",
                    help="re-run only the running-order pass on an existing edit.yaml")
    ap.add_argument("--invariant-person", action="store_true",
                    help="hold the speaker's likeness invariant (see the docstring)")
    args = ap.parse_args()

    if args.reorder_only:
        return reorder_only(Path(args.hook).resolve(), args)

    hook_path = Path(args.hook).resolve()
    if not hook_path.is_file():
        sys.exit(f"no hook descriptor at {hook_path}")
    hook = yaml.safe_load(hook_path.read_text())
    quote = (hook.get("audio") or {}).get("quote")
    if not quote:
        sys.exit(f"{hook_path.name} has no audio.quote — it cannot be a hook")

    root = rtf.find_root(hook_path)
    vertex.load_dotenv(Path.cwd())
    if not vertex.available():
        sys.exit("no Vertex credentials — see vertex.py")

    song = yaml.safe_load((root / "lib" / "songs" / f"{args.song}.song.yaml").read_text())
    beat_ms = 60000.0 / float(song["measured"]["bpm"])
    carousel_beats = float(song["tuning"]["carousel_beats"])
    end_card_beats = 2.0
    n_items = int(carousel_beats - end_card_beats) + 1     # last item holds 2 beats

    vid_id = args.id or f"{hook['id']}-test"
    video = root / "videos" / vid_id
    video.mkdir(parents=True, exist_ok=True)
    (video / "stills").mkdir(exist_ok=True)

    # The renderer resolves a hook as <video>/<clip id>.clip.yaml, so the clip travels
    # into the video folder — a video directory is meant to be self-contained enough to
    # zip and hand to somebody.
    shutil.copy2(hook_path.parent / hook["media"], video / hook["media"])
    shutil.copy2(hook_path, video / f"{hook['id']}.clip.yaml")

    f = hook.get("features", {})
    looks = (f"a {'dark' if f.get('luma', 0.5) < 0.35 else 'bright'}, "
             f"{'static' if f.get('energy', 0) < 0.3 else 'moving'} shot; "
             f"{hook.get('logline') or ''}")

    print(f"hook   {hook['id']}  \"{quote}\"")
    print(f"song   {song['title']} @ {song['measured']['bpm']} bpm, "
          f"{carousel_beats:g} carousel beats -> {n_items} items\n")

    work = root / ".cache" / "scaffold"
    work.mkdir(parents=True, exist_ok=True)
    frames = hook_frames(video / hook["media"], hook["source_meta"]["duration_ms"], work)
    if not frames:
        sys.exit("could not extract hook frames — the payoff would be drafted blind")
    print(f"looking at {len(frames)} hook frames\n")

    data = ask(BRIEF.format(quote=quote, looks=looks, n_items=n_items,
                            beat_ms=int(beat_ms),
                            person_rule=PERSON_RULES[bool(args.invariant_person)]),
               images=frames)
    cong = data["congruence"]
    items = data["items"]
    axes = list(cong.get("inverted", {}).keys())

    # The model is asked for a count and does not always honour it; the beat grid does
    # not negotiate, so the list is trimmed or padded before anything else looks at it.
    if len(items) > n_items:
        items = items[:n_items]
    while len(items) < n_items:
        items.append(dict(items[-1]))
    for i, it in enumerate(items):
        it["still"] = f"pay-{i + 1:02d}"
        it["intensity"] = max(1, min(5, int(it.get("intensity", 2))))
        it["hits"] = [h for h in (it.get("hits") or []) if h in axes] or [axes[0]]

    print("as drafted:")
    for ln in report(items, axes):
        print(ln)
    items = reorder(items, axes, args.max_axis_run, args.seed)
    for i, it in enumerate(items):
        it["still"] = f"pay-{i + 1:02d}"
    print("after reorder:")
    for ln in report(items, axes):
        print(ln)

    # THE STYLE ANCHOR. Telling the image model "2D cartoon, simple line art" in words
    # produced polished rendered illustration — a different medium from the hook, which is
    # the most visible way this format fails. The format already has the mechanism for
    # this and it is the same one that stops character drift: put the actual frames in
    # front of the model on every prompt, not a description of them.
    cast: list[str] = []
    if not args.no_anchor and data.get("anchor"):
        cdir = video / "characters"
        cdir.mkdir(exist_ok=True)
        refs = []
        for i, fr in enumerate(frames):
            dest = cdir / f"{hook['id']}-ref{i + 1}.jpg"
            shutil.copy2(fr, dest)
            refs.append(dest.name)
        (cdir / f"{hook['id']}.character.yaml").write_text(yaml.safe_dump({
            "schema": "rtf.character/v1",
            "id": hook["id"],
            "name": hook["id"],
            "role_in_frame": "the subject of the hook",
            "prompt_description": data["anchor"],
            "reference_label": (
                ANCHOR_LABEL[bool(args.invariant_person)]),
            "reference_frames": refs,
        }, sort_keys=False, allow_unicode=True))
        cast = [hook["id"]]
        print(f"style anchor: {len(refs)} frames -> characters/{hook['id']}.character.yaml")

    edit = {
        "schema": "rtf.edit/v2",
        "id": vid_id,
        "format": "promise-payoff",
        "song": args.song,
        "cast": cast,
        "title": f"{hook['id']} — found hook, congruent payoff",
        "intent": (f"Found dialogue makes the promise; the payoff inverts the world it was "
                   f"said in. Hook line: \"{quote}\""),
        "hook": {"clip": hook["id"], "t_in": 0,
                 "t_out": hook["source_meta"]["duration_ms"]},
        "congruence": {
            "from": cong.get("from", ""),
            "to": cong.get("to", ""),
            "invariant": cong.get("invariant", []),
            "inverted": cong.get("inverted", {}),
        },
        "prompt": {
            "characters_block": (f"{{{hook['id']}.prompt_description}}" if cast else ""),
            "identity": data.get("identity", ""),
        },
        "carousel": {"items": [
            {"still": it["still"], "hits": it["hits"], "intensity": it["intensity"],
             "prompt": it["prompt"]} for it in items]},
    }
    if hook.get("hook_recommended", {}).get("pre_roll_bars"):
        edit["tuning"] = {"pre_roll": dict(
            song["tuning"]["pre_roll"],
            bars=hook["hook_recommended"]["pre_roll_bars"])}

    out = video / "edit.yaml"
    out.write_text(yaml.safe_dump(edit, sort_keys=False, width=100, allow_unicode=True))
    print(f"\n-> {out}\n   next: .venv/bin/python bin/render_edit.py {vid_id} --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
