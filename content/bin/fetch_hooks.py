#!/usr/bin/env python3
"""Mine YouTube captions for hook-shaped spoken lines, then cut the clip around one.

    .venv/bin/python bin/fetch_hooks.py dialogue

`fetch_library.py` builds silent b-roll. That is the wrong substrate for
[FORMAT-SPEC](../FORMAT-SPEC.md)'s `promise-payoff`, whose rule 1 is *one clip, one line,
one legal exit*: the hook has to carry a real `audio.quote`, declare `role.can_lead`, and
end on a `hard_cut`. A shot of a neon sign cannot do any of that.

**The caption track is the whole trick.** It says where the speech is, what it says, and —
the part that actually matters — *when it stops*. That last one is `t_out`: the only legal
place to cut a talking clip is the moment the sentence ends, and a transcript hands it to
you to the millisecond. It also means the search does not have to find the line. Captions
are a few kilobytes, so every candidate's entire transcript is mined for hook grammar and
only the ten seconds around the winner is ever downloaded.

**Hook grammar is FORMAT-SPEC's, not invented here.** Both worked instances are one of two
shapes — a statement of intent ("It's too cold, I'm going to California.") or a question
("Have you ever been bowling?") — and both are followed by a gap the drop can land in. So
utterances are scored on shape, length, and the silence after them, and the silence is
weighted heavily: a line with somebody talking over the cut has no legal exit.

Requires: ffmpeg, ffprobe, numpy, Pillow, pyyaml. Uses `bin/yt-dlp`.
"""

from __future__ import annotations

import argparse
import html
import math
import re
import sys
from pathlib import Path

import yaml

import rtf
import vertex
from fetch_library import (LOW_FPS, LOW_W, crop_x, decode_gray, features, axes,
                           motion_curve, probe_display_size, run, section_for, ytdlp)

GAP_SPLIT = 1.10         # silence that ends an utterance when punctuation does not
SENT_END = re.compile(r"[.?!][\"\u201d\u2019')]*$")
ABBREV = re.compile(r"^(mr|mrs|ms|dr|st|vs|etc|e\.g|i\.e|jr|sr|no)\.$", re.I)
CUE_RE = re.compile(r"(\d\d):(\d\d):(\d\d)\.(\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d)\.(\d\d\d)")
INLINE_RE = re.compile(r"<(\d\d):(\d\d):(\d\d)\.(\d\d\d)><c>([^<]*)</c>")
TAG_RE = re.compile(r"<[^>]*>")

# The two shapes the format actually pays off, plus their near neighbours.
PROMISE = [
    (r"\bhave you ever\b", 3.0), (r"\bdid you know\b", 2.2), (r"\bwhat if\b", 2.2),
    (r"\bi'?m (going|gonna|about) to\b", 3.0), (r"\bi'?ve never\b", 2.4),
    (r"\bi (never|always)\b", 1.8), (r"\bnobody (tells|talks|knows)\b", 2.6),
    (r"\bhere'?s the thing\b", 2.4), (r"\blet me tell you\b", 2.2),
    (r"\bthe (worst|best|craziest|weirdest|strangest)\b", 2.0),
    (r"\byou know what\b", 1.8), (r"\bi have to tell you\b", 2.8),
    (r"\bso basically\b", 1.4), (r"\bturns out\b", 1.8),
]
# Lines that are about the video rather than about anything.
JUNK = re.compile(
    r"\b(subscribe|patreon|sponsor|promo code|link in (the )?(bio|description)|"
    r"comment below|like and|smash that|channel|merch|giveaway|discount|"
    r"click the|check out my|follow me on)\b", re.I)
FILLER = re.compile(r"^(um|uh|so|and|but|like|yeah|okay|ok|right|well)\W*$", re.I)


# ---------------------------------------------------------------- captions


def secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(text: str) -> list[tuple[float, str]]:
    """(start_seconds, word) for every word, de-rolled.

    YouTube's auto-captions are a rolling window, and the rolling happens *inside* a cue:

        00:00:04.560 --> 00:00:07.190
        So, I uh I figured it out why hot dogs        <- the previous cue, restated
        come<00:00:04.720><c> in</c><00:00:04.960><c> packages</c>   <- the only new words

    So the bare lines are the repeat and the inline-timestamped line is the payload, whose
    own head word ("come") is new and untimed. Parsing line-by-line re-emits every repeat
    with the wrong timestamp, which is how a quote ends up saying a sentence twice. Cues
    are therefore parsed as blocks, and a cue with no inline timestamps is only trusted
    when its text has not already been seen — which keeps manually-authored subtitle
    tracks, where that is the only form there is, working unchanged.
    """
    words: list[tuple[float, str]] = []
    tail = ""                       # recently emitted text, for repeat detection

    # Split on a strictly empty line, not on `\n\s*\n`: YouTube writes a line containing
    # a single space as the first body line of a cue, and a whitespace-tolerant split
    # tears that cue in half — losing its header, and with it the opening sentence.
    # Captions are XML-ish, so entities arrive raw: "&gt;&gt; What is your opinion" is a
    # speaker-change marker, not something anybody said, and it would land verbatim in
    # audio.quote — the one field the whole format treats as the spoken line.
    text = html.unescape(text.replace("\r\n", "\n").replace("\r", "\n"))
    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        cue = next((CUE_RE.search(ln) for ln in lines if CUE_RE.search(ln)), None)
        if not cue:
            continue
        g = cue.groups()
        start, end = secs(*g[:4]), secs(*g[4:])
        body = [ln for ln in lines if not CUE_RE.search(ln)]
        inline_lines = [ln for ln in body if INLINE_RE.search(ln)]

        if inline_lines:
            for ln in inline_lines:
                head = TAG_RE.sub("", ln.split("<", 1)[0]).strip()
                if head:
                    for i, w in enumerate(head.split()):
                        words.append((start + i * 0.001, w))
                    tail = (tail + " " + head)[-400:]
                for h, mm, s, ms, w in INLINE_RE.findall(ln):
                    w = w.strip()
                    if w:
                        words.append((secs(h, mm, s, ms), w))
                        tail = (tail + " " + w)[-400:]
            continue

        clean = " ".join(TAG_RE.sub("", ln).strip() for ln in body).strip()
        if not clean or (end - start) < 0.05 or clean in tail:
            continue                # a spacer cue, or the rolling repeat on its own
        for i, w in enumerate(clean.split()):
            words.append((start + i * 0.001, w))
        tail = (tail + " " + clean)[-400:]

    words.sort(key=lambda x: x[0])
    return words


SPEAKER_MARK = re.compile(r"^\s*(?:>>+|-{1,2}|\[[^\]]*\]|\(\s*[^)]*\s*\)|\d{1,3})\s+")


def clean_line(text: str) -> str:
    """Strip caption furniture that nobody said.

    Speaker-change markers (">>"), bracketed sound cues ("[MUSIC]") and the stray line
    numbers some tracks carry are transcription apparatus, not speech — and `audio.quote`
    is supposed to be the line as spoken.
    """
    prev = None
    while prev != text:
        prev = text
        text = SPEAKER_MARK.sub("", text)
    return " ".join(text.split()).strip()


def utterances(words: list[tuple[float, str]]) -> list[dict]:
    """Group words into utterances, split on silence.

    Word end is the next word's start — auto-captions give no explicit end — so the last
    word of each group gets a nominal length rather than a measured one.
    """
    if not words:
        return []
    out, cur = [], [words[0]]
    for (t_prev, w_prev), nxt in zip(words, words[1:]):
        # Sentences first, silence second. Splitting purely on pauses chops a line into
        # "So, I uh I figured it" / "out" / "why hot dogs come in packages of 10 and",
        # and a fragment cannot make a promise — the hook has to be a whole sentence.
        ends_sentence = bool(SENT_END.search(w_prev)) and not ABBREV.match(w_prev)
        if ends_sentence or (nxt[0] - t_prev) > GAP_SPLIT:
            out.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    out.append(cur)

    res = []
    NOMINAL = 0.35          # how long the last word of a group is assumed to run
    for i, grp in enumerate(out):
        start, last = grp[0][0], grp[-1][0]
        if i + 1 < len(out):
            end = min(last + NOMINAL + 0.6, out[i + 1][0][0])
        else:
            end = last + NOMINAL
        end = max(end, start + 0.4)
        res.append({
            "text": clean_line(" ".join(w for _, w in grp)),
            "start": start,
            "end": end,
            "n": len(grp),
        })
    # The first and last utterances have no measured neighbour. Handing them a large
    # nominal gap is not neutral — the gap term is the heaviest in `score()`, so a free
    # 5.0s made the *last line of every transcript* the winner almost every time,
    # regardless of whether anything followed it. Unmeasured has to mean unrewarded.
    UNMEASURED = 0.6
    for i, u in enumerate(res):
        u["gap_before"] = u["start"] - res[i - 1]["end"] if i else UNMEASURED
        u["gap_after"] = res[i + 1]["start"] - u["end"] if i + 1 < len(res) else UNMEASURED
    return res


