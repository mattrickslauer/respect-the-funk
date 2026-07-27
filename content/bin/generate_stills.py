#!/usr/bin/env python3
"""Generate the stills an edit references, content-addressed and cached.

    python3 generate_stills.py california-test.edit.yaml --dry-run
    python3 generate_stills.py california-test.edit.yaml --provider openai

Assembles each prompt from the character files + the edit's still_prompts (so faces and
wardrobe stay consistent), hashes the full request, and only calls the provider for
hashes it has never seen. Every result lands in .cache/<sha>.png and is linked to the
name the edit expects, with an rtf.clip/v1 sidecar recording model, prompt and hash.

This is BUILD-SPEC §2b rule 5 in miniature: identical prompt+model+params reuses the
existing object instead of regenerating. Re-running is free; changing a prompt costs
exactly one image.

Keys come from the nearest .env: OPENAI_API_KEY, or GEMINI_API_KEY /
AGENT_GCP_KEY for Vertex AI express mode.
"""

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml

import rtf
import vertex

# Rough list prices, dated — "price it, date it". Update alongside pricing.yaml.
PRICES_USD = {           # as of 2026-07
    "gpt-image-1": 0.19,          # 1024x1536, high quality
    "gemini-2.5-flash-image": 0.04,
}


# provider → env vars searched in order, first one set wins
KEY_VARS = {
    "gemini": ("GEMINI_API_KEY", "AGENT_GCP_KEY"),
    "openai": ("OPENAI_API_KEY",),
}


def load_dotenv(start: Path) -> None:
    """Read the nearest .env walking up from `start`. Does not overwrite real env vars."""
    for d in [start, *start.parents]:
        f = d / ".env"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
        return


def _key(provider: str) -> str:
    for var in KEY_VARS[provider]:
        if os.environ.get(var):
            return os.environ[var]
    raise SystemExit(f"no key — set one of {', '.join(KEY_VARS[provider])}")


def load(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- prompts


def build_prompts(edit: Path) -> tuple[dict, list[dict], list[str]]:
    """Resolve every still into a full prompt with character descriptions inlined."""
    base = edit.parent
    e, errs = rtf.resolve(edit)     # v2 instance → flat edit; v1 passes through
    sp = e["still_prompts"]

    # The cast is declared by the edit, not discovered by globbing a shared directory.
    # Globbing was fine with one cast in the project and silently wrong with two.
    root = rtf.find_root(base)
    chars = rtf.characters(root, base, e.get("cast") or [])

    block = sp.get("characters_block") or " and ".join(
        " ".join(chars[cid]["prompt_description"].split()) for cid in chars)
    for cid, c in chars.items():
        block = block.replace(f"{{{cid}.prompt_description}}", " ".join(c["prompt_description"].split()))

    out = []
    for entry in e["timeline"]:
        if "sequence" not in entry:
            continue
        for item in entry["items"]:
            body = sp["items"][item["prompt_ref"]]
            prompt = " ".join(f"{block} {sp.get('identity', '')} {body} {sp.get('shared_suffix', '')}".split())
            # NO GLOBAL "Avoid:" LIST. Negatives used to be concatenated across the whole
            # cast and applied to the entire image, so one character's negative fought
            # another's description: "thin face" (meant for the baby) against "Narrow
            # face" (the man), and "short hair, clean-shaven" (meant for the man) against
            # a 7-month-old. Each negative now travels with its own character — attached
            # to their labelled reference group below, and naming them explicitly.
            # Grouped and labelled, not one flat pile. Six unlabelled images give the
            # model no way to know which three are the man and which three are the baby;
            # in frames where someone is not the focal subject it falls back on the text
            # description and invents a face. The label is the whole fix.
            groups = []
            for cid, c in chars.items():
                paths = [p for p in ((Path(c["_dir"]) / r).resolve()
                                     for r in c.get("reference_frames", [])) if p.exists()]
                if paths:
                    # A character file may override the label. The default is written for
                    # a face; a style anchor taken from a found hook is not a face, and
                    # telling the model to "match this face exactly" against three frames
                    # of a cartoon is asking for the wrong thing.
                    lab = c.get("reference_label") or (
                        f"Reference photographs of {c['name']} — "
                        f"{c.get('role_in_frame') or c['name']} "
                        f"in this scene. Match this face exactly:")
                    if c.get("negative_prompt"):
                        # Scoped by naming AND by adjacency: Gemini's parts array is
                        # ordered, so this sits immediately before that person's photos.
                        lab += (f" Do not render {c['name']} as any of these — they apply "
                                f"to {c['name']} ONLY and to nobody else in the frame: "
                                f"{' '.join(c['negative_prompt'].split())}.")
                    groups.append({"label": lab, "paths": paths})
            out.append({
                "name": item["still"],
                "prompt": prompt,
                "groups": groups,
                "refs": [p for g in groups for p in g["paths"]],
                "dest": (base / entry["source"] / f"{item['still']}.png").resolve(),
                "subjects": sorted(chars),
            })
    return e, out, errs


# ---------------------------------------------------------------- providers


RETRY_CODES = {408, 429, 500, 502, 503, 504}


class NoImage(RuntimeError):
    """The model answered, but with prose instead of a picture.

    Usually a safety refusal on one prompt. It used to raise SystemExit, so a single
    awkward prompt killed the whole batch — on a 22-image run that threw away the fifteen
    images that had not been attempted yet, for one that could simply have been skipped.
    """


def _post(req: urllib.request.Request, tries=6) -> dict:
    """POST with exponential backoff on transient provider errors.

    429 is normal on image endpoints, not exceptional — without a retry a long batch
    dies halfway. Anything already generated is in the cache, so a crash is recoverable,
    but there is no reason to make the human re-run it.
    """
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            if ex.code not in RETRY_CODES or attempt == tries - 1:
                raise SystemExit(f"provider error {ex.code}: {ex.read().decode()[:600]}")
            wait = min(60, 4 * 2 ** attempt)
            print(f"     {ex.code} — retry in {wait}s ({attempt + 1}/{tries - 1})", flush=True)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as ex:
            if attempt == tries - 1:
                raise SystemExit(f"network error: {ex}")
            time.sleep(min(60, 4 * 2 ** attempt))
    raise SystemExit("unreachable")


def _multipart(fields: list[tuple[str, str]], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    b = f"----rtf{uuid.uuid4().hex}"
    buf = bytearray()
    for k, v in fields:
        buf += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, p in files:
        ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        buf += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                f"filename=\"{p.name}\"\r\nContent-Type: {ct}\r\n\r\n").encode()
        buf += p.read_bytes() + b"\r\n"
    buf += f"--{b}--\r\n".encode()
    return bytes(buf), f"multipart/form-data; boundary={b}"


def gen_openai(prompt: str, groups: list[dict], model: str, size: str) -> bytes:
    key = _key("openai")
    # the edits endpoint has no way to label images, so fold the labels into
    # the prompt text and rely on ordering
    refs = [p for g in groups for p in g["paths"]]
    if refs:
        # the edits endpoint takes reference images — text alone will not hold a face
        body, ct = _multipart(
            [("model", model), ("prompt", prompt), ("size", size), ("n", "1")],
            [("image[]", p) for p in refs],
        )
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/edits", data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": ct})
    else:
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=json.dumps({"model": model, "prompt": prompt, "size": size, "n": 1}).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    return base64.b64decode(_post(req)["data"][0]["b64_json"])


