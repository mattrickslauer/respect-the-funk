"""Proving that whoever typed an address can read mail at it.

This is the module `accounts.py` said would arrive. That file has carried the admission
since it was written:

> **No email verification, and therefore no re-issue.** Nothing here proves the person
> typing an address owns it. […] the correct fix is a signed, expiring link, and it is a
> different function.

This is that function, in its six-digit form, and what it buys is larger than a login
box. `POST /claim` had to *refuse* a second arrival at a known address, because handing
out a fresh credential to whoever typed an email is account takeover with a form in front
of it. That refusal was the entire mitigation, and it cost a returning tenant their
account whenever they lost the cookie. Verification removes the need for it: an address
that proves itself may sign in as often as it likes.

## What this module is and is not

It owns **the challenge** — mint a code, store its digest, mail it, check it, consume it.
It does not own the session, the account, or the tenant. `accounts.sign_in` does that,
and the split is deliberate: this file has no opinion about what being verified entitles
you to, and `accounts.py` has no opinion about how you proved it. The route calls them in
order.

## The two bounds, and which one is real

An attacker against a six-digit code has two routes and they need different answers.

**Online — POSTing guesses at the verify endpoint.** This is the one that matters, and it
is bounded by `MAX_ATTEMPTS` and `TTL`: five guesses against a million, inside ten
minutes. Both live in the database row, not in this process, and that is not an
implementation detail. The deployment is Lambda in front of a cluster that scales to
zero; an in-memory counter is reset by a cold start, so a counter in a process gives the
attacker five guesses *per container* and containers are free to cause. `accounts.py`
makes the identical argument about `MAX_CLAIMS_PER_HOUR` at greater length.

**Offline — cracking a leaked `otp_challenge` dump.** Bounded by the salt, not by the
hash. Migration 034's header has the full argument; the short version is that 033's
defence of a fast hash rests entirely on its input being 256 bits of CSPRNG output, a
six-digit code is a one-million-entry dictionary, and so the digest is
`sha256(email || code)` rather than `sha256(code)`. A slow KDF would raise the cost of
the offline attack and do nothing about the online one.

## Sending to strangers

`POST /signin/code` is unauthenticated and causes mail to be sent to an address supplied
by whoever called it. That is two exposures, not one:

  * **Hammering one inbox** — bounded by `RESEND_AFTER`, which `sent_at` on the replaced
    row enforces with no extra state.
  * **Cycling distinct addresses**, which defeats a per-address floor completely because
    every request is a different address. This is the one that mails strangers and grows
    the table, and it is bounded by `MAX_CODES_PER_HOUR`, counted in the database.

Per-IP limiting is deliberately absent. Behind a Function URL the client address is
rotated for a fraction of a cent, so it would stop an honest person retrying a typo and
nobody else, while reading like protection in a review. `accounts.py` reached the same
conclusion about `/claim` and says so at length.

## This mail is transactional, and must not carry the outreach footer

`mail.compliant_body` appends a postal address and a "reply STOP" opt-out, because
CAN-SPAM §7704 requires both **of commercial messages**. A sign-in code is transactional:
the recipient asked for it seconds ago, and there is nothing to unsubscribe from. Adding
the footer would invite a person to reply STOP to a login email and thereby set
`contact_route.state = 'opted_out'` on a party they may be in an active campaign with.
Nothing here calls `compliant_body`, and this paragraph is why.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

import psycopg

from rtf_platform import accounts, mail

#: Digits in a code. Six because that is what `autocomplete="one-time-code"` and every
#: phone's autofill expect; a different length silently loses that affordance.
CODE_DIGITS = 6

#: How long a code is good for. Long enough to go and find the message, short enough that
#: the five-guess bound is being spent against a moving target.
TTL = timedelta(minutes=10)

#: Guesses before the challenge closes. Five against one million, and the challenge is
#: *closed* rather than merely counted — see `verify_code`, which refuses the correct code
#: once this is reached. A counter that does not close anything is decoration.
MAX_ATTEMPTS = 5

#: The floor between two sends to one address.
RESEND_AFTER = timedelta(seconds=30)

#: Distinct addresses that may be mailed a code cluster-wide in one rolling hour.
#:
#: Counted over `sent_at`, and because there is one row per address, the count *is*
#: "distinct addresses mailed recently" — a resend to a known address replaces its row and
#: does not consume more of the budget. That is the right metric: resends to one inbox are
#: already bounded by `RESEND_AFTER`, and the thing this number exists to stop is a script
#: walking a list of strangers.
#:
#: The honest weakness, stated the way `accounts.MAX_CLAIMS_PER_HOUR` states its own:
#: **one abuser can lock out legitimate sign-ins for an hour.** A global bound trades that
#: for being un-rotatable, and on a deployment with a small budget it is the right way
#: round. The refusal says so rather than pretending to be an outage.
MAX_CODES_PER_HOUR = 60


class OtpRefused(RuntimeError):
    """A sign-in step the server declined, carrying a machine-readable reason.

    Modelled on `accounts.ClaimRefused` and `spend.SpendRefused`: the sentence is for a
    person and may be rewritten in any commit, `reason` is what a program branches on.

    Declared reasons: `email_missing`, `email_malformed`, `email_too_long`, `too_soon`,
    `rate_limited`, `no_challenge`, `expired`, `wrong_code`, `attempts_exhausted`.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# -------------------------------------------------------------------- the code