def score(u: dict) -> float:
    """How much this line behaves like a promise that a drop can cut off."""
    t = u["text"].strip()
    n = u["n"]
    if n < 3 or n > 16 or JUNK.search(t) or FILLER.match(t):
        return -math.inf
    if len(t) < 12:
        return -math.inf

    s = 0.0
    low = " " + t.lower() + " "
    for pat, w in PROMISE:
        if re.search(pat, low):
            s += w
            break
    if t.rstrip().endswith("?") or re.match(
            r"^(what|why|how|when|where|who|do|does|did|have|has|are|is|can|could|would)\b",
            t.lower()):
        s += 2.0

    # The gap after the line is the cut. Without one there is no legal exit, whatever
    # the words are.
    s += 2.2 * min(u["gap_after"], 1.6)
    s += 0.8 * min(u["gap_before"], 1.0)
    s -= 0.9 * abs((u["end"] - u["start"]) - 2.2)
    return s


# ---------------------------------------------------------------- fetching


RANK_PROMPT = """You are choosing the HOOK for a short vertical music video.

The format is "promise-payoff": one clip makes a promise in ONE spoken line, the music
drop cuts the line off, and a carousel of images pays the promise back in an inverted
world. Two hooks that worked:

    "It's too cold, I'm going to California."   (a statement of intent)
    "Have you ever been bowling?"               (a question asked straight to camera)

A line is a good hook when, on its own and with no context whatever, it makes a viewer
want to know what happens next, and when an image sequence could visibly answer it.

A line is a BAD hook when it is: a fragment; a discourse marker ("where were we", "so
anyway", "oh I remember"); a reference to something already said; an answer rather than a
setup; a list item; or anything that needs the previous sentence to make sense.

Below are candidate lines transcribed from one video. Choose the single best hook, or
choose none if every line is bad — refusing is the correct answer far more often than
not, and a weak hook wastes the whole video.

Reply as JSON: {"best": <index or -1>, "why": "<one short sentence>"}

LINES:
"""


def rank_lines(cands: list[dict]) -> tuple[int | None, str]:
    """Ask a model to pick the hook among the mechanically shortlisted lines.

    The regex scorer is good at throwing out junk and bad at telling a promise from a
    sentence that merely looks like one: "where were we oh I remember" scored 3.88 purely
    because it opens with an interrogative. Whether a line makes a viewer want the next
    shot is a judgement, so it is asked as one — over a shortlist, so the model is picking
    rather than reading a transcript.
    """
    import json
    import urllib.request
    if not vertex.available():
        return None, "no model available"
    listing = "\n".join(f"{i}: {c['text']}" for i, c in enumerate(cands))
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": RANK_PROMPT + listing}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }).encode()
    try:
        req = urllib.request.Request(vertex.endpoint("gemini-2.5-flash"), data=body,
                                     headers=vertex.headers())
        with urllib.request.urlopen(req, timeout=90) as r:
            txt = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
        out = json.loads(txt)
        i = int(out.get("best", -1))
        if 0 <= i < len(cands):
            return i, str(out.get("why", ""))[:160]
        return None, str(out.get("why", "model rejected every line"))[:160]
    except Exception as exc:                                    # noqa: BLE001
        return None, f"rank failed: {type(exc).__name__}"


