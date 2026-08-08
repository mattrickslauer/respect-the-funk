"""Who is asking, and what they may do.

The shape is lifted from `app/remixkit/auth/` — a Principal carrying `tenant_id`,
resolved on every request whether or not anyone is signed in. That app is frozen
during Backblaze judging, so this is a copy of the pattern rather than an import.

Two principals:

  * **anonymous** — gets the landing page, the sign-in form, and one public POST to
    ask for a demo call. Nothing else. An earlier build let anonymous readers browse
    the whole console so a hackathon judge would land on the product rather than a
    login box; that was reversed on purpose — the roster, the counterparty index and
    the campaign state are the label's own information, and judges are handed a token.
  * **admin** — sees the console and may write. A shared token in an httpOnly cookie.

The token is not OTP, and is not pretending to be. `app/remixkit/auth/otp.py` is
61 lines and is where this goes when there is more than one operator; until then a
shared secret is the honest amount of auth for a single-operator console.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

COOKIE_NAME = "rtf_session"


@dataclass(frozen=True)
class Principal:
    tenant_id: str | None
    subject: str
    authenticated: bool

    @property
    def may_write(self) -> bool:
        return self.authenticated


ANONYMOUS = Principal(tenant_id=None, subject="anonymous", authenticated=False)


def principal_from_cookie(token: str | None, admin_token: str) -> Principal:
    """Constant-time compare so the cookie cannot be brute-forced a byte at a time.

    An unset `admin_token` never authenticates anybody — otherwise a deployment
    that forgot to set it would accept an empty cookie as valid.
    """
    if not admin_token or not token:
        return ANONYMOUS
    if hmac.compare_digest(token, admin_token):
        return Principal(tenant_id=None, subject="operator", authenticated=True)
    return ANONYMOUS
