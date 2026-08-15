#!/usr/bin/env python3
"""The generator container's entrypoint — what AWS Batch runs.

Deliberately not a web process. infra/README's caveat #1 is the reason: BUILD-SPEC §4
calls Genblaze with `timeout=900`, which is *exactly* Lambda's 15-minute ceiling with
no headroom for cold start, provider retries, or the upload afterwards. It would fail
by silent timeout on the most expensive path in the system. Batch has no such limit and
still costs nothing at rest via `minvCpus: 0`.

Three modes:

    python worker.py --kit-id kit_abc123               # one kit, then exit (Batch job)
    python worker.py --song-id sng_abc123              # measure one master, then exit
    python worker.py --transcribe-song-id sng_abc123   # hear one master's lyric, then exit
    python worker.py --poll                            # drain SQS until empty (long-running)

Both jobs are idempotent on their document id — a redelivered message costs nothing
(§2b rule 4). Analysis belongs here for the same reason generation does: decoding a master
and fitting a beat comb over it is a minute of CPU on a long file, which is not work an
HTTP request holds. It is also why the analysis extra is installed in the worker image and
need not be in the web one — see `RK_ANALYSIS_BACKEND`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from remixkit.bootstrap import export_for_libs, load_secrets
from remixkit.deps import get_container
from remixkit.services.analysis import JOB_TYPE as ANALYSIS_JOB_TYPE
from remixkit.services.transcription import JOB_TYPE as TRANSCRIPTION_JOB_TYPE
from remixkit.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("remixkit.worker")


def run_one(tenant_id: str, kit_id: str) -> int:
    container = get_container()
    kit = container.kits.run(tenant_id, kit_id)
    log.info(
        "kit %s → %s (%d assets, %d¢, manifest_verified=%s)",
        kit.id,
        kit.status.value,
        len(kit.assets),
        kit.total_cost_cents,
        kit.manifest_verified,
    )
    if kit.error:
        log.error("kit %s error: %s", kit.id, kit.error)
    # Non-zero on failure so Batch records the attempt as failed and retries per the
    # job definition rather than reporting a green run that produced nothing.
    return 0 if kit.status.value == "ready" else 1


def analyse_one(tenant_id: str, song_id: str) -> int:
    container = get_container()
    if not container.analysis.available:
        # Loud and immediate. A worker image built without the audio extra would otherwise
        # mark every song `failed` at a rate of one per message, and the reason would only
        # be visible on the song document.
        log.error("this worker cannot analyse audio: %s", container.analysis.unavailable_reason)
        return 2
    song = container.analysis.run(tenant_id, song_id)
    analysis = song.analysis
    log.info(
        "song %s → %s (%d sections, %s BPM)",
        song.id,
        analysis.status.value if analysis else "unknown",
        len(song.sections),
        song.bpm,
    )
    if analysis and analysis.error:
        log.error("song %s error: %s", song.id, analysis.error)
    return 0 if analysis and analysis.ran else 1


def transcribe_one(tenant_id: str, song_id: str) -> int:
    container = get_container()
    if not container.transcription.available:
        # Loud and immediate, exactly as `analyse_one` is: a worker image built without a
        # credentialed transcriber would otherwise mark every song `failed` at a rate of
        # one per message, with the reason visible only on the song document.
        log.error(
            "this worker cannot transcribe audio: %s",
            container.transcription.unavailable_reason,
        )
        return 2
    song = container.transcription.run(tenant_id, song_id)
    lyrics = song.lyrics
    log.info(
        "song %s → %s (%d lyric lines, language %s)",
        song.id,
        lyrics.status.value if lyrics else "unknown",
        len(lyrics.lines) if lyrics else 0,
        (lyrics.language if lyrics else None) or "undetected",
    )
    if lyrics and lyrics.error:
        log.error("song %s error: %s", song.id, lyrics.error)
    return 0 if lyrics and lyrics.ran else 1


def _handle(job_type: str, payload: dict) -> int:
    """One message → the service that owns it.

    The job type is read from the body rather than inferred from which keys are present:
    a payload-shape guess is the kind of dispatch that works until two jobs share a field —
    which is now literally true, since analysis and transcription carry the same
    `song_id`. The `song_id` fallback below stays for messages enqueued before this job
    type existed, and means analysis, because that is all it could have meant.
    """
    if job_type == TRANSCRIPTION_JOB_TYPE:
        return transcribe_one(payload["tenant_id"], payload["song_id"])
    if job_type == ANALYSIS_JOB_TYPE or "song_id" in payload:
        return analyse_one(payload["tenant_id"], payload["song_id"])
    return run_one(payload["tenant_id"], payload["kit_id"])


def poll(max_messages: int = 10) -> int:
    """Drain SQS. Delete only after the kit reaches a terminal state.

    A message that is not deleted becomes visible again after the queue's visibility
    timeout, which is the retry — so a crash mid-generation is a retry rather than a
    lost kit. The DLQ catches anything that fails repeatedly.
    """
    import boto3

    settings = get_settings()
    if not settings.sqs_queue_url:
        log.error("--poll needs RK_SQS_QUEUE_URL")
        return 2

    sqs = boto3.client("sqs", region_name=settings.aws_region)
    drained = 0
    while True:
        response = sqs.receive_message(
            QueueUrl=settings.sqs_queue_url,
            MaxNumberOfMessages=min(10, max_messages),
            WaitTimeSeconds=10,
        )
        messages = response.get("Messages", [])
        if not messages:
            log.info("queue empty — drained %d message(s)", drained)
            return 0

        for message in messages:
            try:
                body = json.loads(message["Body"])
                payload = body.get("payload", body)
                _handle(body.get("job_type", ""), payload)
            except Exception:
                log.exception("message failed — leaving it for redelivery/DLQ")
                continue
            sqs.delete_message(
                QueueUrl=settings.sqs_queue_url, ReceiptHandle=message["ReceiptHandle"]
            )
            drained += 1


def main() -> int:
    # Same two-step as create_app: the path to the secrets is itself a setting, and on
    # a laptop it comes from app/.env. See remixkit/bootstrap.py.
    if load_secrets(get_settings().ssm_path, profile=get_settings().aws_profile):
        get_settings.cache_clear()
    export_for_libs(get_settings())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit-id", help="Run one kit and exit")
    parser.add_argument("--song-id", help="Measure one song's master and exit")
    parser.add_argument("--transcribe-song-id", help="Transcribe one song's master and exit")
    parser.add_argument("--tenant-id", help="Tenant (defaults to RK_DEFAULT_TENANT_ID)")
    parser.add_argument("--poll", action="store_true", help="Drain SQS until empty")
    args = parser.parse_args()

    tenant_id = args.tenant_id or get_settings().default_tenant_id

    if args.kit_id:
        return run_one(tenant_id, args.kit_id)
    if args.song_id:
        return analyse_one(tenant_id, args.song_id)
    if args.transcribe_song_id:
        return transcribe_one(tenant_id, args.transcribe_song_id)
    if args.poll:
        return poll()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
