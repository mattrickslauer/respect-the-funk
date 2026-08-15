# Email-OTP sign-in, an account section, and the end of the React console

**Date:** 2026-08-15
**Status:** approved, in implementation
**Touches:** `apps/spindle/web/spindle/`, `apps/spindle/schema/`, `infra/terraform/spindle/`, and
the deletion of `platform/console/`

---

## What this changes, in one paragraph

Today the platform console has two credentials — a shared `PLATFORM_ADMIN_TOKEN` that
makes you an operator with no tenant, and a per-tenant token minted by `POST /claim`
against an unverified email address. After this, there is one: a six-digit code mailed
to an address you demonstrably control. The operator role goes away entirely, which
means every principal carries a real `tenant_id` and the two `PLATFORM_TENANT_SLUG`
fallbacks that existed only for the operator become unreachable and are deleted. The
console's left rail grows an account block at its foot, and a `/account` page behind it.
The React console at `platform/console` is removed.

## Why now

`accounts.py` has carried a written admission since it was built:

> **No email verification, and therefore no re-issue.** Nothing here proves the person
> typing an address owns it. […] When there is an email sender that is allowed to mail
> humans — there is not — the correct fix is a signed, expiring link, and it is a
> different function.

This is that function, arriving with the mail sender it was waiting for. Two things it
resolves that were previously bounded rather than fixed: a returning tenant who lost
their token could not get back in, and a second claim on a known address had to be
*refused* because serving one would have been account takeover with a form in front of
it. Both were correct decisions given no verification. Both stop being necessary once
the address is proven.

---

## §1 — The OTP challenge

### Migration `034_otp_challenge.sql`

