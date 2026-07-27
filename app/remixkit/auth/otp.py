"""Email-OTP authentication — the provider `auth/__init__.py` said would arrive.

It is exactly the shape that file predicted: one class implementing `AuthProvider`, one
line in `deps._build_auth`, and no route, service, template, or storage key changes. The
principal it returns differs from `AnonymousAuth`'s in three ways — `authenticated` is
true, `subject` is the operator's email instead of the string `"anonymous"`, and
`scopes` is populated — and every consumer downstream was already written against those
fields rather than against the assumption that nobody is logged in.

Two places a session can arrive:

  * the `rk_session` cookie, which is how the console works — set httpOnly at sign-in,
    so console JavaScript cannot read it and an XSS cannot exfiltrate it;
  * `Authorization: Bearer <token>`, which is how a script works.

The cookie is checked second so an explicit Bearer header always wins. A script that
passes a token and also happens to carry a browser cookie means the token.
"""

from __future__ import annotations

from remixkit.auth.provider import AuthError, AuthProvider, Principal
from remixkit.services.accounts import AccountService


class OtpAuth(AuthProvider):
    name = "otp"

    def __init__(self, accounts: AccountService, *, cookie_name: str = "rk_session") -> None:
        self._accounts = accounts
        self._cookie_name = cookie_name

    async def authenticate(self, *, authorization: str | None, cookies: dict[str, str]) -> Principal:
        token = _bearer(authorization) or cookies.get(self._cookie_name, "")
        if not token:
            raise AuthError("Sign in to use the console.")

        claims = self._accounts.read_session(token)
        if claims is None:
            # Malformed, forged, expired, or an account since removed from the
            # allowlist. The caller's response to all four is identical, so they are
            # not distinguished here — telling an unauthenticated caller *why* their
            # token failed is free information about the signing key.
            raise AuthError("Your session has expired. Sign in again.")

        return Principal(
            tenant_id=str(claims["tid"]),
            subject=str(claims["sub"]),
            display_name=str(claims.get("name") or claims["sub"]),
            scopes=frozenset(claims.get("scopes") or ()),
            authenticated=True,
        )


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()
