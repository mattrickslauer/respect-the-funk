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
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]


class InlineQueue:
    name = "inline"

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

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
