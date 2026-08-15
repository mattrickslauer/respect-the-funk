"""Job dispatch.

BUILD-SPEC §2b rule 3: everything long-running is a queued job, never a request. Kit
generation is minutes; an HTTP handler must not be holding it. The inline adapter
honours the same contract on a background thread so local dev has no broker, and the
SQS adapter is a drop-in for the deployed path.
"""

from __future__ import annotations

from typing import Any, Protocol


class JobQueue(Protocol):
    name: str

    def enqueue(self, job_type: str, payload: dict[str, Any], *, dedupe_key: str) -> str:
        """Dispatch. `dedupe_key` is the idempotency key — the same key twice is one job."""