def get_captions(yt: str, url: str, work: Path, sid: str) -> str | None:
    base = work / f"{sid}.subs"
    for f in work.glob(f"{sid}.subs*"):
        f.unlink(missing_ok=True)
    run([yt, "--no-warnings", "--skip-download", "--write-auto-subs", "--write-subs",
         "--sub-langs", "en.*", "--sub-format", "vtt", "-o", str(base), url])
    hits = sorted(work.glob(f"{sid}.subs*.vtt"))
    if not hits:
        return None
    return hits[0].read_text(errors="ignore")


def best_line(yt: str, cand: dict, work: Path, sid: str, cap: dict) -> dict | None:
    vtt = get_captions(yt, cand["url"], work, sid)
    if not vtt:
        return None
    us = utterances(parse_vtt(vtt))
    if not us:
        return None
    scored = [(score(u), u) for u in us]
    scored = [(s, u) for s, u in scored if s > -math.inf and s >= cap["min_score"]]
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])

    # The regex shortlists; the model chooses. Either alone is worse: the model should not
    # be handed a whole transcript, and the regex cannot tell a promise from a fragment
    # that merely starts with a question word.
    short = [u for _, u in scored[:8]]
    if cap.get("rank", True) and len(short) > 1:
        i, why = rank_lines(short)
        if i is not None:
            u = short[i]
            return {"score": round(score(u), 2), "why": why, **u}
        if why and not why.startswith("no model"):
            return None                 # the model looked and said none of these work
    s, u = scored[0]
    return {"score": round(s, 2), "why": "mechanical pick", **u}


def plan_cut(u: dict, cap: dict) -> dict:
    """Turn a line into a hook window that satisfies the format's timing rules.

    `t_out` is the line's end plus a tail, because the pre-roll swell has to start after
    the dialogue stops (rule 5) and still fit inside the hook. Where 2 bars will not fit,
    the recommendation drops to 1 rather than moving the cut — the cut belongs to the
    line, not to the arrangement.
    """
    lead = cap["lead_in_ms"] / 1000.0
    tail = cap["tail_ms"] / 1000.0
    t_in = max(0.0, u["start"] - lead)
    line_end_rel = (u["end"] - t_in)
    dur = line_end_rel + tail

    lo, hi = cap["min_hook_ms"] / 1000.0, cap["max_hook_ms"] / 1000.0
    if dur < lo:
        dur = lo
    if dur > hi:
        dur = max(lo, min(hi, line_end_rel + 1.2))

    beat_ms, bars = 480.0, 2
    while bars >= 1 and (dur * 1000 - bars * 4 * beat_ms) < line_end_rel * 1000:
        bars -= 1
    return {"t_in_s": round(t_in, 3), "dur_s": round(dur, 3),
            "line_start_ms": int(round((u["start"] - t_in) * 1000)),
            "line_end_ms": int(round(line_end_rel * 1000)),
            "pre_roll_bars": max(1, bars)}


def normalise(src: Path, out: Path, s: float, dur: float, x: int, w: int, h: int) -> None:
    """Keep the audio. It is the entire reason this clip exists."""
    want = int(round(h * 9 / 16))
    crop = f"crop={min(want, w)}:{h}:{x}:0," if want < w else ""
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.3f}", "-i", str(src),
         "-t", f"{dur:.3f}",
         "-vf", f"{crop}scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,fps=30,setsar=1,format=yuv420p",
         "-c:v", "libx264", "-crf", "19", "-preset", "veryfast",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", str(out)], check=False)


def mean_db(path: Path, ss: float, t: float) -> float | None:
    """Mean volume of a window, via ffmpeg's volumedetect."""
    r = run(["ffmpeg", "-hide_banner", "-ss", f"{ss:.3f}", "-i", str(path),
             "-t", f"{t:.3f}", "-af", "volumedetect", "-f", "null", "-"])
    for line in r.stderr.decode(errors="ignore").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
    return None


