"""No mail credentials — the code goes to the log instead.

This is the zero-credential path, matching `storage_local` and `generator mock`: the
whole sign-in flow runs end-to-end on a laptop with nothing configured, because the
developer can read the code out of the server output.

It reports `delivered=False` truthfully. The API and the console both surface that as
"emailed the code" versus "mail is not configured — check the server log", so nobody
sits waiting for a message that was never sent.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class ConsoleMailer:
    name = "console"

    def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> bool:
        log.warning(
            "MAIL NOT CONFIGURED — would have sent to %s\n  subject: %s\n%s", to, subject, text
        )
        return False
