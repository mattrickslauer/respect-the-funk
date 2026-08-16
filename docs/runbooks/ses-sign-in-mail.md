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

**If the domain is already verified in SES**, do not let Terraform create the identity:
`VerifyDomainIdentity` issues a *fresh* verification token, which can flip an
already-verified domain back to `Pending` until the new TXT is published. Import it
instead, then plan — the create disappears and only the IAM policy is left to add.

```
terraform -chdir=infra/terraform/spindle import 'aws_ses_domain_identity.mail[0]' <domain>
terraform -chdir=infra/terraform/spindle import 'aws_ses_domain_dkim.mail[0]'     <domain>
```

Note what importing means afterwards: Terraform now manages an identity it did not
create, so a future `terraform destroy` would delete it.

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

## Step 3 — set the mail variables

**Sign-in needs two of them; outreach needs three.** That split is real and deliberate:

```hcl
mail_domain          = "respectthefunk.com"
mail_sender          = "hello@respectthefunk.com"    # must be at mail_domain
mail_reply_to        = "hello@respectthefunk.com"
# mail_postal_address = "…"                          # only for outreach — see below
```

A **sign-in code is transactional**: the recipient asked for it seconds ago and there is
nothing to unsubscribe from. `settings.transactional_mail_configured` needs only the
sender and the reply-to, and `mail.load()` checks exactly that.

**Outreach is commercial**, so CAN-SPAM §7704(a)(5) requires a physical postal address in
the body and `settings.mail_configured` demands all three. With the address unset,
`sender.py` refuses to claim the outbox and the changefeed attaches no sender — **sign-in
is unaffected**. That is this deployment's state as of 2026-08-15: people can log in,
nobody can be pitched.

Do not put a placeholder in `mail_postal_address` to make a check go green. It is
appended verbatim to the footer of every commercial message by `mail.compliant_body`, so
a fake address is a fake address on real mail.

`mail_sender` must be at `mail_domain` or SES rejects every send once the identity is
verified — the identity authorises the domain, not an arbitrary From.

Confirm after applying:

```
terraform output sign_in_mail_configured    # must be true, or nobody can log in
terraform output outreach_mail_configured   # false is fine unless you are sending pitches
```

## Step 4 — deliverability, or the code arrives in spam

**Measured on 2026-08-15: a sign-in code to Gmail landed in the spam folder.** The
identity was verified and DKIM was passing, which is the state most people stop at. It is
not enough, and the gap is specific.

### What was actually wrong

| Check | State | Aligned with `mattrickslauer.com`? |
|---|---|---|
| DKIM | pass | **yes** |
| SPF | pass, for `amazonses.com` | **no** |
| DMARC | pass (on DKIM), `p=none` | policy publishes no intent |

DMARC passed, on the DKIM half alone. The SPF half failed *alignment*, and that is the
part worth understanding because the obvious fix does not work:

> Without a custom MAIL FROM, SES sets the envelope sender (`Return-Path`) to
> `*.amazonses.com`. The receiver checks SPF against **that** domain, not against the one
> in your `From` header. Adding `include:amazonses.com` to `mattrickslauer.com`'s SPF
> record changes nothing, because that record is never consulted.

The fix is to move the envelope sender onto a subdomain you control, which
`aws_ses_domain_mail_from` now does — `bounce.<domain>`, with
`behavior_on_mx_failure = "UseDefaultValue"` so nothing breaks in the window before the
DNS exists.

### Publish two records

**As of 2026-08-16 Terraform does this**, once the Route 53 migration in
`docs/runbooks/dns-to-route53.md` completes: `aws_route53_record.mail_from_mx` and
`_spf` in `dns.tf` create both records, and `ses_mail_from_records` returns an empty list
so nobody adds them a second time by hand. The manual path below applies only to a
deployment whose zone is still somewhere else.

```
terraform -chdir=infra/terraform/spindle output ses_mail_from_records
```

At the registrar (when this domain's DNS is **not** in Route 53, so Terraform cannot do
it):