def mint_code() -> str:
    """A fresh six-digit code, zero-padded. `secrets`, never `random`.

    `random` is a Mersenne Twister seeded from the clock, and observing a few outputs
    reveals every output it will ever produce. For a value that is somebody's entire
    session that is not a theoretical distinction.

    The zero-padding is not cosmetic. `randbelow(1_000_000)` returns an integer, and one
    draw in ten is under 100000 — rendered with a bare `str()` those become five-character
    codes that the form's `pattern` rejects and the person cannot successfully type. It
    would look like a one-in-ten flaky login.
    """
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(email: str, code: str) -> str:
    """What goes in the database: lowercase hex `sha256(email || NUL || code)`.

    **Salted with the address, and migration 034's header argues why at length.** The one
    sentence version: six digits is a dictionary of a million entries, so an unsalted
    column is a rainbow table that anyone with read access precomputes once and reuses
    against every row forever.

    The separator is a NUL byte rather than nothing, so that `("ab@x.com", "1")` and
    `("ab@x.com1", "")` cannot collide onto one digest. Neither is reachable through the
    current callers — `normalise_email` would refuse both — but a concatenation with no
    separator is the kind of thing that becomes reachable two refactors later, and the
    byte is free.
    """
    return hashlib.sha256(
        email.encode("utf-8") + b"\x00" + code.encode("utf-8")).hexdigest()


def _normalise(raw: str) -> str:
    """`accounts.normalise_email`, with its refusal translated into this module's.

    The translation exists so that a caller of `otp` catches one exception type. It is
    not a fallback and swallows nothing: the message, which is the part a person reads, is
    carried through unchanged, and so is `reason`, which is the part a program branches
    on. What changes is the class name, and `ClaimRefused` on a sign-in page would name a
    flow — claiming — that no longer exists.

    Normalising through `accounts` rather than reimplementing it is load-bearing:
    `otp_challenge.email` and `account.email` are compared to each other on every sign-in,
    so a second definition of "the same address" would be a bug the first time the two
    drifted.
    """
    try:
        return accounts.normalise_email(raw)
    except accounts.ClaimRefused as exc:
        raise OtpRefused(str(exc), reason=exc.reason) from exc


# ------------------------------------------------------------------ the bounds

def codes_sent_since(conn: psycopg.Connection, *, hours: int = 1) -> int:
    """Addresses mailed a code in the last `hours`. The rolling-hour bound.

    Rolling rather than a fixed bucket, for the reason `accounts.claims_since` gives: a
    fixed hourly bucket lets twice the cap through across a boundary, and bounding the
    endpoint is the entire point of the number.
    """
    with conn.cursor() as cur:
        # The window is a `timedelta` psycopg adapts to an INTERVAL, not an integer
        # formatted into the SQL. Safe today, and the habit that writes the injection next
        # year; no statement in this package is built by formatting.
        cur.execute(
            "SELECT count(*) AS n FROM otp_challenge WHERE sent_at > now() - %s",
            (timedelta(hours=hours),))
        return int((cur.fetchone() or {}).get("n", 0))


