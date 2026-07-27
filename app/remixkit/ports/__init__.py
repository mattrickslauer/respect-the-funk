"""Ports — the seams.

Every one of these is a `typing.Protocol`, so adapters satisfy them structurally and
nothing in `services/` imports a concrete implementation. That is what makes the
swap-in list in `deps.py` a one-line change per axis:

    storage   local dir  → Backblaze B2
    generator mock       → Genblaze + GMI Cloud / ElevenLabs
    queue     inline     → SQS + Batch
    auth      anonymous  → OIDC / Clerk

None of the four requires touching a service or a route.
"""

from remixkit.ports.generator import GenerationRequest, GenerationResult, Generator
from remixkit.ports.queue import JobQueue
from remixkit.ports.repository import DocumentRepository
from remixkit.ports.storage import Storage

__all__ = [
    "DocumentRepository",
    "GenerationRequest",
    "GenerationResult",
    "Generator",
    "JobQueue",
    "Storage",
]
