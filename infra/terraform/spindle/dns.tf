# =============================================================================
# dns.tf — the zone moves into Route 53
# =============================================================================
#
# Until 2026-08-16 this domain's DNS lived at Namecheap (`dns1.registrar-servers.com`),
# and every record that mattered was added by hand. `docs/runbooks/ses-sign-in-mail.md`
# says so three separate times, each one a manual step in a runbook: the DKIM CNAMEs,
# the MAIL FROM pair, and the DMARC record. Each of those is a place where a deployment
# is one forgotten paste away from silently unauthenticated mail.
#
# The trigger for moving was the console's custom domain. ACM validates a certificate by
# DNS, and with the zone at the registrar that is a manual step *inside* a terraform
# apply — the apply blocks on a record a human has to go and add, which is not an apply
# any more. With the zone here, validation is a resource and the alias record is a
# resource, and `spindle.mattrickslauer.com` becomes something this file can assert
# rather than something a runbook asks for.
#
#
# ## What this costs
#
# $0.50/month for the hosted zone, plus $0.40 per million queries. That is the first
# recurring charge in this stack — main.tf's header opens by claiming an idle cost of
# exactly zero, and that claim is now false by fifty cents. It is stated here rather
# than discovered on a bill, and the header has been amended to match.
#
#
# ## The migration is not complete until the registrar's nameservers change
#
# Creating this zone changes nothing about live DNS. Route 53 becomes authoritative only
# when `mattrickslauer.com`'s NS records at Namecheap are repointed at the four
# nameservers this zone allocates — `terraform output dns_nameservers` prints them.
#
# That ordering is deliberate and it is the safe one: every record below is created and
# verifiable *before* any resolver is asked to consult it. Publish the zone, check it
# answers, then switch. The runbook has the sequence.
#
# **Every record below was read off the live Namecheap zone on 2026-08-16 and is a
# replica, not a redesign.** The two differences, both intentional:
#
#   * the apex and `www` become ALIAS records rather than four hardcoded A records and
#     a CNAME. Namecheap has no ALIAS at the apex, so it published CloudFront's edge
#     addresses literally — a list that CloudFront is free to change under us. An ALIAS
#     tracks the distribution instead of a snapshot of its IPs.
#
#   * the three DKIM CNAMEs are NOT here. They are already expressed by
#     `aws_route53_record.ses_dkim` in main.tf, which has been waiting for a zone id to
#     exist. Writing them here as well would be two resources fighting over one name.
#
#
# ## What is deliberately NOT replicated, because it does not exist
#
# **There is no MX record on this domain, and no apex TXT.** Verified against
# `dns1.registrar-servers.com` directly, not a cache. That means `spindle@` — the address
# this deployment publishes as `mail_reply_to` — cannot receive mail, so a curator who
# replies to a pitch gets a bounce. The runbook currently claims the registrar forwards
# it; that claim is wrong and has been corrected there.
#
# No MX is invented here. Choosing where this domain's mail lands is a decision with a
# monthly cost and a provider attached, and `NO FALLBACKS` applies to infrastructure too:
# a guessed MX that silently drops replies is worse than an absent one that bounces them
# loudly. Add it in the commit that makes the choice.

variable "dns_domain" {
  type        = string
  default     = ""
  description = <<-EOT
    Domain to host in Route 53. Empty creates no zone and no records at all, which is
    still a supported state — the console keeps answering on its Function URL.

    Usually the same string as `mail_domain`, and deliberately a separate variable: mail
    identity and DNS authority are two decisions, and a deployment that verifies SES on a
    domain whose DNS somebody else runs is an ordinary arrangement, not a mistake.
  EOT
}

variable "console_subdomain" {
  type        = string
  default     = "spindle"
  description = "Subdomain of dns_domain the console answers on. Joined as <console_subdomain>.<dns_domain>."
}