def gen_gemini(prompt: str, groups: list[dict], model: str, size: str) -> bytes:
    """Vertex AI.

    Originally express mode: an api key against
    `aiplatform.googleapis.com/v1/publishers/...`. That path now returns 401
    CREDENTIALS_MISSING — "API keys are not supported by this API" — so the primary route
    is the project-scoped regional endpoint with an ADC bearer token (see `vertex.py`).
    The express path is kept as a fallback for whoever still has a key that works.
    """
    def img(p: Path) -> dict:
        return {"inline_data": {
            "mime_type": mimetypes.guess_type(p.name)[0] or "image/jpeg",
            "data": base64.b64encode(p.read_bytes()).decode()}}

    # Interleave label → images → label → images. Gemini's parts array is ordered, so a
    # text part immediately before a group scopes it to those images.
    parts: list[dict] = [{"text": prompt}]
    for g in groups:
        parts.append({"text": g["label"]})
        parts.extend(img(p) for p in g["paths"])
    body = json.dumps({
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                             "imageConfig": {"aspectRatio": "9:16"}},
    }).encode()
    if vertex.available():
        req = urllib.request.Request(vertex.endpoint(model), data=body,
                                     headers=vertex.headers())
    else:
        req = urllib.request.Request(
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/"
            f"{model}:generateContent", data=body,
            headers={"x-goog-api-key": _key("gemini"), "Content-Type": "application/json"})
    for part in _post(req)["candidates"][0]["content"]["parts"]:
        for k in ("inlineData", "inline_data"):
            if k in part:
                return base64.b64decode(part[k]["data"])
    raise NoImage("no image in response — the model replied with text only")


PROVIDERS = {
    "openai": (gen_openai, "gpt-image-1", "1024x1536"),
    "gemini": (gen_gemini, "gemini-2.5-flash-image", "9:16"),   # native vertical
}


# ---------------------------------------------------------------- sidecars


