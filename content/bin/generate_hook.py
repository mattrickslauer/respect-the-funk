#!/usr/bin/env python3
"""Generate an edit's hook clip: a first frame from the image model, then Veo for motion.

    python3 generate_hook.py generated-test.edit.yaml --first-frame-only
    python3 generate_hook.py generated-test.edit.yaml --dry-run
    python3 generate_hook.py generated-test.edit.yaml

Identity is the hard part and the image model already solves it — it holds these faces off
ten reference photographs and made every carousel still. So the hook's FIRST FRAME is
generated there, through the exact prompt-assembly and cache machinery in
generate_stills.py, and only then handed to Veo as its starting image. Veo is asked for
motion, performance and the spoken line, and never asked to invent a face.

Two credentials, two jobs:
  • image model  → AGENT_GCP_KEY (Vertex express). Already working.
  • Veo          → a PROJECT-SCOPED token. Express keys cannot call predictLongRunning;
                   they return RESOURCE_PROJECT_INVALID. Set GCP_PROJECT and one of:
                     GCP_ACCESS_TOKEN=<paste from `gcloud auth print-access-token`>
                     GOOGLE_APPLICATION_CREDENTIALS=<service-account JSON>  (needs google-auth)
                     gcloud on PATH, already logged in

⚠ The Veo half of this script has never been run against a live endpoint — there was no
  project-scoped credential on this machine when it was written. The request/response
  shapes come from the documented API, not from a successful call. Expect to correct
  field names on the first real run; --dry-run prints the exact payload so you can see
  what it would send before it sends it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

import generate_stills as gs
import rtf

POLL_S = 15
POLL_MAX = 40          # ~10 minutes; Veo is typically 1–3


# ---------------------------------------------------------------- credentials


def bearer() -> str:
    """A project-scoped OAuth token, by whichever route this machine has."""
    if tok := os.environ.get("GCP_ACCESS_TOKEN"):
        return tok.strip()
    if shutil.which("gcloud"):
        r = subprocess.run(["gcloud", "auth", "print-access-token"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    if sa := os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError:
            raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS is set but google-auth is "
                             "not installed — `.venv/bin/pip install google-auth`")
        creds = service_account.Credentials.from_service_account_file(
            sa, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        return creds.token
    raise SystemExit(
        "no project-scoped credential. Veo needs one — AGENT_GCP_KEY is an express key "
        "and express keys cannot call predictLongRunning.\n"
        "  set GCP_ACCESS_TOKEN, or GOOGLE_APPLICATION_CREDENTIALS, or install gcloud")


# ---------------------------------------------------------------- the first frame


def gen_still(edit_path: Path, inst: dict, body: str, cast: list, identity: str,
              tag: str, reroll: int, force: bool, suffix: str = None) -> tuple[Path, str]:
    """Generate (or reuse) one still through the carousel's own prompt pipeline.

    Deliberately the same assembly as every carousel still — same character block, same
    identity clause, same labelled reference groups, same content-addressed cache — so
    the man in the hook is the man in the payoff by construction rather than by luck.

    `cast` is explicit because WHO IS IN THIS FRAME differs shot to shot, and describing
    someone is what puts them in it.
    """
    base = edit_path.parent
    g = inst["hook"]["generate"]

    root = rtf.find_root(base)
    fmt = rtf.load(root / "lib" / "formats" / f"{inst['format']}.format.yaml")
    pr = rtf.merge(fmt.get("prompt", {}), inst.get("prompt", {}))
    chars = rtf.characters(root, base, inst.get("cast") or [])

    cast = [cid for cid in cast if cid in chars]
    block = " and ".join(" ".join(chars[cid]["prompt_description"].split()) for cid in cast)
    prompt = " ".join(f"{block} {identity} {body} "
                      f"{pr['shared_suffix'] if suffix is None else suffix}".split())
    # Negatives travel with their character, in the labelled group below — never as one
    # global list, which made each cast member's negative fight the others' description.

    # A reference photograph of a child, attached to a request that is not about a child,
    # is also the fastest way to have a video model decline the whole job.
    groups = []
    for cid in cast:
        c = chars[cid]
        paths = [p for p in ((Path(c["_dir"]) / r).resolve()
                             for r in c.get("reference_frames", [])) if p.exists()]
        if paths:
            lab = (f"Reference photographs of {c['name']} — "
                   f"{c.get('role_in_frame') or c['name']} in this scene. "
                   f"Match this face exactly:")
            if c.get("negative_prompt"):
                lab += (f" Do not render {c['name']} as any of these — they apply to "
                        f"{c['name']} ONLY and to nobody else in the frame: "
                        f"{' '.join(c['negative_prompt'].split())}.")
            groups.append({"label": lab, "paths": paths})

    model, size = "gemini-2.5-flash-image", "9:16"
    key = gs.sha({"provider": "gemini", "model": model, "size": size, "prompt": prompt,
                  "reroll": reroll, "kind": f"hook_{tag}",
                  "refs": [gs.hashlib.sha256(p.read_bytes()).hexdigest()
                           for gp in groups for p in gp["paths"]],
                  "labels": [gp["label"] for gp in groups]})
    cache = root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    blob = cache / f"{key}.png"

    if blob.exists() and not force:
        print(f"  CACHED  {tag}  {key[:12]}")
    else:
        print(f"→ generating {tag}  {key[:12]}  {len(groups)} ref group(s) …",
              flush=True)
        blob.write_bytes(gs.gen_gemini(prompt, groups, model, size))

    dest = (base / f"{inst['hook']['clip']}.{tag}.png").resolve()
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    os.link(blob, dest)
    return dest, prompt


def slot_cast(inst: dict, chars: dict, key: str) -> list:
    """Who is described in a given generated frame. Minors excluded unless named."""
    g = inst["hook"]["generate"]
    cast = g.get(key)
    if cast is None:
        cast = [cid for cid in g.get("reference_characters", [])
                if not chars.get(cid, {}).get("consent", {}).get("minor")]
    return cast


def load_chars(edit_path: Path, cast: list) -> dict:
    video = edit_path.parent
    return rtf.characters(rtf.find_root(video), video, cast)


# ---------------------------------------------------------------- assembly


def assemble(segs: list, out: Path, w=1080, h=1920, fps=30) -> None:
    """Splice per-slot segments into one hook clip.

    A segment is (path, ms, move). A video path is trimmed to `ms`; an image is held for
    `ms` with an optional zoompan move. This is the editing step, and it belongs here
    rather than inside a prompt: a video model is a single-shot generator, and asking one
    to honour "Beat 3 (0.96-2.40s)" gets you inert coverage paced to its own taste. It
    makes the shots; the cut is ours.
    """
    norm = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps={fps},setsar=1,format=yuv420p")
    inputs, filt, vlabels, alabels = [], [], [], []
    for i, (path, ms, move) in enumerate(segs):
        n = ms / 1000
        if path.suffix.lower() in (".mp4", ".mov"):
            inputs += ["-t", f"{n}", "-i", str(path)]
            filt.append(f"[{i}:v]{norm}[v{i}]")
            filt.append(f"[{i}:a]atrim=0:{n},asetpts=N/SR/TB[a{i}]")
        else:
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{n}", "-i", str(path)]
            mv = animate_move(move, round(ms / 1000 * fps), w, h, fps) if move else ""
            filt.append(f"[{i}:v]{mv}{norm}[v{i}]")
            # Silence of exactly the right length, so the spoken line keeps its position
            # instead of being stretched across the stills either side of it.
            filt.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{n},asetpts=N/SR/TB[a{i}]")
        vlabels.append(f"[v{i}]"); alabels.append(f"[a{i}]")

    fc = ";".join(filt)
    fc += f";{''.join(vlabels)}concat=n={len(segs)}:v=1:a=0[vout]"
    fc += f";{''.join(alabels)}concat=n={len(segs)}:v=0:a=1[aout]"
    total = sum(ms for _, ms, _ in segs) / 1000
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", fc,
           "-map", "[vout]", "-map", "[aout]", "-t", f"{total}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    if subprocess.call(cmd) != 0:
        raise SystemExit("ffmpeg failed assembling the hook")
    print(f"  \u2713 {out.name} \u2014 " + " + ".join(
        f"{ms}ms {p.suffix.lstrip('.')}" for p, ms, _ in segs))


def animate_move(kind: str, n: int, w: int, h: int, fps: int) -> str:
    """The same zoompan vocabulary render_edit uses on carousel stills."""
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    moves = {
        "push_in":  (f"1+0.10*on/{n}", cx, cy),
        "pull_out": (f"1.10-0.10*on/{n}", cx, cy),
        "snap":     (f"1.35-0.35*min(1,on/({n}*0.4))", cx, cy),
    }
    if kind not in moves:
        return ""
    z, x, y = moves[kind]
    return f"zoompan=z=\'{z}\':x=\'{x}\':y=\'{y}\':d=1:s={w}x{h}:fps={fps},"


# ---------------------------------------------------------------- veo


VEO_DURATIONS = (4, 6, 8)      # Veo renders fixed lengths, not arbitrary ones


def compose_prompt(inst: dict, desc: dict, grid: list) -> str:
    """Build the video prompt: style preamble, then one line per cadence slot, then close.

    The beat timings are NOT typed anywhere. They come off the cadence grid, which comes
    off the song's measured beat — so the seconds quoted to the model cannot disagree with
    the descriptor, the cadence or the track. Retune the song and this prompt retimes
    itself; the alternative is three files that each believe a different thing about when
    the line happens.
    """
    g = inst["hook"]["generate"]
    if g.get("prompt"):
        return " ".join(g["prompt"].split())      # hand-written, still allowed

    beats = desc.get("beats", [])
    parts = [" ".join(g.get("style", "").split())]
    for i, (slot, b) in enumerate(zip(grid, beats), start=1):
        line = (f"Beat {i} ({slot['t_in'] / 1000:.2f}–{slot['t_out'] / 1000:.2f}s, "
                f"{slot['role']}): {b.get('frame', '')}")
        if slot["role"] == "line" and g.get("dialogue"):
            line += (f' He says, deadpan and completely flat, with no smile and no '
                     f'emphasis: "{g["dialogue"]}"')
        parts.append(line)
    parts.append(" ".join(g.get("closing", "").split()))
    return " ".join(" ".join(parts).split())


def veo_payload(inst: dict, frame: Path | None, prompt: str, duration_ms: int) -> dict:
    g = inst["hook"]["generate"]
    # Round UP to a length Veo actually renders and let render_edit trim back to the cut
    # point. Rounding down would shorten the hook and move the hard cut, which is the one
    # thing in this format that may not move. Overshoot is free; the trim is exact.
    want = duration_ms / 1000
    secs = next((d for d in VEO_DURATIONS if d >= want), VEO_DURATIONS[-1])
    inst_obj: dict = {"prompt": prompt}
    if frame:
        inst_obj["image"] = {"bytesBase64Encoded": base64.b64encode(frame.read_bytes()).decode(),
                             "mimeType": "image/png"}
    return {
        "instances": [inst_obj],
        "parameters": {
            "aspectRatio": g.get("aspect", "9:16"),
            "durationSeconds": secs,
            "sampleCount": 1,
            "generateAudio": bool(g.get("generate_audio", True)),
            # allow_adult is the default ceiling on Vertex. allow_all is allowlist-gated
            # and, for a real child's likeness, may not be grantable at all.
            "personGeneration": g.get("person_generation", "allow_adult"),
        },
    }


MINOR_WORDS = ("baby", "infant", "toddler", "child", "month-old", "months old")


def minor_risk(inst: dict, body: str) -> str:
    """Warn before spending when the prompt asks for someone the policy may not allow.

    This is a policy limit, not a credential limit — no amount of billing setup changes
    it. Worth saying out loud before a render, not after.
    """
    g = inst["hook"]["generate"]
    if g.get("person_generation", "allow_adult") == "allow_adult" and \
            any(w in body.lower() for w in MINOR_WORDS):
        return ("⚠ the video prompt describes a child but personGeneration is "
                "'allow_adult'. Veo will most likely refuse the request, or return the "
                "shot with the child silently absent. If it does, generate the reveal "
                "beat as a still instead and cut it onto the end of the Veo take — the "
                "image model already holds that face.")
    return ""


def _post(url: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        raise SystemExit(f"Veo error {ex.code}: {ex.read().decode()[:900]}")


def veo_gemini(inst: dict, frame: Path | None, out: Path, prompt: str, dur: int) -> None:
    """Veo through the Gemini API — a plain API key on a billed project, no OAuth.

    The simpler of the two routes and the one worth trying first: no gcloud, no service
    account, no project id in the URL. The key must be a paid-tier AI Studio key; a free
    or express key gets 403 PERMISSION_DENIED on this method, which is exactly what
    AGENT_GCP_KEY does today.
    """
    key = os.environ.get("VEO_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("set VEO_API_KEY (a paid-tier AI Studio key) in .env")
    model = inst["hook"]["generate"]["model"]
    host = "https://generativelanguage.googleapis.com/v1beta"

    def call(url: str, body: dict | None):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode() if body is not None else None,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            raise SystemExit(f"Veo error {ex.code}: {ex.read().decode()[:900]}")

    p = veo_payload(inst, frame, prompt, dur)
    op = call(f"{host}/models/{model}:predictLongRunning", p)
    name = op.get("name")
    if not name:
        raise SystemExit(f"no operation name: {json.dumps(op)[:400]}")
    print(f"  operation {name.rsplit('/', 1)[-1]}")

    for i in range(POLL_MAX):
        time.sleep(POLL_S)
        st = call(f"{host}/{name}", None)
        if st.get("error"):
            raise SystemExit(f"Veo failed: {json.dumps(st['error'])[:600]}")
        if st.get("done"):
            samples = (((st.get("response") or {}).get("generateVideoResponse") or {})
                       .get("generatedSamples") or [])
            if not samples:
                raise SystemExit(f"done but no video — likely a safety refusal:\n"
                                 f"{json.dumps(st.get('response', {}))[:800]}")
            uri = samples[0]["video"]["uri"]
            req = urllib.request.Request(uri, headers={"x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=300) as r:
                out.write_bytes(r.read())
            print(f"  ✓ {out}  ({out.stat().st_size / 1e6:.1f} MB)")
            return
        print(f"  … still rendering ({(i + 1) * POLL_S}s)", flush=True)
    raise SystemExit("timed out waiting for Veo")


def veo(inst: dict, frame: Path | None, out: Path, prompt: str, dur: int) -> None:
    """Veo on Vertex, through the google-genai SDK rather than hand-rolled REST.

    The SDK owns the request shape, the operation polling and the response parsing, all
    of which were guesses when this file only spoke HTTP. It also surfaces
    `rai_media_filtered_reasons`, which is the field that says *why* a take came back
    empty — the difference between "the prompt is bad" and "the policy said no".
    """
    from google import genai
    from google.genai import types

    g = inst["hook"]["generate"]
    loc = os.environ.get("GCP_LOCATION", "us-central1")

    # Three ways in, cheapest first. An express API key issued from inside a project is
    # NOT the same thing as a project-less express key — it carries the project with it,
    # which is what the old AGENT_GCP_KEY could not do.
    if key := os.environ.get("VERTEX_API_KEY"):
        client = genai.Client(vertexai=True, api_key=key.strip())
    else:
        project = os.environ.get("GCP_PROJECT")
        if not project:
            raise SystemExit("set VERTEX_API_KEY, or GCP_PROJECT plus a token/ADC")
        creds = None
        if tok := os.environ.get("GCP_ACCESS_TOKEN"):
            # Lets a pasted `gcloud auth print-access-token` work with no gcloud
            # installed locally. Tokens last about an hour, which is several takes.
            from google.oauth2.credentials import Credentials
            creds = Credentials(token=tok.strip())
        client = genai.Client(vertexai=True, project=project, location=loc,
                              credentials=creds)

    secs = next((d for d in VEO_DURATIONS if d >= dur / 1000), VEO_DURATIONS[-1])
    n = int(g.get("samples", 1))
    src = types.GenerateVideosSource(
        prompt=prompt,
        image=(types.Image(image_bytes=frame.read_bytes(), mime_type="image/png")
               if frame else None),
    )
    cfg = types.GenerateVideosConfig(
        aspect_ratio=g.get("aspect", "9:16"),
        number_of_videos=n,
        duration_seconds=secs,
        person_generation=g.get("person_generation", "allow_adult"),
        generate_audio=bool(g.get("generate_audio", True)),
        resolution=g.get("resolution", "720p"),
    )
    print(f"  veo {g['model']} · {secs}s · {cfg.aspect_ratio} · {cfg.resolution} · "
          f"{n} take(s) · person_generation={cfg.person_generation}")

    try:
        op = client.models.generate_videos(model=g["model"], source=src, config=cfg)
    except Exception as ex:
        # Established empirically, twice, with two different keys — including one issued
        # from inside the target project: an API key CANNOT call Veo. Express mode serves
        # generateContent and nothing long-running, and predictLongRunning has no project
        # to bill against. This is not a quota or a flag; it is the wrong kind of
        # credential. Say so, rather than making the next person read a stack trace.
        if "RESOURCE_PROJECT_INVALID" in str(ex):
            raise SystemExit(
                "Veo refused the credential: RESOURCE_PROJECT_INVALID.\n"
                "  An API key — even one created inside the project — cannot call Veo.\n"
                "  predictLongRunning needs OAuth. Any of:\n"
                "    • GCP_ACCESS_TOKEN=$(gcloud auth print-access-token)   ← Cloud Shell works\n"
                "    • gcloud auth application-default login\n"
                "    • GOOGLE_APPLICATION_CREDENTIALS=<service-account.json>\n"
                "  Unset VERTEX_API_KEY when you do; it takes priority in this script.")
        raise
    waited = 0
    while not op.done:
        time.sleep(POLL_S)
        waited += POLL_S
        op = client.operations.get(op)
        print(f"  … rendering ({waited}s)", flush=True)
        if waited > POLL_S * POLL_MAX:
            raise SystemExit("timed out waiting for Veo")

    resp = op.result
    if resp is None:
        raise SystemExit(f"operation finished with no result: {op}")
    if getattr(resp, "rai_media_filtered_count", 0):
        raise SystemExit(
            f"Veo filtered {resp.rai_media_filtered_count} take(s):\n  "
            + "\n  ".join(resp.rai_media_filtered_reasons or ["no reason given"])
            + "\n\nIf this is the reveal beat, generate it as a still and cut it onto the "
              "end of the take — the image model already holds that face.")
    vids = resp.generated_videos or []
    if not vids:
        raise SystemExit("no videos returned and nothing reported as filtered")

    # Every take is kept. Picking one is a human judgement and overwriting the others to
    # save disk is how you end up re-rendering at $2 a go to get back what you had.
    takes = []
    for i, gv in enumerate(vids, start=1):
        p = out.with_suffix(f".take{i}.mp4")
        if gv.video.video_bytes:
            p.write_bytes(gv.video.video_bytes)
        elif gv.video.uri:
            client.files.download(file=gv.video, path=str(p))
        takes.append(p)
        print(f"  ✓ {p.name}  ({p.stat().st_size / 1e6:.1f} MB)")

    if out.exists() or out.is_symlink():
        out.unlink()
    os.link(takes[0], out)
    print(f"  → {out.name} is take 1; relink another with "
          f"`ln -f {takes[0].parent}/{out.stem}.takeN.mp4 {out}`")


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edit", help="video id, video dir, or path to edit.yaml")
    ap.add_argument("--first-frame-only", action="store_true",
                    help="draw the opening frame and stop — no Veo, no project key needed")
    ap.add_argument("--no-first-frame", action="store_true",
                    help="text-only Veo prompt (the face will drift)")
    ap.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    ap.add_argument("--force", action="store_true", help="ignore the first-frame cache")
    ap.add_argument("--reroll", type=int, default=0)
    args = ap.parse_args()

    edit_path = rtf.find_edit(args.edit)
    # .resolve() matters: load_dotenv walks `parents`, and a relative path has almost
    # none — it would look in ./ and stop, never reaching the repo root where .env is.
    gs.load_dotenv(edit_path.parent.resolve())
    inst = rtf.load(edit_path)
    if "generate" not in inst.get("hook", {}):
        raise SystemExit(f"{edit_path.name}: hook.generate is absent — this hook is filmed, "
                         f"not generated")

    resolved, errs = rtf.resolve(edit_path)
    if errs:
        print("FORMAT VALIDATION FAILED:", file=sys.stderr)
        for e in errs:
            print("  •", e, file=sys.stderr)
        return 1

    desc = rtf.load(edit_path.parent / f"{inst['hook']['clip']}.clip.yaml")
    out = (edit_path.parent / desc["media"]).resolve()

    cad = resolved.get("cadence")
    if not cad:
        raise SystemExit(f"{edit_path.name}: no cadence — a generated hook needs one, or "
                         f"its beat timings are just numbers somebody typed")
    grid = cad["grid"]
    cad_doc = rtf.load(rtf.find_root(edit_path.parent) / "lib" / "cadences"
                       / f"{cad['id']}.cadence.yaml")
    chars = load_chars(edit_path, resolved.get("cast") or [])
    fmt = rtf.load(rtf.find_root(edit_path.parent) / "lib" / "formats"
                   / f"{inst['format']}.format.yaml")
    pr = rtf.merge(fmt.get("prompt", {}), inst.get("prompt", {}))
    g = inst["hook"]["generate"]

    # ---- one segment per cadence slot ---------------------------------
    # Claude scripts; the generators render single shots. A slot that needs a PERFORMANCE
    # (someone speaking, a body doing something) is worth a video model. A static
    # composition is an image — asking a video model for one buys nothing but drift and
    # a bill. A camera move is neither; it is applied at assembly.
    dur_ms = grid[-1]["t_out"]
    print(f"  cadence '{cad['id']}' — {len(grid)} slots, {dur_ms}ms at "
          f"{cad['beat_ms']:.0f}ms/beat")

    # The start image for any performance take. Identity is carried by the image model,
    # which holds these faces off ten reference photographs; the video model is never
    # asked to invent a face, only to move one.
    frame = None
    if not args.no_first_frame and not args.dry_run:
        frame, _ff = gen_still(edit_path, inst, g.get("first_frame_prompt"),
                               slot_cast(inst, chars, "first_frame_characters"),
                               g.get("first_frame_identity") or pr.get("identity", ""),
                               "frame0", args.reroll, args.force)
        print(f"    frame0     start image  {frame.name}")
        if args.first_frame_only:
            return 0

    segs, spent_veo = [], 0
    for slot in grid:
        spec = next((x for x in cad_doc.get("slots", []) if x["role"] == slot["role"]), {})
        motion = spec.get("motion", "static")
        ms = slot["t_out"] - slot["t_in"]
        beat = next((b for b in desc["beats"] if b["t_in"] == slot["t_in"]), None)
        if not beat:
            raise SystemExit(f"no descriptor beat at {slot['t_in']}ms for '{slot['role']}'")

        if motion == "performance":
            take = out.with_suffix(f".{slot['role']}.mp4")
            prompt = compose_prompt(inst, desc, [slot])
            if args.dry_run:
                print(f"    {slot['role']:<10} VEO   {ms}ms\n      {prompt[:150]}…")
            else:
                {"gemini": veo_gemini, "vertex": veo}[g.get("provider", "gemini")](
                    inst, frame, take, prompt, ms)
                spent_veo += 1
            segs.append((take, ms, None))
        elif args.dry_run:
            # A dry run that quietly spends $0.20 on stills and then prints "nothing
            # spent" is worse than no dry run at all.
            print(f"    {slot['role']:<10} STILL {ms}ms  cast={beat.get('subjects', [])}")
            segs.append((Path(f"<{slot['role']}.png>"), ms, spec.get("move")))
            continue
        else:
            cont, setting = desc.get("continuity", {}), desc.get("setting", {})
            body = (f"{beat['frame']} The scene is a {setting.get('place','')} at "
                    f"{setting.get('time_of_day','')}. Lighting: {cont.get('light','')}. "
                    f"Camera: {cont.get('lens_feel','')}.")
            if spec.get("obscured"):
                png, _ = gen_still(edit_path, inst, body, [], "", slot["role"],
                                   args.reroll, args.force,
                                   suffix=pr.get("obscured_suffix"))
            else:
                # Only name people the beat actually contains. A tableau of bowling shoes
                # does not need two faces described into it, and describing them is what
                # puts them there.
                cast = beat.get("subjects", [])
                png, _ = gen_still(edit_path, inst, body, cast,
                                   pr.get("identity", "") if cast else "",
                                   slot["role"], args.reroll, args.force,
                                   suffix=None if cast else pr.get("still_life_suffix"))
            segs.append((png, ms, spec.get("move")))
        print(f"    {slot['role']:<10} {motion:<12} {ms:>5}ms  "
              f"{'move=' + spec['move'] if spec.get('move') else ''}")

    if args.dry_run:
        print("\ndry run — nothing generated, nothing spent")
        return 0

    assemble(segs, out)
    desc["provenance"] = {
        "provider": g.get("provider"), "model": g["model"],
        "cadence": cad["id"],
        "segments": [{"role": sl["role"], "ms": sl["t_out"] - sl["t_in"],
                      "file": sg[0].name} for sl, sg in zip(grid, segs)],
        "dialogue": g.get("dialogue"),
    }
    (edit_path.parent / f"{inst['hook']['clip']}.clip.yaml").write_text(
        yaml.safe_dump(desc, sort_keys=False, width=100, allow_unicode=True))
    print("  now: python3 render_edit.py", edit_path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