```
otp_challenge (
  email       STRING PRIMARY KEY,
  code_hash   STRING NOT NULL,     -- sha256(email || code), 64 hex, CHECK'd
  expires_at  TIMESTAMPTZ NOT NULL,
  attempts    INT NOT NULL DEFAULT 0,
  sent_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

**One row per address, `email` as the primary key.** Requesting a second code replaces
the first rather than adding to it, so there is never a set of simultaneously-valid
codes for one address and no question about which of them a verification is checking.

**This is the second table in the schema whose leading column is not `tenant_id`, and
it is the same exception `account_by_token` takes.** `repo.py`'s standing rule is that
every query is scoped by tenant. A sign-in request arrives carrying an email address and
nothing else — no cookie, no tenant, frequently no account yet. Requiring `tenant_id` to
look up the challenge that *establishes* `tenant_id` is circular. Migration 033 made this
argument once for the credential lookup; 034 makes it for the step before it. Everything
downstream of authentication is scoped normally.

**`code_hash` is salted with the email.** `sha256(email || code)` rather than
`sha256(code)`, because there are only 10⁶ six-digit codes and an unsalted column would
be a rainbow table with 1,000,000 rows that anyone could precompute once and reuse
against every row forever. Binding the digest to the address makes a precomputed table
useless against any address it was not built for.

The fast-hash argument from 033 does **not** transfer and must not be assumed to. 033
uses SHA-256 on a 256-bit CSPRNG token, where a work factor buys nothing because there is
no dictionary. A six-digit code *is* a dictionary — the whole of it. What bounds guessing
here is not the hash, it is `attempts` and `expires_at`, and those are the controls that
have to be right.

### `spindle/otp.py`

Ported in shape from `app/remixkit/services/accounts.py`, which already solved this once.

| Knob | Value | Why |
|---|---|---|
| Code length | 6 digits | What autofill expects; `autocomplete="one-time-code"` |
| TTL | 10 minutes | Long enough to find the mail, short enough to bound guessing |
| Max attempts | 5 | 5 tries against 10⁶ with a 10-minute window |
| Resend floor | 30 seconds | Stops the form being a mail cannon aimed at a third party |

**`attempts` is counted in the database, not in the process.** The deployment is Lambda
against a cluster that scales to zero. An in-memory counter is reset by a cold start,
which means an attacker gets five guesses *per container* and can cause containers at
will. The counter has to live where the state actually is.

**Comparison is `hmac.compare_digest`,** and a wrong code says "that code is not right"
without saying how much of it was. Timing and message both.

**Codes come from `secrets.randbelow(1_000_000)`, formatted to six digits.** Not
`random`. And the code is never logged — with no reveal path (see §4) there is no
supported way to read a code other than receiving the mail.

---

## §2 — OTP replaces both existing credentials

`/signin` becomes two steps on one page: request a code, then enter it.

```
GET  /signin              the email form
POST /signin/code         mint, store hash, mail it   → renders the code form
POST /signin/verify       check it, sign in           → 303 to /
POST /signout             unchanged
```

### Verification autocreates the tenant

On a successful code, `otp.verify` resolves the account for that address, and **creates
the tenant, the account and the budget row in one transaction when there is not one** —
reusing `accounts.claim`'s existing transaction rather than writing a second one. First
sign-in *is* signup; there is no separate signup step and no `POST /claim`.

This is safe in a way `/claim` was not, and the difference is the whole point of the
work: `/claim` created an account for whoever typed an address, so a second claim on a
known address had to be refused to prevent takeover. Verification proves control of the
address first, so the refusal has nothing left to protect and is removed with it.

**What stays:** `accounts.MAX_CLAIMS_PER_HOUR` and `MAX_ACCOUNTS`. Those bound how much
database a stranger can cause to exist, and proving you own an address does not bound
that — anyone with a mail domain has unlimited addresses. The rolling-hour cap and the
absolute cap are unchanged and still counted in the database.

### The session, and why it is `account.token_hash`

Verification mints `secrets.token_urlsafe(32)`, writes its sha256 to
`account.token_hash`, and sets that token as the `rtf_session` cookie.

No new session table, no new signing secret, no second credential path. The resolver
`auth.principal_from_cookie` already takes — `accounts.account_for_token` — is exactly
the lookup this needs, and migration 033's UNIQUE `account_by_token` index is exactly the
constraint. **The JSON API's bearer-token path therefore keeps working with no change at
all**, because the session token and the account token are now the same object.

The honest cost, which 033 already wrote down: **one session per account.** Signing in on
a phone rewrites `token_hash` and signs the laptop out. 033 also wrote down the growth
path and it is additive — a second table `account_token (tenant_id, token_hash, label,
created_at, revoked_at)` with `plan` staying exactly where it is. Not built now.

### Sign-in persists

The cookie is set with a 90-day `max_age`, matching `CLAIM_COOKIE_SECONDS`, and
`httponly`/`secure`/`samesite=lax` exactly as `/signin` and `/claim` already set them.

The *reason* for 90 days changes even though the number does not, and the old reason
should not be left in the file to rot. `CLAIM_COOKIE_SECONDS` documents ninety days as
damage control: the raw token was shown once, was not recoverable, and could not be
reissued, so an expiring cookie meant "lose the account" rather than "sign in again".
That is no longer true — anyone can prove their address and get a fresh session in two
steps. Ninety days is now ordinary convenience, and the comment says so.

### Deleted

- `PLATFORM_ADMIN_TOKEN`, and the `admin_token` branch of `principal_from_cookie`
- `auth.OPERATOR_PLAN`
- `Principal.is_operator`
- `POST /claim` and `CLAIM_COOKIE_SECONDS`
- the operator-token field on `signin.html`

**Stated plainly, because the file being deleted argues the opposite:** `auth.py` holds
that the operator token "must keep working under every failure of everything else […]
because the operator is who fixes it. Reversing the order would put the recovery path
behind the thing being recovered." That is a correct argument and this change accepts its
cost knowingly. Sign-in now depends on the database *and* on SES. When either is down,
nobody enters the console, including whoever would repair it — recovery moves to the AWS
console and `psql`, which is where the credentials for both already live. This was the
explicit decision on 2026-08-15.

---

## §3 — Dropping the operator role

Every authenticated principal now carries a real `tenant_id`. Nobody sees across tenants.

**The blast radius is much smaller than a grep suggests.** `Operator` appears at ~70
annotation sites, but `require_operator` only ever checked `principal.authenticated` — it
was the *signed-in* gate wearing a role's name. No route branches on `is_operator`; it
appears only in `auth.py`, two tests, and docstrings.

- `require_operator` → `require_signed_in`, `Operator` → `SignedIn`, in `routes.py` and
  `api/deps.py`. Mechanical, and worth doing rather than leaving: a gate named for a role
  that no longer exists is exactly the drift this codebase keeps refusing.
- `routes._tenant_id`'s fallback to `SETTINGS.tenant_slug` — deleted. Its docstring says
  "Falling through to `SETTINGS.tenant_slug` is the **operator** path and nothing else."
  There is no operator path.
- `api/deps.current_tenant`'s same fallback, and its `NO_TENANT` refusal — deleted, for
  the same reason.
- `_tenant_id_for_write`'s `ensure_tenant(SETTINGS.tenant_slug, ...)` — deleted. The
  tenant now exists by the time any write can happen, because sign-in created it.
- `SETTINGS.tenant_slug` / `PLATFORM_TENANT_SLUG` — deleted.
- `routes._ctx`'s `principal.tenant_slug or SETTINGS.tenant_slug` — the right side goes.

---

## §4 — SES, wired for real

Mail is now the only way in, so `mail_configured == False` is a deployment nobody can
enter. `infra/terraform/spindle/main.tf:526` currently records the opposite decision:

> The Sender is deliberately NOT reachable from here: no `PLATFORM_MAIL_*` variables are
> set […] Wiring SES here is a decision to take on purpose.

This is that decision. The comment is **amended to record it, not deleted** — the
reasoning for the original position is still why the sender worker stays gated
separately.

**Terraform:** `aws_ses_domain_identity` plus its DKIM CNAME records, the three
`PLATFORM_MAIL_*` variables on the function, and `ses:SendEmail` added to the Lambda's
IAM policy.

**No reveal path, no dev code, no logging the code.** `otp.request_code` calls
`mail.load()`, which already raises `MailNotConfigured` naming all three missing
variables. The sign-in page renders that sentence. A deployment without mail refuses to
issue codes and says exactly which variables are unset — which is the loud failure this
project's standing rule asks for, rather than a second-best path that quietly works
differently.

**The SES sandbox is a real constraint and is not solvable in code.** A fresh SES account
may only send to *verified* addresses, and leaving the sandbox is a support request with
a turnaround measured in a day or so. Until it clears, sign-in works only for addresses
verified in the AWS console. `docs/runbooks/ses-sign-in-mail.md` covers the DNS
verification and the sandbox exit, because neither can be done from this repository.

---

## §5 — The account section

### In the rail

`.railfoot` already sits at the bottom of the left rail via `margin-top:auto`. It becomes
the account block: the signed-in email, the tenant, a plan chip, a **Settings** link to
`/account`, and **Sign out**.

Sign out is a gap being filled, not a nicety: `POST /signout` has existed the whole time
and the rail has never had a link to it. The foot currently carries the tenant slug, a
read-only indicator, and a link to the roster form.

The existing responsive block keeps every label — the file carries a pointed comment
about a deleted rule that once hid `.railfoot` and the scope switcher at 1180px, blanking
the only sign-out affordance and the only artist-scoping control on every laptop. The
account block inherits that treatment: it wraps, it does not disappear.

### `/account`

One route, one template. Not added to `demo.NAV` — it is reached from the rail foot,
which is where accounts live in every console anyone has used.

- **Email.** Read-only. Changing it means re-verifying the new address, which is a
  different flow and is not built; the page says that rather than offering a box that
  silently does nothing.
- **Tenant display name.** Editable.
- **Plan and usage.** The tier from `plans.TIERS`, today's spend from
  `spend.spent_today`, against the ceiling from `spend.tenant_ceiling`.
- **Upgrade.** Posts to the existing `POST /billing/checkout`. No new billing code; there
  is currently no billing *page* in the console at all, and this becomes it.
- **Sign out everywhere.** Rotates `token_hash`. Labelled honestly: with one session per
  account this is the same thing as signing out, and the page says so rather than
  implying a device list that does not exist.

---

## §6 — Removing the React console

**Deleted:** `platform/console/`, `spindle/console_assets.py`,
`tests/test_console_assets.py`, the mount in `main.py`, and the npm block in
`infra/terraform/spindle/build.sh` — which takes Node out of the deployment path entirely.

**Preservation.** `platform/console` is a subtree of `main`, not a branch, so its full
history stays recoverable from git after the deletion commit; a bundle would add nothing.
What is *not* in git is the uncommitted work in three files
(`components/primitives.tsx`, `styles/app.css`, `surfaces/Now.tsx`). That is written to
`/home/mattricks/rtf-react-console-wip-2026-08-15.patch` before deletion, following the
same keep-it-outside-the-repo practice as the genblaze bundle.

**Deliberately kept:**

- **`spindle/api/`.** Built for the React console, but independently tested,
  documented at `docs/reference/api-v1.md`, and the only way anything scripts this
  system. Deleting it is larger than what was asked for. Under §2 its auth is unchanged.
- **`app/remixkit/`.** A separate surface, not named in this request.

---

## Testing

- `otp.py` unit tests against a fake clock and a fake mailer: expiry, attempt exhaustion,
  the resend floor, replacement of an outstanding challenge, and that a wrong code's
  message does not vary with how wrong it was.
- Sign-in flow tests: unknown address creates a tenant exactly once; known address
  resolves the existing one; the cookie carries the 90-day `max_age` and all three flags.
- A test that `mail_configured == False` refuses to issue a code and names all three
  variables — the no-reveal guarantee, asserted rather than described.
- `tests/test_api_surface.py` already walks every API route asserting one of the two
  gates is in its dependency tree; it must keep passing across the rename, which is what
  makes the rename provably total.
- Baseline before any change: **687 passed, 270 skipped**. That is the number to hold.

## Known limitations, stated rather than discovered later

1. **One session per account.** Signing in anywhere signs out everywhere else.
2. **No recovery path independent of the database and SES.** §2 accepts this knowingly.
3. **SES sandbox** bounds who can sign in until production access is granted.
4. **No account deletion**, and no way to change the email on an account.
