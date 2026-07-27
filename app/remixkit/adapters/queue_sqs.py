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

    def __init__(
        self,
        queue_url: str,
        *,
        region: str = "us-east-1",
        batch_job_queue: str = "",
        batch_job_definition: str = "",
    ) -> None:
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

        # Something has to *start* the worker. A queue nobody consumes is a kit that
        # sits at `queued` forever — the same failure the inline-on-Lambda path had,
        # arrived at from the other direction.
        #
        # The alternative designs are worse here: an SQS event-source mapping only
        # triggers Lambda (which is precisely what cannot run a 15-minute generation),
        # and an EventBridge poller is a second always-on thing to operate. Submitting
        # a `--poll` job on enqueue keeps Batch at zero when idle and needs no extra
        # service.
        #
        # Submitting more jobs than necessary is harmless and deliberate: a duplicate
        # job finds an empty queue and exits in seconds, and `KitService.run` is
        # idempotent on the kit id anyway (§2b rule 4).
        self._batch_job_queue = batch_job_queue
        self._batch_job_definition = batch_job_definition
        self._batch = (
            boto3.client("batch", region_name=region)
            if batch_job_queue and batch_job_definition
            else None
        )

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
        self._wake_worker(dedupe_key)
        return dedupe_key

    def _wake_worker(self, dedupe_key: str) -> None:
        """Start a Batch job to drain the queue. Never fails the enqueue.

        The message is already durably on SQS at this point, so a failure to start the
        worker delays the kit — it does not lose it. Raising here would turn a
        recoverable scheduling problem into a failed request.
        """
        if self._batch is None:
            log.warning(
                "No Batch job queue configured — the message is on SQS but nothing will "
                "consume it. Set RK_BATCH_JOB_QUEUE and RK_BATCH_JOB_DEFINITION."
            )
            return
        try:
            job = self._batch.submit_job(
                jobName=f"kit-{dedupe_key.replace('_', '-')[:100]}",
                jobQueue=self._batch_job_queue,
                jobDefinition=self._batch_job_definition,
            )
            log.info("submitted Batch job %s for %s", job.get("jobId"), dedupe_key)
        except Exception:
            log.exception("could not start the Batch worker; the message stays queued")