def write_sidecar(dest: Path, item: dict, model: str, provider: str, digest: str, edit_id: str):
    """Every generated still gets a descriptor — CLIP-SPEC rule 3 means no descriptor,
    no use, and `ai` is a legal rights.source only when the provenance is recorded."""
    doc = {
        "schema": "rtf.clip/v1",
        "id": item["name"],
        "media": dest.name,
        "title": item["name"],
        "logline": item["prompt"][:180],
        "rights": {"source": "ai", "owner": "anthony", "people_release": True,
                   "minors_in_frame": "cosima" in item["subjects"],
                   "notes": f"Synthetic. Generated for edit '{edit_id}'. Disclose as AI."},
        "subjects": item["subjects"],
        "role": {"primary": "b_roll", "can_lead": False, "can_follow": True},
        "provenance": {"provider": provider, "model": model, "prompt": item["prompt"],
                       "sha256": digest, "cache_key": f".cache/{digest}.png"},
    }
    dest.with_suffix(".clip.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edit", help="video id, video dir, or path to edit.yaml")
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="gemini")
    ap.add_argument("--model")
    ap.add_argument("--size")
    ap.add_argument("--dry-run", action="store_true", help="show prompts and cache status, spend nothing")
    ap.add_argument("--no-refs", action="store_true", help="text-only prompts (faces will drift)")
    ap.add_argument("--force", action="store_true", help="ignore the cache and regenerate")
    ap.add_argument("--only", help="comma-separated still names — generate just these")
    ap.add_argument("--reroll", type=int, default=0,
                    help="nonce in the cache key: a fresh roll of the same prompt, "
                         "kept alongside the previous one rather than overwriting it")
    args = ap.parse_args()

    edit_path = rtf.find_edit(args.edit)
    load_dotenv(edit_path.parent.resolve())
    edit, items, fmt_errs = build_prompts(edit_path)
    if fmt_errs:
        # A dry run still prints the plan — you are usually mid-authoring when you see
        # these. A real run refuses: generating 22 images against an edit that isn't the
        # format it claims to be is the most expensive way to find that out.
        print("FORMAT VALIDATION FAILED:", file=sys.stderr)
        for x in fmt_errs:
            print("  •", x, file=sys.stderr)
        if not args.dry_run:
            return 1
        print(file=sys.stderr)
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        items = [i for i in items if i["name"] in keep]
    fn, model, size = PROVIDERS[args.provider]
    model, size = args.model or model, args.size or size

    cache = rtf.find_root(edit_path.parent) / ".cache"
    cache.mkdir(parents=True, exist_ok=True)

    hits = misses = 0
    plan = []
    for it in items:
        if args.no_refs:
            it["refs"], it["groups"] = [], []
        key = sha({"provider": args.provider, "model": model, "size": size,
                   "prompt": it["prompt"], "reroll": args.reroll,
                   "refs": [hashlib.sha256(r.read_bytes()).hexdigest() for r in it["refs"]],
                   "labels": [g["label"] for g in it["groups"]]})
        blob = cache / f"{key}.png"
        cached = blob.exists() and not args.force
        hits, misses = hits + cached, misses + (not cached)
        plan.append((it, key, blob, cached))

    cost = misses * PRICES_USD.get(model, 0.0)
    print(f"{len(plan)} stills · {hits} cached · {misses} to generate")
    print(f"provider {args.provider} / {model} / {size} · est. ${cost:.2f}\n")

    for it, key, blob, cached in plan:
        print(f"  {'CACHED ' if cached else 'GENERATE'} {it['name']}  {key[:12]}  "
              f"{len(it['refs'])} ref(s)")
        if args.dry_run:
            print(f"           {it['prompt'][:150]}…")

    if args.dry_run:
        print("\ndry run — nothing generated, nothing spent")
        return 0

    if args.provider == "gemini":
        vertex.load_dotenv(Path.cwd())
        if not vertex.available():
            _key("gemini")            # no ADC — fall back to needing an express key
    else:
        _key(args.provider)   # fail before spending, not halfway through

    skipped: list[str] = []
    for it, key, blob, cached in plan:
        it["dest"].parent.mkdir(parents=True, exist_ok=True)
        if not cached:
            print(f"→ generating {it['name']} …", flush=True)
            try:
                data = fn(it["prompt"], it["groups"], model, size)
            except NoImage as exc:
                # One refusal is not a reason to abandon the run. The still is left
                # missing, render_edit draws a labelled placeholder for it, and the
                # summary says which ones to re-run.
                print(f"   ! {it['name']}: {exc} — skipped", flush=True)
                skipped.append(it["name"])
                continue
            blob.write_bytes(data)
            (cache / f"{key}.json").write_text(json.dumps(
                {"provider": args.provider, "model": model, "size": size,
                 "prompt": it["prompt"], "refs": [str(r) for r in it["refs"]]},
                indent=2))
        if it["dest"].exists() or it["dest"].is_symlink():
            it["dest"].unlink()
        os.link(blob, it["dest"])       # hardlink: one blob, many names, no copy
        write_sidecar(it["dest"], it, model, args.provider, key, edit["id"])

    print(f"\n✓ {len(plan) - len(skipped)} stills in place · spent on {misses}")
    if skipped:
        print(f"  ! {len(skipped)} skipped (model returned no image): "
              f"{', '.join(skipped)}\n"
              f"    re-run to retry them, or reword those prompts")
    print("  now: python3 render_edit.py", edit_path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
