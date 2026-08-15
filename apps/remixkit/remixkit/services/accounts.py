"""Sign-in: email a code, check the code, hand back a session.

Passwordless on purpose. A password would mean storing a hash, building a reset flow
that emails a link, and owning a credential that can be reused elsewhere — all to
authenticate a handful of people who already control the mailbox that any reset would
go to. The mailbox is the credential; this skips the indirection.

Three decisions worth stating, because each has a defensible opposite:

**The allowlist is the user table.** `RK_ALLOWED_EMAILS` decides who exists. There is no
registration. The console administers a real label's roster and its intended population
is "the label", not "anyone with an email address".

**A refused address is told so.** This leaks whether an address is on the allowlist,
which the usual advice says to avoid. The usual advice is written for consumer signup,
where the user base is a secret and enumeration is the attack. Here the population is a
handful of known colleagues, membership is not confidential, and the real failure mode
is someone mistyping their own address and staring at "check your email" forever.

**Sessions are stateless.** A signed token with an expiry, not a row. That means no
revocation before expiry — the reason `session_ttl_s` is a week rather than a year. The
fix, if a token ever leaks, is to rotate `RK_SESSION_SECRET`, which invalidates every
session at once. That is a blunt instrument and an acceptable one at this size.

Nothing here imports FastAPI. The service is callable from a script or the worker.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from remixkit.domain.models import Account, OtpChallenge, utcnow
from remixkit.ports.mailer import Mailer
from remixkit.ports.repository import DocumentRepository
from remixkit.services.errors import ServiceError

log = logging.getLogger(__name__)

ACCOUNTS = "accounts"
CHALLENGES = "otp"

# What a signed-in operator may do. Written onto the account and into the session so
# `Principal.can()` has something real to check the day a service starts calling it.
FULL_SCOPES = (
    "artists:read",
    "artists:write",
    "identities:write",
    "songs:write",
    "kits:read",
    "kits:write",
    "kits:approve",
)


class AuthRefused(ServiceError):
    """Wrong code, expired code, address not on the allowlist.

    401 rather than 400: the caller may retry with a different credential, and the
    console's error component reads the status to decide whether to keep the form up.
    """

    status_code = 401


class TooManyAttempts(ServiceError):
    status_code = 429


@dataclass(frozen=True)
class CodeSent:
    """The outcome of asking for a code — including whether mail actually left."""

    email: str
    expires_at: datetime
    delivered: bool
    # Populated only when nothing was delivered AND this process is `env=dev`, so the
    # laptop flow is completable without a mail account.
    #
    # Two conditions rather than one because either alone is a door. Gating on delivery
    # only would hand out codes to anyone who could reach a production deployment that
    # someone had left on the console mailer — a misconfiguration that is otherwise
    # merely inconvenient becomes a full authentication bypass. Gating on `env` only
    # would echo codes in a dev process that *is* sending real mail. See
    # `AccountService(reveal_codes=...)`.
    dev_code: str | None = None


def account_id(email: str) -> str:
    """Derived, not random, so email → document is a `get` rather than a collection scan.

    Hashed rather than the slugified address because the address is the one PII field
    here and it should not also be an object key sitting in a bucket listing.
    """
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"acct_{digest[:16]}"


class AccountService:
    def __init__(
        self,
        repo: DocumentRepository,
        mailer: Mailer,
        *,
        tenant_id: str,
        allowlist: frozenset[str],
        session_secret: str,
        session_ttl_s: int = 7 * 24 * 3600,
        otp_length: int = 6,
        otp_ttl_s: int = 600,
        otp_max_attempts: int = 5,
        otp_resend_interval_s: int = 30,
        reveal_codes: bool = False,
        app_name: str = "RemixKit",
    ) -> None:
        self._repo = repo
        self._mailer = mailer
        self._tenant_id = tenant_id
        self._allowlist = allowlist
        self._secret = session_secret.encode()
        self._session_ttl_s = session_ttl_s
        self._otp_length = otp_length
        self._otp_ttl_s = otp_ttl_s
        self._otp_max_attempts = otp_max_attempts
        self._otp_resend_interval_s = otp_resend_interval_s
        self._reveal_codes = reveal_codes
        self._app_name = app_name

    # ------------------------------------------------------------------ allowlist
    def is_allowed(self, email: str) -> bool:
        return email.strip().lower() in self._allowlist

    # ------------------------------------------------------------------ step one
    def request_code(self, email: str) -> CodeSent:
        """Mint a code, store its hash, mail it. Replaces any outstanding challenge."""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise AuthRefused("That does not look like an email address.")
        if not self.is_allowed(email):
            raise AuthRefused(f"{email} is not permitted to sign in to this console.")

        doc_id = account_id(email)
        existing = self._repo.get(self._tenant_id, CHALLENGES, doc_id, OtpChallenge)
        if existing is not None and not existing.is_expired():
            age_s = (utcnow() - existing.created_at).total_seconds()
            if age_s < self._otp_resend_interval_s:
                raise TooManyAttempts(
                    f"A code was just sent. Wait {int(self._otp_resend_interval_s - age_s)}s "
                    "before requesting another."
                )

        code = self._new_code()
        expires_at = utcnow() + timedelta(seconds=self._otp_ttl_s)
        challenge = OtpChallenge(
            tenant_id=self._tenant_id,
            id=doc_id,
            email=email,
            code_hash=self._hash_code(email, code),
            expires_at=expires_at,
        )
        self._repo.put(self._tenant_id, CHALLENGES, doc_id, challenge)

        minutes = max(1, self._otp_ttl_s // 60)
        delivered = self._mailer.send(
            to=email,
            subject=f"{code} is your {self._app_name} sign-in code",
            text=_plain_body(code, minutes, self._app_name),
            html=_html_body(code, minutes, self._app_name),
        )
        if not delivered:
            log.warning("sign-in code for %s: %s (not emailed)", email, code)

        return CodeSent(
            email=email,
            expires_at=expires_at,
            delivered=delivered,
            dev_code=code if (not delivered and self._reveal_codes) else None,
        )

    # ------------------------------------------------------------------ step two
    def verify_code(self, email: str, code: str) -> tuple[Account, str]:
        """Check the code and return the account plus a signed session token.

        The challenge is consumed on success and on exhaustion, never left behind for a
        second attempt at the same code.
        """
        email = email.strip().lower()
        code = code.strip().replace(" ", "").replace("-", "")
        doc_id = account_id(email)

        challenge = self._repo.get(self._tenant_id, CHALLENGES, doc_id, OtpChallenge)
        if challenge is None:
            raise AuthRefused("No sign-in code outstanding. Request a new one.")
        if challenge.is_expired():
            self._repo.delete(self._tenant_id, CHALLENGES, doc_id)
            raise AuthRefused("That code has expired. Request a new one.")

        # Compared under HMAC, so both sides are fixed-length and the comparison leaks
        # nothing about how much of the code was right.
        if not hmac.compare_digest(challenge.code_hash, self._hash_code(email, code)):
            challenge.attempts += 1
            if challenge.attempts >= self._otp_max_attempts:
                self._repo.delete(self._tenant_id, CHALLENGES, doc_id)
                raise TooManyAttempts("Too many incorrect codes. Request a new one.")
            challenge.touch()
            self._repo.put(self._tenant_id, CHALLENGES, doc_id, challenge)
            remaining = self._otp_max_attempts - challenge.attempts
            raise AuthRefused(f"That code is not right. {remaining} attempt(s) left.")

        self._repo.delete(self._tenant_id, CHALLENGES, doc_id)

        # The allowlist is re-checked here, not just at request time: a code minted
        # before someone was removed must not still work afterwards.
        if not self.is_allowed(email):
            raise AuthRefused(f"{email} is not permitted to sign in to this console.")

        account = self._upsert(email)
        return account, self.mint_session(account)

    def _upsert(self, email: str) -> Account:
        doc_id = account_id(email)
        account = self._repo.get(self._tenant_id, ACCOUNTS, doc_id, Account)
        if account is None:
            account = Account(
                tenant_id=self._tenant_id,
                id=doc_id,
                email=email,
                display_name=email.split("@", 1)[0],
                scopes=list(FULL_SCOPES),
            )
            log.info("first sign-in for %s — account created", email)
        account.last_login_at = utcnow()
        account.touch()
        self._repo.put(self._tenant_id, ACCOUNTS, doc_id, account)
        return account

    def get_account(self, email: str) -> Account | None:
        return self._repo.get(self._tenant_id, ACCOUNTS, account_id(email), Account)

    # ------------------------------------------------------------------- sessions
    def mint_session(self, account: Account) -> str:
        payload = {
            "v": 1,
            "sub": account.email,
            "tid": account.tenant_id,
            "name": account.display_name or account.email,
            "scopes": sorted(account.scopes),
            "iat": int(utcnow().timestamp()),
            "exp": int(utcnow().timestamp()) + self._session_ttl_s,
        }
        body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{_b64encode(self._sign(body))}"

    def read_session(self, token: str) -> dict | None:
        """Validate signature and expiry. Returns the claims, or None for anything wrong.

        One return value for "malformed", "forged", and "expired" on purpose — the
        caller's only correct response to all three is to ask for a sign-in.
        """
        if not token or "." not in token:
            return None
        body, _, signature = token.rpartition(".")
        try:
            if not hmac.compare_digest(_b64decode(signature), self._sign(body)):
                return None
            claims = json.loads(_b64decode(body))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

        if not isinstance(claims, dict) or claims.get("v") != 1:
            return None
        if int(claims.get("exp", 0)) <= utcnow().timestamp():
            return None
        # Removing someone from the allowlist must end their session at the next
        # request, not at the token's expiry a week later.
        if not self.is_allowed(str(claims.get("sub", ""))):
            return None
        return claims

    # --------------------------------------------------------------------- crypto
    def _sign(self, body: str) -> bytes:
        return hmac.new(self._secret, body.encode(), hashlib.sha256).digest()

    def _hash_code(self, email: str, code: str) -> str:
        # Keyed by email as well as the secret, so a hash lifted from one challenge
        # cannot be replayed into another account's document.
        return hmac.new(self._secret, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()

    def _new_code(self) -> str:
        upper = 10**self._otp_length
        return str(secrets.randbelow(upper)).zfill(self._otp_length)


# -------------------------------------------------------------------------- helpers
def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _plain_body(code: str, minutes: int, app_name: str) -> str:
    return (
        f"Your {app_name} sign-in code is:\n\n"
        f"    {code}\n\n"
        f"It expires in {minutes} minutes and can be used once.\n"
        "If you did not ask to sign in, you can ignore this email — nobody can get in "
        "without the code.\n"
    )


def _html_body(code: str, minutes: int, app_name: str) -> str:
    # Inline styles and a table-free layout: this has to survive Gmail and Outlook,
    # neither of which is a browser. The code is also in the plain-text part, so a
    # client that strips all of this still shows something usable.
    return f"""\
<div style="font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
            background:#0d0d10;color:#e8e8ea;padding:32px;border-radius:12px;max-width:440px">
  <p style="margin:0 0 4px;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#8a8a94">
    {app_name}
  </p>
  <h1 style="margin:0 0 20px;font-size:20px;font-weight:600;color:#fff">Your sign-in code</h1>
  <p style="margin:0 0 20px;font-size:32px;font-weight:700;letter-spacing:.28em;
            font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#fff">{code}</p>
  <p style="margin:0 0 8px;font-size:14px;line-height:1.6;color:#b6b6be">
    It expires in {minutes} minutes and can be used once.
  </p>
  <p style="margin:0;font-size:13px;line-height:1.6;color:#8a8a94">
    If you did not ask to sign in, ignore this email — nobody can get in without the code.
  </p>
</div>"""
