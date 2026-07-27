"""SQS dispatch — the deployed path.

The consumer is not here. A message lands on the queue, and AWS Batch on Fargate Spot
picks it up and runs the generator container (`worker.py`). That split is deliberate
and is the one place infra/README says the obvious GCP→AWS mapping is actively wrong:
BUILD-SPEC §4 calls Genblaze with `timeout=900`, which is *exactly* Lambda's ceiling
with no headroom for cold start, provider retries, or the upload afterwards. Batch has
no such limit and still costs nothing at rest via `minvCpus: 0`.

`dedupe_key` is sent as `MessageDeduplicationId` for FIFO queues and echoed into the
body for standard ones, so the worker can enforce idempotency either way.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class SQSQueue:
    name = "sqs"

    def __init__(self, queue_url: str, *, region: str = "us-east-1") -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise RuntimeError(
                "queue_backend=sqs needs the 'queue' extra: pip install 'remixkit[queue]'"
            ) from exc
        if not queue_url:
            raise RuntimeError("queue_backend=sqs requires RK_SQS_QUEUE_URL")
        self._client = boto3.client("sqs", region_name=region)
        self._queue_url = queue_url
        self._is_fifo = queue_url.endswith(".fifo")

    def enqueue(self, job_type: str, payload: dict[str, Any], *, dedupe_key: str) -> str:
        body = json.dumps({"job_type": job_type, "dedupe_key": dedupe_key, "payload": payload})
        kwargs: dict[str, Any] = {"QueueUrl": self._queue_url, "MessageBody": body}
        if self._is_fifo:
            kwargs["MessageDeduplicationId"] = dedupe_key
            # One group per tenant: kits for different labels stay independently
            # ordered rather than serialising behind each other.
            kwargs["MessageGroupId"] = str(payload.get("tenant_id", "default"))
        response = self._client.send_message(**kwargs)
        log.info("enqueued %s (%s) → %s", job_type, dedupe_key, response.get("MessageId"))
        return dedupe_key
