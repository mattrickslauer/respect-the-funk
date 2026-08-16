# Moving mattrickslauer.com to Route 53, and naming the console

**One manual step, and it is the irreversible-feeling one.** Everything else is
`terraform apply`. Read the whole page before starting, because the ordering is what
makes this safe and the ordering is not obvious.

Two things ship together here and it is worth being clear why. The console needs a name
(`spindle.mattrickslauer.com`) so that links in outbound mail sit on a domain a recipient
recognises and a spam filter can associate with the sending address. Giving it that name
needs an ACM certificate, ACM validates by DNS, and validating by DNS from inside a
`terraform apply` is not possible while a human has to go and paste a record at a
registrar. So the zone moves first, and then the name is a resource like anything else.

---

## What already exists, and what is being replaced

Before this change, `mattrickslauer.com` is served by Namecheap
(`dns1.registrar-servers.com`, `dns2.registrar-servers.com`) and holds nine records. Read
off the authoritative nameserver on 2026-08-16:

| Name | Type | Value |
|---|---|---|
| `mattrickslauer.com` | A | four CloudFront edge addresses (`108.157.150.x`) |
| `mattrickslauer.com` | AAAA | eight CloudFront edge addresses |
| `www` | CNAME | `d1uv1o8afvfumj.cloudfront.net` |
| `bounce` | MX | `10 feedback-smtp.us-east-1.amazonses.com` |
| `bounce` | TXT | `v=spf1 include:amazonses.com ~all` |
| `_dmarc` | TXT | `v=DMARC1; p=none;` |
| `<token>._domainkey` ×3 | CNAME | `<token>.dkim.amazonses.com` |

`dns.tf` reproduces every one of them. Two are expressed differently and neither is a
change of behaviour:

- the apex and `www` become **ALIAS** records pointing at the distribution, rather than a
  frozen list of the edge IPs Namecheap had to hardcode because it has no apex ALIAS;
- the three DKIM CNAMEs come from `aws_route53_record.ses_dkim`, which has been in
  `main.tf` all along waiting for a zone id to exist.

**There is no MX and no apex TXT, and none is invented.** See the reply-to section of
`ses-sign-in-mail.md` — `spindle@mattrickslauer.com` cannot currently receive mail, and
that is a decision to make deliberately rather than a gap to paper over during a
migration.

---

## Step 1 — build the zone (changes nothing)

**This is a targeted apply, and the targeting is not optional.** A plain `terraform
apply` would also create the certificate, and `aws_acm_certificate_validation` then
blocks for its full 75-minute timeout waiting for a record that no resolver can see yet —
because Namecheap is still authoritative. That is a hang, not progress, and it happens
before you have the nameservers you need in order to end it.

So the zone goes up on its own first:

```
terraform -chdir=infra/terraform/spindle apply \
  -target=aws_route53_zone.main \
  -target=aws_route53_record.apex_a   -target=aws_route53_record.apex_aaaa \
  -target=aws_route53_record.www_a    -target=aws_route53_record.www_aaaa \
  -target=aws_route53_record.mail_from_mx -target=aws_route53_record.mail_from_spf \
  -target=aws_route53_record.dmarc    -target=aws_route53_record.ses_dkim
```

This creates the hosted zone and all nine records. **Live DNS is untouched**: the world
still asks Namecheap, and Namecheap still answers exactly as before. Route 53 is a
complete, inert copy.

The certificate and the CloudFront distribution are step 4, after the switch.

*Done on 2026-08-16 — zone `Z090126035V1P4R9WWWU4`.*

## Step 2 — check the copy before trusting it

Ask Route 53 directly, by name, before anyone else does:

```
NS=$(terraform -chdir=infra/terraform/spindle output -json dns_nameservers | jq -r '.[0]')

dig +noall +answer A     mattrickslauer.com        @"$NS"
dig +noall +answer CNAME www.mattrickslauer.com    @"$NS"
dig +noall +answer MX    bounce.mattrickslauer.com @"$NS"
dig +noall +answer TXT   bounce.mattrickslauer.com @"$NS"
dig +noall +answer TXT   _dmarc.mattrickslauer.com @"$NS"
```

Every one must answer, and the DKIM three must answer as well. **This is the whole safety
of the migration** — the copy is verifiable against the new authority before the new
authority is consulted by anybody. If a record is missing, fix it here; a missing record
discovered after step 3 is an outage.

## Step 3 — repoint the nameservers (the manual step)

```
terraform -chdir=infra/terraform/spindle output dns_nameservers
```

Set those four at Namecheap, replacing `dns1/dns2.registrar-servers.com`.

Propagation is usually minutes and can be up to the parent zone's TTL. From this moment
Route 53 is authoritative and `dns.tf` is the source of truth for this domain — a record
added at the registrar afterwards does nothing at all.

Confirm the delegation actually moved before going on, or step 4 will fail in a way that
looks like a certificate problem and is not:

```
dig +short NS mattrickslauer.com @8.8.8.8     # must be the four awsdns names
```

## Step 4 — the certificate and the console's name

Now the full apply, with no targets:

```
terraform -chdir=infra/terraform/spindle apply
```

ACM can now resolve its validation record, so the certificate issues (usually a few
minutes), CloudFront is created, and `spindle.mattrickslauer.com` starts answering. A
CloudFront distribution takes a further few minutes to deploy to the edge — a 403 or a
TLS error in that window is the distribution still rolling out, not a misconfiguration.

## Step 5 — confirm nothing broke

Mail first, because mail is the thing whose breakage is silent:

```
aws ses get-identity-verification-attributes      --identities mattrickslauer.com
aws ses get-identity-dkim-attributes              --identities mattrickslauer.com
aws ses get-identity-mail-from-domain-attributes  --identities mattrickslauer.com
```

All three must still read `Success`. They were `Success` before the migration, so
anything else here means a record did not come across and step 2 was not thorough enough.

Then the website, then the console:

```
curl -sI https://mattrickslauer.com        | head -1
curl -sI https://spindle.mattrickslauer.com | head -1
```

## Step 6 — close the side door

The console's Lambda Function URL is still public and still answers, deliberately: it is
the fallback while the domain settles, and `domain.tf` explains why closing it in the
same change would be reckless on a deployment whose only credential is an emailed code.

Once `spindle.mattrickslauer.com` has been serving reliably, close it — an Origin Access
Control plus `authorization_type = "AWS_IAM"`, so CloudFront becomes the only caller.
That is its own change, with its own apply, and its own way to lock everybody out if it
is wrong.

---

## If the site goes down after step 3

Set the nameservers back to `dns1.registrar-servers.com` and
`dns2.registrar-servers.com`. Namecheap still holds the original zone — nothing in this
runbook deletes it — so reverting is a nameserver change and a wait, not a rebuild.

That is the reason the switch comes after the zone is built and checked, and the reason
nothing is deleted at the registrar until well after the migration is confirmed. Leave
Namecheap's zone in place — it costs nothing and it is the whole rollback.