def measure_tail(out: Path, plan: dict) -> dict:
    """Is the tail actually quieter than the line, or did the caption gap lie?

    A caption gap says nothing about sound — it says nobody typed a word there, and a
    music bed or continued off-mic speech reads as a gap to the transcript and as noise
    to the viewer. This measures it instead of assuming, and records the number rather
    than gating on it: a hook over a music bed is fine, a hook with somebody still
    talking through the drop is not, and the difference is visible here.
    """
    line_s = plan["line_end_ms"] / 1000.0
    tail_s = plan["dur_s"] - line_s
    if tail_s < 0.3:
        return {}
    a, b = mean_db(out, 0.0, line_s), mean_db(out, line_s, tail_s)
    if a is None or b is None:
        return {}
    return {"line_db": round(a, 1), "tail_db": round(b, 1),
            "tail_quieter_db": round(a - b, 1)}


def sidecar(sid: str, out: Path, meta: dict, plan: dict, u: dict, f: dict,
            wants: str, query: str) -> dict:
    dur_ms = int(round(plan["dur_s"] * 1000))
    return {
        "schema": "rtf.clip/v1",
        "id": sid,
        "media": out.name,
        "title": wants or sid,
        "logline": u["text"][:180],
        "source_meta": {"duration_ms": dur_ms, "width": 1080, "height": 1920,
                        "fps": 30, "aspect": "9:16"},
        "rights": {
            "source": "youtube",
            "owner": meta.get("channel"),
            # CLIP-SPEC has both fields and this tool cannot fill either honestly: a
            # stranger in a found clip has released nothing, and nothing here can tell a
            # 17-year-old from a 19-year-old. Recorded as unknown rather than omitted,
            # because an absent field reads as "no minors" to anyone skimming.
            "people_release": False,
            "minors_in_frame": "unknown",
            "notes": ("third-party source, proof-of-concept hook library. Identifiable "
                      "people may appear and have not consented; check before publishing "
                      "or before holding a likeness invariant in a payoff."),
        },
        "provenance": {"query": query, "url": meta.get("url"),
                       "video_id": meta.get("id"), "video_title": meta.get("title"),
                       "channel": meta.get("channel"),
                       "section_start_s": plan["t_in_s"],
                       "caption_score": u["score"],
                       "chosen_because": u.get("why"),
                       "gap_after_s": round(u["gap_after"], 3),
                       **(u.get("_audio") or {})},
        "audio": {
            "has_speech": True,
            "quote": u["text"],
            "language": "en",
            "diegetic": True,
            "music_bed_ok": True,
            "keep_audio_on_cut": True,
        },
        "beats": [
            {"t_in": 0, "t_out": plan["line_end_ms"], "shot": "static",
             "framing": "medium", "frame": "the speaker delivers the line",
             "means": "the promise is made"},
            {"t_in": plan["line_end_ms"], "t_out": dur_ms, "shot": "static",
             "framing": "medium", "frame": "the line lands and nobody says anything",
             "means": "the pause the drop cuts off"},
        ],
        "cut_points": [
            {"t": 0, "kind": "in", "note": "lead-in before the line"},
            {"t": plan["line_end_ms"], "kind": "beat_change",
             "note": "the line ends; the pre-roll swell starts after this"},
            {"t": dur_ms, "kind": "hard_cut",
             "note": "the drop lands here — FORMAT-SPEC rule 1"},
        ],
        "role": {"primary": "cold_open", "can_lead": True, "can_follow": False,
                 "min_useful_ms": max(1500, plan["line_end_ms"])},
        "hook_recommended": {"t_in": 0, "t_out": dur_ms,
                             "pre_roll_bars": plan["pre_roll_bars"]},
        "features": f,
        "axes": axes(f),
    }


