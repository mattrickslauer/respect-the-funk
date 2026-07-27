#!/usr/bin/env python3
"""Score every generated still against the cast's reference photographs.

    python3 bin/check_likeness.py generated-test
    python3 bin/check_likeness.py --all --min 3
    python3 bin/check_likeness.py generated-test --truth 3,7,8,9,10 --force

This is the vision pass FORMAT-SPEC said did not exist: `congruence.invariant` claims the
same faces come back across 22 pictures, and until now nothing checked it.

WHY THIS IS THE SECOND VERSION. The first sent the whole 1080x1920 frame and asked "is
this the same person". It returned 5/5 "match" for every image a human had already
rejected — including one that is plainly a different man. Two reasons, both fixed here:

  1. THE FACE WAS TOO SMALL. In a full-body shot the head is maybe 6% of the pixels. The
     judge was scoring hair colour and vibe because that is all it could resolve. Now a
     first pass locates the face, the frame is cropped to it, and only the crop is judged
     against reference crops of comparable scale.

  2. THE QUESTION INVITED A YES. "Is this the same person" from an agreeable model is a
     yes. Now it must fill in a fixed feature table — face length/width, jaw, nose,
     brow, hairline, philtrum — and mark each same/different BEFORE it may give a score,
     and it is told to assume different unless the evidence says otherwise.

`--truth` takes the still numbers a human has judged bad and prints a confusion matrix,
so the checker's own accuracy is measured rather than asserted. It was wrong before; it
does not get to be trusted now on its say-so.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import rtf
from generate_stills import load_dotenv

MODEL = "gemini-2.5-flash"
RUBRIC = "v2-crop"

BOX_SCHEMA = {
    "type": "object",
    "properties": {
        "faces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string"},
                    "ymin": {"type": "integer"}, "xmin": {"type": "integer"},
                    "ymax": {"type": "integer"}, "xmax": {"type": "integer"},
                },
                "required": ["who", "ymin", "xmin", "ymax", "xmax"],
            },
        }
    },
    "required": ["faces"],
}

FEATURES = ["face_length_to_width", "jaw_shape", "nose", "brow_and_eye_spacing",
            "hairline", "philtrum_and_mouth_width"]

CMP_SCHEMA = {
    "type": "object",
    "properties": {
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string"},
                    "verdict": {"type": "string"},   # same | different | unclear
                    "detail": {"type": "string"},
                },
                "required": ["feature", "verdict", "detail"],
            },
        },
        "score": {"type": "integer"},
        "summary": {"type": "string"},
    },
    "required": ["features", "score", "summary"],
}

BOX_ASK = """Locate every human face in this image.

For each, give a bounding box in integer coordinates on a 0-1000 scale (0,0 is top-left),
and in `who` put a two-word description such as "adult man", "baby", "background woman".
Include partial and out-of-focus faces. If there are no faces, return an empty list."""

CMP_ASK = """You are a casting-continuity checker. Two crops follow: REFERENCE (the real
person, several photographs) and CANDIDATE (one frame from a generated shot).

Your default assumption is that these are DIFFERENT people. Generated images routinely
reproduce someone's hair, colouring and general type while getting the underlying skull
wrong, and that is precisely what you exist to catch. Colouring, hairstyle, lighting,
expression, age of the photograph, grain and camera angle are NOT evidence of identity.

Fill in the feature table first. For each feature say `same`, `different` or `unclear`,
with a short concrete detail. Only then give a score:

  5  every resolvable feature same — unmistakably this person
  4  all same but one, and that one is minor
  3  mixed evidence; a sibling would score here
  2  two or more features clearly different — same type, different person
  1  most features different — a different person
  0  no face resolvable in the candidate crop