| Name | Type | Value |
|---|---|---|
| `bounce.<domain>` | MX | `10 feedback-smtp.us-east-1.amazonses.com` |
| `bounce.<domain>` | TXT | `v=spf1 include:amazonses.com ~all` |

Confirm, and expect `Success` rather than `Pending`:

```
aws ses get-identity-mail-from-domain-attributes --identities <domain>
```

Once it reads `Success`, tighten `behavior_on_mx_failure` to `RejectMessage` in
`main.tf`. Leaving it on `UseDefaultValue` forever means a DNS lapse silently reverts to
an unaligned envelope — the exact defect this step fixed, returning without a symptom.

### Strengthen DMARC

The current record is `v=DMARC1; p=none;` — no reporting address, no stated intent. A
receiver reads that as a domain nobody is minding. Once SPF aligns, publish:

```
_dmarc.<domain>  TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc@<domain>; fo=1"
```

Go to `p=quarantine` **after** confirming alignment, not before — with `p=none` a
misalignment is a scoring penalty, and with `p=quarantine` it is a spam folder by policy.

### What no amount of DNS will fix quickly

`mattrickslauer.com` has no sending history. Reputation is earned per-domain over days of
consistent, low-complaint volume, and a brand-new domain sending its first few messages
gets treated with suspicion whatever its authentication says. Expect the first codes to
need rescuing from spam even after the records are right.

Two things that genuinely help, in order:

1. **Mark the first few as "not spam" in Gmail.** A recipient-side signal is worth more
   than anything sender-side at this volume.
2. **Leave the SES sandbox** (Step 2). Sandbox is not itself a spam signal, but the
   200/day cap keeps volume too low to build reputation.

Do *not* reach for the usual list-mail remedies — `List-Unsubscribe`, a postal footer,
an unsubscribe link. A sign-in code is transactional; adding an opt-out to one invites a
recipient to reply STOP to a login email, and `otp.py` explains what that would do to a
counterparty in an active campaign.

### The reply-to is not a real mailbox

**Corrected 2026-08-16.** This section used to say that the domain's MX pointed at the
registrar's forwarding service and that `spindle@` would deliver if a forwarder existed.
That was wrong, and the truth is worse:

```
$ dig +noall +answer MX mattrickslauer.com @dns1.registrar-servers.com
$ dig +noall +answer TXT mattrickslauer.com @dns1.registrar-servers.com
```

Both empty, asked of the authoritative nameserver rather than a cache. **There is no MX
record on this domain at all**, so `spindle@mattrickslauer.com` — the address published
as `mail_reply_to` on every sign-in code and every pitch — cannot receive mail. A curator
who replies gets a bounce.

Two consequences worth separating:

- **Sign-in is unaffected.** Nobody replies to a six-digit code.
- **Outreach is not.** A reply is the outcome the whole pipeline exists to produce, and
  it is currently being thrown away by the mail system before anybody sees it. The reply
  a pitch is asking for is the one thing that cannot arrive.

`dns.tf` deliberately does **not** invent an MX during the Route 53 migration. Choosing
where this domain's mail lands is a decision with a provider and a monthly cost attached,
and a guessed MX that silently drops replies is worse than an absent one that bounces
them loudly. Add it in the change that makes the choice.

Note that `m@mattrickslauer.com` is a verified SES *email identity*, which proves only
that somebody once confirmed a link — it is not evidence that the domain can receive mail
today.

## Verifying end to end

```
curl -si https://<function-url>/signin/code -d "email=someone@example.com" | head -1
```

- `200` — a code was minted and handed to SES.
- `503` — the transport is unconfigured. The body names which variables are unset, and will never ask for `PLATFORM_MAIL_POSTAL_ADDRESS`.
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
# against the cluster, with spindle importable
from spindle import accounts, db
conn = db.connect(DATABASE_URL)
account, token = accounts.sign_in(conn, "you@example.com")
print(token)          # set as the rtf_session cookie, or send as a Bearer header
```

`accounts.sign_in` is the same call the verify route makes *after* a code checks out —
running it directly is precisely the step that skips the proof, which is why it needs
database credentials and why it is at the bottom of a runbook rather than behind a flag.
It rotates the token, so it signs out any existing session for that account.