locals {
  dns_enabled = var.dns_domain != ""

  # The console's public hostname, in one place, so the certificate, the distribution
  # alias, the DNS record, the CORS allow-list and the base URL handed to the app cannot
  # disagree about what this deployment is called.
  console_host = local.dns_enabled ? "${var.console_subdomain}.${var.dns_domain}" : ""

  # Route 53's zone id if this configuration created one, the caller's value otherwise.
  # `aws_route53_record.ses_dkim` reads this rather than the variable directly, so moving
  # the zone in here wires DKIM up automatically instead of needing the id pasted into
  # tfvars after the first apply — which would be a two-apply bootstrap for no reason.
  mail_zone_id = var.mail_route53_zone_id != "" ? var.mail_route53_zone_id : (
    local.dns_enabled ? aws_route53_zone.main[0].zone_id : ""
  )

  # The same question, answered from variables alone.
  #
  # `mail_zone_id` above is unknowable until apply, because on a first run the zone does
  # not exist yet. A `count` may not depend on such a value — Terraform cannot decide how
  # many instances to plan — and `count = local.mail_zone_id == "" ? 0 : 3` fails the plan
  # outright with "the count value depends on resource attributes that cannot be
  # determined until apply". This boolean asks *whether there will be a zone* rather than
  # *what its id is*, which is answerable from `var.dns_domain` and
  # `var.mail_route53_zone_id` before anything is created.
  mail_zone_known = var.mail_route53_zone_id != "" || local.dns_enabled
}

resource "aws_route53_zone" "main" {
  count = local.dns_enabled ? 1 : 0
  name  = var.dns_domain

  comment = "Migrated from Namecheap 2026-08-16. See dns.tf for the record-by-record provenance."
}

# ------------------------------------------------------------------ the website
#
# The apex and `www` already serve a static site from a CloudFront distribution this
# configuration does not own and must not adopt. It is read, not managed: `terraform
# destroy` here has no business deleting the marketing site.
data "aws_cloudfront_distribution" "site" {
  count = local.dns_enabled ? 1 : 0
  id    = var.site_distribution_id
}

variable "site_distribution_id" {
  type        = string
  default     = "E21HGPF5XR0CUL"
  description = <<-EOT
    The existing CloudFront distribution serving the apex and www. Read via a data source
    and never managed here — it predates this stack and belongs to the marketing site.

    Verified 2026-08-16: E21HGPF5XR0CUL is d1uv1o8afvfumj.cloudfront.net, aliases
    mattrickslauer.com and www.mattrickslauer.com, origin mattrickslauer-site-*.s3.
  EOT
}

# Z2FDTNDATAQYW2 is CloudFront's hosted zone id. It is the same constant for every
# distribution in every region and AWS documents it as such; there is no data source that
# returns it, which is why it appears as a literal here rather than as a lookup.
locals {
  cloudfront_zone_id = "Z2FDTNDATAQYW2"
}

resource "aws_route53_record" "apex_a" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = var.dns_domain
  type    = "A"

  alias {
    name                   = data.aws_cloudfront_distribution.site[0].domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "apex_aaaa" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = var.dns_domain
  type    = "AAAA"

  alias {
    name                   = data.aws_cloudfront_distribution.site[0].domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "www_a" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = "www.${var.dns_domain}"
  type    = "A"

  alias {
    name                   = data.aws_cloudfront_distribution.site[0].domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "www_aaaa" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = "www.${var.dns_domain}"
  type    = "AAAA"

  alias {
    name                   = data.aws_cloudfront_distribution.site[0].domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

# --------------------------------------------------------------------- the mail
#
# The MAIL FROM pair, which `outputs.tf` has been emitting for a human to paste since
# 2026-08-15. They are the records that make SPF *align* — main.tf's comment above
# `aws_ses_domain_mail_from` is the full argument and it is worth reading before touching
# either of these.
#
# Gated on the SES side being configured as well as DNS: a bounce subdomain for an
# identity that does not exist is two records pointing at nothing.

resource "aws_route53_record" "mail_from_mx" {
  count   = local.dns_enabled && var.mail_domain != "" ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = aws_ses_domain_mail_from.mail[0].mail_from_domain
  type    = "MX"
  ttl     = 600
  records = ["10 feedback-smtp.${var.region}.amazonses.com"]
}

resource "aws_route53_record" "mail_from_spf" {
  count   = local.dns_enabled && var.mail_domain != "" ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = aws_ses_domain_mail_from.mail[0].mail_from_domain
  type    = "TXT"
  ttl     = 600
  records = ["v=spf1 include:amazonses.com ~all"]
}

# DMARC, replicated exactly as Namecheap held it: `v=DMARC1; p=none;`.
#
# `p=none` is weak and the runbook says to strengthen it to `p=quarantine` once SPF
# alignment is confirmed. It is copied verbatim anyway, because a zone migration that
# also changes policy is two changes wearing one commit — if mail breaks after the
# nameserver switch, the first question has to be "what moved", and the answer has to be
# "nothing but the authority". Tighten it in its own change, on purpose.
resource "aws_route53_record" "dmarc" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = "_dmarc.${var.dns_domain}"
  type    = "TXT"
  ttl     = 600
  records = ["v=DMARC1; p=none;"]
}
