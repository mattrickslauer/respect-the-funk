"""Authentication — deliberately absent, deliberately shaped.

There is **no authentication today**. That is the current instruction, and this package
does not pretend otherwise: `AnonymousAuth` hands every caller the same principal and
never rejects anyone.

What this package does buy is that adding auth later is one file plus one line in
`deps.py`, rather than a refactor. It costs almost nothing now because the two things
that are genuinely expensive to retrofit are already threaded through:

1. **Every request already resolves a `Principal`.** Routes depend on
   `deps.current_principal`, not on a global. Swapping the provider changes who that
   principal is, not how any handler reads it.
2. **Every document, object key, and job payload already carries `tenant_id`,** taken
   from that principal. BUILD-SPEC §2b rule 6 calls multi-tenancy "near-impossible to
   retrofit"; this is the cheap half of it, paid now.

When auth is wanted, write an `AuthProvider` (`oidc.py`, `clerk.py`, …), return a real
principal from the token, and point `deps.py` at it. No service, route, template, or
storage key changes shape.
"""

from remixkit.auth.anonymous import AnonymousAuth
from remixkit.auth.provider import AuthError, AuthProvider, Principal

__all__ = ["AnonymousAuth", "AuthError", "AuthProvider", "Principal"]
