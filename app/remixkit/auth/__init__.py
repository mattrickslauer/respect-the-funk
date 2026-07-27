"""Authentication — two providers, selected by `RK_AUTH_BACKEND`.

    none → AnonymousAuth   every caller is the default tenant, nobody is rejected
    otp  → OtpAuth         email a six-digit code, hand back a signed session

`none` is still the default and still a complete implementation rather than a stub: the
console runs with no credentials, no mail, and no login, which is what makes a laptop
checkout work. `RK_REQUIRE_AUTH` is the guard against shipping that shape by accident.

This package used to say authentication was "deliberately absent, deliberately shaped",
and that shape is why `otp.py` is sixty lines. The two things that are genuinely
expensive to retrofit had already been paid for:

1. **Every request already resolved a `Principal`.** Routes depend on
   `deps.current_principal`, not on a global. Swapping the provider changed who that
   principal is, not how any handler reads it.
2. **Every document, object key, and job payload already carried `tenant_id`,** taken
   from that principal. BUILD-SPEC §2b rule 6 calls multi-tenancy "near-impossible to
   retrofit"; that was the cheap half, paid early.

What OTP added on top of the seam: a `Mailer` port with a console fallback, an
`AccountService` that owns codes and sessions, a login page, and an `AuthError` handler
that redirects browsers while returning 401 to scripts. No service, no template that
existed before, and no storage key changed shape.
"""

from remixkit.auth.anonymous import AnonymousAuth
from remixkit.auth.otp import OtpAuth
from remixkit.auth.provider import AuthError, AuthProvider, Principal

__all__ = ["AnonymousAuth", "AuthError", "AuthProvider", "OtpAuth", "Principal"]