# ------------------------------------------------------------- issuing a code

_SUBJECT = "{code} is your Respect the Funk sign-in code"

_BODY = """\
{code} is your sign-in code for Respect the Funk.

It expires in {minutes} minutes and can be used once.

If you did not ask to sign in, nothing has happened to any account and you can
ignore this message. Somebody typed this address into the sign-in form; that on
its own gives them nothing.
"""


def request_code(conn: psycopg.Connection, raw_email: str,
                 mailer: mail.Mailer) -> None:
    """Mint a code, store its digest, and mail it. Returns nothing, deliberately.

    **The code is not returned, not logged, and has no development reveal path.** The only
    way to learn a code is to receive the message. A `dev_code` escape hatch is the
    obvious convenience and it is the one thing that would make every other bound in this
    module decorative — a flag flipped on the wrong deployment hands codes to whoever can
    reach the sign-in page. `app/remixkit` has such a path; this does not, and a
    deployment with no mail configured refuses to issue codes rather than falling back to
    showing them.

    `mailer` is a required argument rather than defaulted to `mail.load()`. The composition
    root passes it, so a caller cannot get an unconfigured deployment silently sending
    nothing, and a test cannot accidentally reach SES.

    Order of operations: bounds first, so a refusal costs one round trip and writes
    nothing; then the row; then the send. **If the send fails the row is removed.** A
    challenge whose code nobody received is worse than no challenge at all — the resend
    floor would then block the retry for thirty seconds, locking the person out of a code
    they never got.
    """
    email = _normalise(raw_email)

    # The floor first: it is per-address, so it is the refusal a real person hits, and it
    # should not be masked by a global bound somebody else caused.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT extract(epoch from now() - sent_at) AS age_s
                 FROM otp_challenge WHERE email = %s""",
            (email,))
        outstanding = cur.fetchone()
    if outstanding is not None:
        age_s = float(outstanding["age_s"])
        if age_s < RESEND_AFTER.total_seconds():
            wait = int(RESEND_AFTER.total_seconds() - age_s) + 1
            raise OtpRefused(
                f"A code was sent to {email} moments ago. Wait {wait} seconds before "
                f"asking for another, then check the address is right if it has not "
                f"arrived.",
                reason="too_soon")

    recent = codes_sent_since(conn, hours=1)
    if recent >= MAX_CODES_PER_HOUR:
        raise OtpRefused(
            f"{recent} sign-in codes have been sent in the last hour, which is the "
            f"limit. The bound is global rather than per-address — it exists to stop this "
            f"form being used to mail strangers — so somebody else's burst can delay you. "
            f"Try again shortly.",
            reason="rate_limited")

    code = mint_code()

    # One row per address: a second request replaces the first rather than sitting beside
    # it, so there is never a set of simultaneously-valid codes. `attempts` resets to zero
    # with the new code — a fresh challenge is fresh, and carrying the count forward would
    # mean somebody who mistyped five times could never sign in from that address again.
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO otp_challenge (email, code_hash, expires_at, attempts, sent_at)
               VALUES (%s, %s, now() + %s, 0, now())
          ON CONFLICT (email) DO UPDATE
                  SET code_hash = excluded.code_hash,
                      expires_at = excluded.expires_at,
                      attempts = 0,
                      sent_at = now()""",
            (email, hash_code(email, code), TTL))

    minutes = max(1, int(TTL.total_seconds() // 60))
    try:
        mailer.send(
            to=email,
            subject=_SUBJECT.format(code=code),
            body=_BODY.format(code=code, minutes=minutes),
            # Stable for a given code, so a retried send is identifiable as the same
            # message in the raw headers. The digest rather than the code, because an
            # idempotency key travels in a header and reaches logs.
            #
            # **The separator is a hyphen and must stay one.** SES carries this as a
            # message *tag*, and a tag value may contain only ASCII letters, digits,
            # underscore and hyphen. The first version of this line used `otp:` — the
            # colon made SES answer `InvalidParameterValue`, which `mail._PERMANENT`
            # correctly classifies as never-retry, so every sign-in code failed to send,
            # permanently, for every address. The mail layer was behaving exactly right
            # with a message it should never have been handed.
            idempotency_key=f"otp-{hash_code(email, code)[:32]}",
        )
    except Exception:
        # Not swallowed and not translated — the caller must see why mail failed. The row
        # goes because a challenge nobody can answer is strictly worse than none: see the
        # docstring.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM otp_challenge WHERE email = %s", (email,))
        raise


# ------------------------------------------------------------ checking a code

def verify_code(conn: psycopg.Connection, raw_email: str, raw_code: str) -> str:
    """Check a code and consume it. Returns the normalised address, or raises.

    Returning the address rather than `True` is what makes the caller use the *normalised*
    form for the account lookup that follows, instead of the raw string a form gave it.
    A `bool` here would make `accounts.sign_in(conn, request.form["email"])` the natural
    next line, and that looks up an unnormalised address in a table of normalised ones.

    The order of the checks is the security-relevant part:

      1. **No challenge** — nothing to check.
      2. **Expired** — the row is deleted rather than left to be counted against.
      3. **Attempts exhausted, before the comparison.** This is the check that makes the
         bound real: once five guesses are spent the *correct* code is refused too. If
         exhaustion were checked after comparing, an attacker's sixth guess would still be
         evaluated, and the counter would be a speed bump rather than a limit.
      4. **The comparison**, in constant time.

    A wrong code says the same sentence however wrong it was, and carries the same
    `reason`. Anything that varied with the guess is a gradient to climb.
    """
    email = _normalise(raw_email)
    # Whitespace and the hyphens people paste out of a message. Not a fallback: this is
    # the same normalisation `normalise_email` does for addresses, applied to the other
    # field the same form collects, and it happens before anything is compared so there is
    # one canonical form rather than two that sometimes match.
    code = raw_code.strip().replace(" ", "").replace("-", "")

    with conn.cursor() as cur:
        cur.execute(
            """SELECT code_hash, attempts, now() > expires_at AS expired
                 FROM otp_challenge WHERE email = %s""",
            (email,))
        challenge = cur.fetchone()

    if challenge is None:
        raise OtpRefused(
            f"No sign-in code is outstanding for {email}. Ask for one and it will be "
            f"mailed to that address.",
            reason="no_challenge")

    if challenge["expired"]:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM otp_challenge WHERE email = %s", (email,))
        raise OtpRefused(
            f"That code has expired — they last {int(TTL.total_seconds() // 60)} "
            f"minutes. Ask for a new one.",
            reason="expired")

    if int(challenge["attempts"]) >= MAX_ATTEMPTS:
        raise OtpRefused(
            f"That code has been guessed at {MAX_ATTEMPTS} times and is now closed, so "
            f"it will not work even if the next attempt is right. Ask for a new one.",
            reason="attempts_exhausted")

    if not hmac.compare_digest(str(challenge["code_hash"]), hash_code(email, code)):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE otp_challenge SET attempts = attempts + 1 WHERE email = %s",
                (email,))
        raise OtpRefused(
            "That code is not right. Check the most recent message — asking for a new "
            "code replaces the previous one.",
            reason="wrong_code")

    # Single use. Without this the code stays valid for the rest of its ten minutes after
    # the owner has already signed in with it, which is exactly the window an intercepted
    # code is worth something in.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM otp_challenge WHERE email = %s", (email,))
    return email


def outstanding(conn: psycopg.Connection, raw_email: str) -> dict[str, Any] | None:
    """The live challenge for an address, or `None`. For the console's step-two page.

    Never returns `code_hash` — nothing outside this module has any business reading it,
    and leaving it out means it cannot reach a template by somebody spreading a row into
    a context dict. `accounts._ACCOUNT_COLUMNS` omits its own hash for the same reason.
    """
    email = _normalise(raw_email)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT email, attempts, sent_at, expires_at, now() > expires_at AS expired
                 FROM otp_challenge WHERE email = %s""",
            (email,))
        return cur.fetchone()
