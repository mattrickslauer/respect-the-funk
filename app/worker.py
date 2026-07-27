#!/usr/bin/env python3
"""The generator container's entrypoint — what AWS Batch runs.

Deliberately not a web process. infra/README's caveat #1 is the reason: BUILD-SPEC §4
calls Genblaze with `timeout=900`, which is *exactly* Lambda's 15-minute ceiling with
no headroom for cold start, provider retries, or the upload afterwards. It would fail
by silent timeout on the most expensive path in the system. Batch has no such limit and
still costs nothing at rest via `minvCpus: 0`.

Two modes:

    python worker.py --kit-id kit_abc123          # one kit, then exit (Batch job)
    python worker.py --poll                       # drain SQS until empty (long-running)

Both go through `KitService.run`, which is idempotent on the kit id — a redelivered
message costs nothing (§2b rule 4).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from remixkit.bootstrap import export_for_libs, load_secrets
from remixkit.deps import get_container
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
                run_one(payload["tenant_id"], payload["kit_id"])
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
    parser.add_argument("--tenant-id", help="Tenant (defaults to RK_DEFAULT_TENANT_ID)")
    parser.add_argument("--poll", action="store_true", help="Drain SQS until empty")
    args = parser.parse_args()

    tenant_id = args.tenant_id or get_settings().default_tenant_id

    if args.kit_id:
        return run_one(tenant_id, args.kit_id)
    if args.poll:
        return poll()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
