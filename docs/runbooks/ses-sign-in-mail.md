# Wiring SES, so people can sign in

**Read this before deploying the OTP sign-in change.** Since 2026-08-15 an emailed
six-digit code is the only credential this console accepts. There is no shared operator
token and no development reveal path, which means:

> **A deployment that cannot send mail is a deployment nobody can enter.**

That is the intended behaviour — `otp.py` refuses to issue a code and the sign-in page
names the missing variables — but it makes the two manual steps below load-bearing rather
than housekeeping. Terraform does everything else.

---

## What Terraform does

With `mail_domain` set, `terraform apply` creates:

- `aws_ses_domain_identity.mail` — the domain identity
- `aws_ses_domain_dkim.mail` — the three DKIM tokens
- `aws_route53_record.ses_dkim` — the three CNAMEs, **only if `mail_route53_zone_id` is
  also set**
- `aws_iam_role_policy.console_send_mail` — `ses:SendEmail` on the console role, scoped to
  that identity's ARN and nothing else
- the three `PLATFORM_MAIL_*` variables on the console function

With `mail_domain` empty it creates none of them and applies cleanly. That is a supported
state; it is just one where nobody can sign in.

## Step 1 — verify the domain (DNS)

**If the domain's DNS is in Route 53:** set `mail_route53_zone_id` and you are done. The
CNAMEs are created by the apply and SES verifies within the hour, usually in minutes.

**If it is not** — which is common, and not a lesser path — the apply prints them:

```
terraform output ses_dkim_records
```

Three records, each a CNAME from `<token>._domainkey.<domain>` to
`<token>.dkim.amazonses.com`. Add all three at whatever hosts the domain's DNS. SES
checks periodically; the identity flips to `Success` on its own.

Check it without waiting on the console UI:

```
aws ses get-identity-verification-attributes --identities "$MAIL_DOMAIN"
aws ses get-identity-dkim-attributes        --identities "$MAIL_DOMAIN"
```

Both must report `Success`. Until then every send raises
`MailRefused: SES refused: MailFromDomainNotVerified`, which `otp.py` surfaces on the
sign-in page rather than swallowing — the person trying to sign in sees that the provider
refused, not a blank failure.

## Step 2 — leave the SES sandbox

**This is the step that catches people, and it cannot be automated.**

A new SES account is in the *sandbox*: it will only deliver to addresses that have
themselves been individually verified, and it is capped at 200 messages a day. Verifying
the sending domain does **not** get you out of it.

While you are in the sandbox, on a deployment where an emailed code is the only way in:

> **Only addresses you have verified in the AWS console can sign in at all.**

For a demo or a judging window that may be entirely sufficient — verify the two or three
addresses that need in, and stop. For anything else, request production access:

```
aws sesv2 put-account-details \
  --production-access-enabled \
  --mail-type TRANSACTIONAL \
  --website-url https://<your-domain> \
  --use-case-description "Six-digit sign-in codes for our own console users, sent only in response to a sign-in request. No marketing."
```

Turnaround is typically under a day. `TRANSACTIONAL` is the honest answer for sign-in
codes and the one that gets approved fastest; the outbox that pitches curators is a
separate, and genuinely commercial, use of the same identity — say so if asked.

To verify individual addresses in the meantime:

```
aws ses verify-email-identity --email-address someone@example.com
```

They receive a confirmation link and must click it.

## Step 3 — set the three variables

All three, or none of them count. `settings.mail_configured` is all-or-nothing and
`mail.load()` refuses with all three named:

```hcl
mail_domain          = "respectthefunk.com"
mail_sender          = "hello@respectthefunk.com"    # must be at mail_domain
mail_reply_to        = "hello@respectthefunk.com"
mail_postal_address  = "…, …, …"
```

`mail_sender` must be at `mail_domain` or SES rejects every send once the identity is
verified — the identity authorises the domain, not an arbitrary From.

`mail_postal_address` is required because CAN-SPAM §7704(a)(5) requires a physical address
in every *commercial* message and `mail.compliant_body` appends it. **Sign-in codes do not
carry it**, and that is deliberate: a login email is transactional, and appending the
outreach footer would invite somebody to reply STOP to a sign-in code and thereby set
`contact_route.state = 'opted_out'` on a party they may be in an active campaign with.
`otp.py` has the argument.

Confirm after applying:

```
terraform output mail_configured     # must be true
```

## Verifying end to end

```
curl -si https://<function-url>/signin/code -d "email=someone@example.com" | head -1
```

- `200` — a code was minted and handed to SES.
- `503` — `mail_configured` is false. The body names which variables are unset.
- `502` — SES refused. Almost always an unverified domain (step 1) or a sandbox recipient
  (step 2); the body carries the SES error code.
- `429` — the resend floor (30s for one address) or the hourly cap (60 addresses
  cluster-wide). Both are in `otp.py`.

## If nobody can get in

There is no break-glass credential. That was decided knowingly and `auth.py` records the
trade; the recovery path is the database, not the console.

To let a known address in when mail is broken, mint a token by hand and set it as the
`rtf_session` cookie:

```python
# against the cluster, with rtf_platform importable
from rtf_platform import accounts, db
conn = db.connect(DATABASE_URL)
account, token = accounts.sign_in(conn, "you@example.com")
print(token)          # set as the rtf_session cookie, or send as a Bearer header
```

`accounts.sign_in` is the same call the verify route makes *after* a code checks out —
running it directly is precisely the step that skips the proof, which is why it needs
database credentials and why it is at the bottom of a runbook rather than behind a flag.
It rotates the token, so it signs out any existing session for that account.
