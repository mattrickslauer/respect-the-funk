"""Discogs genre classification, as a container-image Lambda.

    {"bucket": "...", "key": "..."}  ->  {"styles": [...], "parent": "...", ...}

## Why this is a separate function rather than part of the worker

`essentia-tensorflow` pulls TensorFlow, which is ~1.5 GB unpacked and is available only
for CPython ≤3.11. Putting that in `requirements-worker.txt` would make every worker —
including one draining `embed_document` — carry TensorFlow to do arithmetic on a
1024-dimension vector. Here it is one image, invoked only when there is a master to
listen to, and scaled to zero the rest of the time.

## Why a Lambda rather than a spot instance

The workload is seconds of CPU per track, sporadically, triggered by an upload. A spot
instance idles between tracks, and the submission's production-readiness claim — "scales
to zero, `$0` idle" — is measured and true today. Standing up an always-on box to
classify a handful of tracks would contradict a claim made on camera and add interruption
handling for a workload that never needed it. `docs/2026-08-10-masters-and-
classification.md` §3b is the long version.

## The preprocessing is Essentia's, and that is the whole point

An earlier attempt computed the mel spectrogram in numpy and fed it to the effnet ONNX
export — no TensorFlow, no container, testable on a laptop. It **failed reference-track
validation**: Metallica came back `Electronic---Hardcore`, Coltrane
`Electronic---Speedcore`, Marley `Electronic---Electro`. Everything collapsed to
Electronic, which made Daft Punk landing on House a coincidence rather than a pass.

That is the signature of a front end that does not match what the network was trained
on, and the model emits confident labels regardless. Essentia's own
`TensorflowPredictEffnetDiscogs` is used here because it is the preprocessing the
weights were trained against — `unit_tri` band normalisation, window convention,
magnitude-versus-power and framing all have to agree, and tuning them by trial against
four tracks would produce something that passes four tests and is still wrong.

`tests/validate.py` runs six reference tracks spanning six parent genres on every image
build. A genre classifier without them is unfalsifiable, which is exactly what the numpy
version was until it was checked.
"""

from __future__ import annotations

import json
import os
import tempfile

import numpy as np

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/models")
EMBEDDING_GRAPH = f"{MODEL_DIR}/discogs-effnet-bs64-1.pb"
HEAD_GRAPH = f"{MODEL_DIR}/genre_discogs400-discogs-effnet-1.pb"
HEAD_META = f"{MODEL_DIR}/genre_discogs400-discogs-effnet-1.json"

#: Node names taken from the model's own metadata JSON rather than from a tutorial —
#: `schema.inputs[0].name` and `schema.outputs[0].name`. A wrong name here fails at graph
#: load with a message about a missing operation, which is at least loud.
HEAD_INPUT = "serving_default_model_Placeholder"
HEAD_OUTPUT = "PartitionedCall:0"
EMBEDDING_OUTPUT = "PartitionedCall:1"

#: EffNet-Discogs was trained on 16 kHz mono. This is not a quality knob — feeding it
#: 44.1 kHz produces a spectrogram whose bands mean something different, which is a
#: quieter version of the same mistake the numpy front end made.
SAMPLE_RATE = 16000

#: Below this, the winning *style* is not reported and only its parent genre is. Dr. Dre
#: "Still D.R.E." is the case that motivated it: the model puts it in Hip Hop correctly
#: and then picks `Horrorcore` at 0.215, which is confidently wrong about the sub-genre
#: while being right about the genre. Reporting the parent alone is the true subset of
#: what it knows, and `SCOPE-RESET §2a` rule 1 says an inference is recorded at the
#: confidence it actually has.
STYLE_FLOOR = 0.30

#: Below this, nothing is reported at all. A 400-way sigmoid whose best answer is under
#: this has not recognised the record, and emitting the argmax anyway is how a
#: classifier launders noise into a label.
GENRE_FLOOR = 0.10

#: How many styles to return. The caller writes one row, so this is what an operator
#: sees when they ask why a track was called what it was called.
TOP_N = 5

_models: dict[str, object] = {}


def _load() -> tuple:
    """Graphs and labels, loaded once per container and reused across invocations.

    A cold start pays ~10 s for TensorFlow's import and the graph load. Keeping them in
    module state means a burst of uploads pays that once rather than per track, which is
    the difference between this being usable from the console and not.
    """
    if "head" not in _models:
        from essentia.standard import TensorflowPredict2D, TensorflowPredictEffnetDiscogs

        _models["embedding"] = TensorflowPredictEffnetDiscogs(
            graphFilename=EMBEDDING_GRAPH, output=EMBEDDING_OUTPUT)
        _models["head"] = TensorflowPredict2D(
            graphFilename=HEAD_GRAPH, input=HEAD_INPUT, output=HEAD_OUTPUT)
        _models["classes"] = json.load(open(HEAD_META))["classes"]
    return _models["embedding"], _models["head"], _models["classes"]


def classify_file(path: str) -> dict:
    """Everything the model says about one audio file, with the floors applied.

    Predictions are averaged over the track's patches rather than taken from the loudest
    or the first. A three-minute record is many overlapping windows and any single one
    can be an intro, a breakdown, or silence; the mean is the claim about the record.
    """
    from essentia.standard import MonoLoader

    embedding, head, classes = _load()
    audio = MonoLoader(filename=path, sampleRate=SAMPLE_RATE, resampleQuality=4)()
    if len(audio) < SAMPLE_RATE * 3:
        raise ValueError(
            f"{len(audio) / SAMPLE_RATE:.1f}s of audio is below the 3s the patch "
            f"window needs — there is nothing here to classify")

    predictions = head(embedding(audio)).mean(axis=0)
    order = np.argsort(predictions)[::-1][:TOP_N]

    ranked = [{"label": classes[int(i)], "p": round(float(predictions[int(i)]), 4)}
              for i in order]
    best = ranked[0]
    parent = best["label"].split("---")[0]

    # Three tiers, and the middle one is the interesting one: the model is often right
    # about the genre and wrong about the style within it, so those are reported
    # separately rather than as one answer that is partly wrong.
    if best["p"] < GENRE_FLOOR:
        confident = "none"
    elif best["p"] < STYLE_FLOOR:
        confident = "parent"
    else:
        confident = "style"

    return {
        "styles": ranked,
        "parent": parent if confident != "none" else "",
        "style": best["label"] if confident == "style" else "",
        "confidence": best["p"],
        "confident": confident,
        "model": "genre_discogs400-discogs-effnet-1",
        "sample_rate": SAMPLE_RATE,
    }


def handler(event: dict, context: object = None) -> dict:
    """Fetch the object named in the event, classify it, return the labels.

    The object is fetched with this function's own IAM role rather than through a
    presigned URL handed in by the caller. Both work; this one was chosen because a URL
    minted before the worker queued the invoke can expire while the classifier is cold-
    starting, and an expired signature surfaces as a 403 on the download rather than as
    anything that names the real cause.
    """
    import boto3

    bucket = event.get("bucket", "")
    key = event.get("key", "")
    if not bucket or not key:
        return {"error": "the event named no bucket and key to classify"}

    suffix = os.path.splitext(key)[1] or ".audio"
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            path = handle.name
        # /tmp is the only writable path in Lambda and defaults to 512MB. The Terraform
        # raises `ephemeral_storage` to 2048 for exactly this download.
        boto3.client("s3").download_file(bucket, key, path)
        return classify_file(path)
    except Exception as exc:  # noqa: BLE001 - the message is the product
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