A score of 4 or 5 requires at least four features marked `same` and none marked
`different`. Do not award 5 to a face you could not resolve."""


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


_CLIENT = None


def _client():
    # One client for the process. Building a fresh one per call closes the previous
    # httpx session out from under the retry wrapper.
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        import vertex
        vertex.load_dotenv(Path.cwd())
        # Express-mode api keys stopped being accepted by aiplatform (401
        # CREDENTIALS_MISSING), so prefer application-default credentials against the
        # project. The key path stays as a fallback. See vertex.py.
        if vertex.available():
            _CLIENT = genai.Client(vertexai=True, project=vertex.project(),
                                   location=vertex.location())
        else:
            _CLIENT = genai.Client(vertexai=True, api_key=os.environ["AGENT_GCP_KEY"])
    return _CLIENT


def _part(p: Path | bytes, mime="image/png"):
    from google.genai import types
    data = p.read_bytes() if isinstance(p, Path) else p
    if isinstance(p, Path):
        mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return types.Part.from_bytes(data=data, mime_type=mime)


def _ask(parts, schema):
    from google.genai import types
    r = _client().models.generate_content(
        model=MODEL, contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           response_schema=schema, temperature=0.0))
    return json.loads(r.text)


_BOXES: dict = {}


def detect_faces(img: Path, cache: Path, force: bool) -> list:
    """Every face in the frame, found once and reused for the whole cast.

    Detection is per IMAGE, not per person — running it once per cast member doubled the
    call count for an answer that could not differ between them.
    """
    from google.genai import types
    key = sha_file(img)
    if key in _BOXES:
        return _BOXES[key]
    blob = cache / f"faces-{key}.json"
    if blob.exists() and not force:
        _BOXES[key] = json.loads(blob.read_text())
        return _BOXES[key]
    faces = _ask([types.Part(text=BOX_ASK), _part(img)], BOX_SCHEMA).get("faces", [])
    cache.mkdir(parents=True, exist_ok=True)
    blob.write_text(json.dumps(faces))
    _BOXES[key] = faces
    return faces


def crop_face(img: Path, want_baby: bool, cache: Path, force: bool) -> bytes | None:
    """Return a padded crop of the relevant face, or None if there isn't one."""
    from PIL import Image

    boxes = detect_faces(img, cache, force)
    if not boxes:
        return None
    baby = [b for b in boxes if any(w in b["who"].lower()
                                    for w in ("baby", "infant", "child", "toddler"))]
    adult = [b for b in boxes if b not in baby]
    pool = baby if want_baby else adult
    if not pool:
        return None
    # Largest of the right kind — in a crowd shot the subject is the nearest face.
    b = max(pool, key=lambda x: (x["ymax"] - x["ymin"]) * (x["xmax"] - x["xmin"]))

    im = Image.open(img).convert("RGB")
    W, H = im.size
    x0, y0 = b["xmin"] / 1000 * W, b["ymin"] / 1000 * H
    x1, y1 = b["xmax"] / 1000 * W, b["ymax"] / 1000 * H
    padx, pady = (x1 - x0) * 0.35, (y1 - y0) * 0.35
    box = (max(0, int(x0 - padx)), max(0, int(y0 - pady)),
           min(W, int(x1 + padx)), min(H, int(y1 + pady)))
    if box[2] - box[0] < 24 or box[3] - box[1] < 24:
        return None
    out = im.crop(box)
    out = out.resize((512, int(512 * out.height / out.width)), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def judge(still: Path, cid: str, c: dict, cache: Path, force: bool) -> dict:
    from google.genai import types
    refs = [q for q in ((Path(c["_dir"]) / r).resolve()
                        for r in c.get("reference_frames", [])) if q.exists()]
    # Prefer the tight face crops the character file already lists.
    face_refs = [r for r in refs if "face" in r.name.lower()] or refs

    key = hashlib.sha256(json.dumps({
        "rubric": RUBRIC, "model": MODEL, "still": sha_file(still), "cid": cid,
        "refs": [sha_file(r) for r in face_refs],
    }, sort_keys=True).encode()).hexdigest()
    blob = cache / f"likeness-{key}.json"
    if blob.exists() and not force:
        return json.loads(blob.read_text())

    want_baby = bool(c.get("consent", {}).get("minor"))
    crop = crop_face(still, want_baby, cache, force)
    if crop is None:
        out = {"score": 0, "summary": "no face of this kind in frame", "features": []}
    else:
        parts = [types.Part(text=CMP_ASK),
                 types.Part(text=f"REFERENCE photographs of {c['name']}:")]
        parts += [_part(r) for r in face_refs]
        parts += [types.Part(text="CANDIDATE crop:"), _part(crop)]
        parts.append(types.Part(text="Features to fill in, in this order: "
                                     + ", ".join(FEATURES)))
        out = _ask(parts, CMP_SCHEMA)
    cache.mkdir(parents=True, exist_ok=True)
    blob.write_text(json.dumps(out, indent=2))
    return out


def run(video: str, floor: int, force: bool, truth: set) -> tuple[int, list]:
    edit_path = rtf.find_edit(video)
    load_dotenv(edit_path.parent.resolve())
    resolved, _ = rtf.resolve(edit_path)
    root = rtf.find_root(edit_path.parent)
    chars = rtf.characters(root, edit_path.parent, resolved.get("cast") or [])
    if not chars:
        print(f"{video}: no cast declared")
        return 0, []

    seq = next(e for e in resolved["timeline"] if "sequence" in e)
    stills = [Path(seq["source"]) / f"{i['still']}.png" for i in seq["items"]]
    stills += sorted(edit_path.parent.glob("*.png"))

    ids = list(chars)
    print(f"\n{video} — {len(stills)} images, cast {ids}")
    print(f"  {'image':<24}" + "".join(f"{c:>9}" for c in ids) + "   worst feature")
    bad, rows = [], []
    for s in stills:
        if not s.exists():
            continue
        cells, worst, note = "", 9, ""
        for cid in ids:
            v = judge(s, cid, chars[cid], root / ".cache", force)
            sc = v.get("score", 0)
            cells += f"{sc if sc else '·':>9}"
            if sc and sc < worst:
                diff = [f["feature"] for f in v.get("features", [])
                        if f.get("verdict") == "different"]
                worst = sc
                note = f"{cid}: " + (", ".join(diff[:3]) if diff else v.get("summary", ""))
        flag = "  ⟵ FAIL" if worst <= floor else ""
        print(f"  {s.stem:<24}{cells}   {note[:42]}{flag}")
        rows.append((s.stem, worst))
        if worst <= floor:
            bad.append(s.stem)

    if truth:
        got = {n for n, w in rows if w <= floor}
        tp = len(got & truth); fn = len(truth - got); fp = len(got - truth)
        judged = {n for n, _ in rows if n in truth or n in got or n.startswith("bowl-")}
        tn = len(judged) - tp - fn - fp
        print(f"\n  vs human labels: caught {tp}/{len(truth)} bad, missed {fn}, "
              f"false alarms {fp}, agreed-good {tn}")
        if fn:
            print(f"    MISSED: {', '.join(sorted(truth - got))}")
    print(f"  {len(bad)}/{len(stills)} at or below {floor}")
    return len(bad), rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--min", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--truth", help="comma-separated still numbers a human called bad")
    a = ap.parse_args()

    if a.all:
        root = rtf.find_root(Path.cwd())
        vids = sorted(p.name for p in (root / "videos").iterdir()
                      if (p / "edit.yaml").is_file())
    elif a.video:
        vids = [a.video]
    else:
        ap.error("pass a video id or --all")

    total = 0
    for v in vids:
        truth = set()
        if a.truth and len(vids) == 1:
            prefix = "bowl" if "generated" in v else "cali"
            truth = {f"{prefix}-{int(n):02d}" for n in a.truth.split(",")}
        n, _ = run(v, a.min, a.force, truth)
        total += n
    print(f"\n{total} image(s) flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