def fetch_one(yt: str, src: dict, cap: dict, outdir: Path, work: Path,
              rejected: set) -> str:
    sid = src["id"]
    out = outdir / f"{sid}.mp4"
    if out.exists():
        return f"  {sid:<18} skip (exists)"

    from fetch_library import resolve_query
    cands = [c for c in resolve_query(yt, src) if c["id"] not in rejected]
    if not cands:
        return f"  {sid:<18} FAIL no search results"

    for cand in cands[:5]:
        u = best_line(yt, cand, work, sid, cap)
        if not u:
            continue
        plan = plan_cut(u, cap)
        # Only now does anything big get downloaded, and only the seconds around the line.
        start = max(0.0, plan["t_in_s"] - 1.0)
        run([yt, "--no-warnings", "--no-playlist", "--force-overwrites",
             "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
             "--download-sections", f"*{start:.1f}-{start + plan['dur_s'] + 3:.1f}",
             "--max-filesize", "120M", "-o", str((work / sid).with_suffix(".%(ext)s")),
             cand["url"]])
        raw = next((h for h in sorted(work.glob(sid + ".*"))
                    if h.suffix in {".mp4", ".mkv", ".webm"}), None)
        if not raw or raw.stat().st_size < 50_000:
            for f_ in work.glob(sid + ".*"):
                f_.unlink(missing_ok=True)
            continue
        try:
            w, h = probe_display_size(raw)
            lh = max(2, int(round(LOW_W * h / w / 2)) * 2)
            frames = decode_gray(raw, LOW_W, lh, LOW_FPS)
            curve = motion_curve(frames)
            s_rel = max(0.0, plan["t_in_s"] - start)
            x = crop_x(frames, s_rel, plan["dur_s"], w, h)
            feat = features(raw, frames, s_rel, plan["dur_s"], curve)
            normalise(raw, out, s_rel, plan["dur_s"], x, w, h)
            if not out.exists():
                continue
            u["_audio"] = measure_tail(out, plan)
            doc = sidecar(sid, out, cand, plan, u, feat, src.get("wants", ""),
                          src.get("query", ""))
            (outdir / f"{sid}.clip.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
        except Exception as exc:                                    # noqa: BLE001
            return f"  {sid:<18} FAIL {type(exc).__name__}: {exc}"
        finally:
            for f_ in work.glob(sid + ".*"):
                f_.unlink(missing_ok=True)
        tq = (u.get("_audio") or {}).get("tail_quieter_db")
        return (f"  {sid:<18} {plan['dur_s']:4.1f}s  score {u['score']:5.2f}  "
                f"bars {plan['pre_roll_bars']}  "
                f"tail{'' if tq is None else f'{tq:+.1f}dB'}  \"{u['text'][:46]}\"")
    return f"  {sid:<18} FAIL no line scored above {cap['min_score']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--out")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-rank", action="store_true",
                    help="skip the model pick; take the top mechanical score")
    args = ap.parse_args()

    p = Path(args.brief)
    if not p.is_file():
        p = rtf.find_root(Path.cwd()) / "lib" / "briefs" / f"{args.brief}.brief.yaml"
    if not p.is_file():
        sys.exit(f"no brief at {p}")
    brief = yaml.safe_load(p.read_text())
    root = rtf.find_root(p)
    vertex.load_dotenv(Path.cwd())

    outdir = Path(args.out) if args.out else root / "lib" / "hooks" / brief["id"]
    outdir.mkdir(parents=True, exist_ok=True)
    work = root / ".cache" / "hooks"
    work.mkdir(parents=True, exist_ok=True)

    cap = {"lead_in_ms": 400, "tail_ms": 2600, "min_hook_ms": 4500,
           "max_hook_ms": 7500, "min_score": 2.0, "rank": not args.no_rank}
    cap.update(brief.get("capture") or {})

    sources = brief["sources"]
    if args.only:
        sources = [s for s in sources if s["id"] in set(args.only)]
    if args.limit:
        sources = sources[: args.limit]

    rej_path = outdir / "rejected.yaml"
    rejected = set(yaml.safe_load(rej_path.read_text()) or []) if rej_path.exists() else set()

    yt = ytdlp()
    print(f"{brief['id']}: {len(sources)} sources -> {outdir}")
    fails = 0
    for src in sources:
        line = fetch_one(yt, src, cap, outdir, work, rejected)
        if "FAIL" in line:
            fails += 1
        print(line, flush=True)
    print(f"\nhooks: {len(list(outdir.glob('*.mp4')))} clips, {fails} failed this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
