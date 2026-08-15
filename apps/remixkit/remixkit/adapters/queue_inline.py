"""In-process job runner — a queue's contract without a broker.

It is a real background thread, not a synchronous call dressed up as one: the request
that enqueues a kit returns immediately, exactly as it will when SQS is behind this
port. Handlers stay fast in dev for the same reason they must in prod (§2b rule 3), so
a route that accidentally blocks on generation is caught on a laptop.

Idempotency is enforced here rather than left to the caller, because it has to hold on
both adapters: the same `dedupe_key` while a job is in flight is a no-op. That mirrors
SQS + Batch redelivery (§2b rule 4).

What it does not survive is a process restart — in-flight work is lost. That is the
honest limit of running without a broker, and it is exactly what `queue_backend=sqs`
buys.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]


def _on_lambda() -> bool:
    """True when running inside AWS Lambda.

    Lambda freezes the whole execution environment the moment a handler returns, so a
    background thread stops mid-work and only resumes if another invocation happens to
    land on the same container. Combined with per-container `/tmp`, a kit started this
    way sits at `running` forever — which is exactly the failure BUILD-SPEC §2b rule 3
    exists to prevent, observed in the wild.
    """
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


class InlineQueue:
    name = "inline"

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        # Refuse rather than hang. A queued kit that never finishes gives the operator
        # nothing to act on; a refusal names the fix.
        self.can_execute = not _on_lambda()
        self.unavailable_reason = (
            "Generation cannot run on the inline queue inside Lambda — the execution "
            "environment freezes when a request returns, so the job would never finish. "
            "Set RK_QUEUE_BACKEND=sqs (with B2 storage) so AWS Batch runs it."
            if not self.can_execute
            else ""
        )

    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    def enqueue(self, job_type: str, payload: dict[str, Any], *, dedupe_key: str) -> str:
        handler = self._handlers.get(job_type)
        if handler is None:
            raise KeyError(f"No handler registered for job type {job_type!r}")

        with self._lock:
            if dedupe_key in self._inflight:
                log.info("job %s already in flight — no-op", dedupe_key)
                return dedupe_key
            self._inflight.add(dedupe_key)

        def _run() -> None:
            try:
                handler(payload)
            except Exception:
                # The job records its own failure on the kit document; this is the
                # last-resort log so a crash is never silent.
                log.exception("job %s (%s) failed", dedupe_key, job_type)
            finally:
                with self._lock:
                    self._inflight.discard(dedupe_key)

        threading.Thread(target=_run, name=f"job-{dedupe_key}", daemon=True).start()
        return dedupe_key
