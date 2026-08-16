# =============================================================================
# domain.tf — the console gets a name
# =============================================================================
#
# main.tf's header used to end its cost argument with "No CloudFront, no custom domain,
# no ACM. The Function URL is a working HTTPS endpoint. A domain is a $12/year decision
# to take when there is something worth pointing it at." That decision has been taken,
# and this file is it. What made it worth taking was outbound mail: a pitch that carries
# a link to `*.lambda-url.us-east-1.on.aws` is a cold email carrying an opaque AWS
# hostname, which is a deliverability cost paid on every send — and paid immediately
# after `aws_ses_domain_mail_from` was added specifically to stop this domain's mail
# looking untrustworthy.
#
# Serving links from `spindle.mattrickslauer.com` while sending from
# `spindle@mattrickslauer.com` means the link domain and the From domain are the same
# organisational domain. That alignment is the point.
#
#
# ## Why CloudFront and not API Gateway
#
# A Lambda Function URL cannot take a custom domain. The two ways to put one in front of
# it are API Gateway (~$1.00/million requests, plus a REST/HTTP API to configure) and
# CloudFront (free tier of 1TB out and 10M requests/month, then $0.085/GB). For a
# server-rendered console at this volume CloudFront is free where API Gateway is not,
# and the same pattern is already running in this account: distribution d143oj738yqz4p
# fronts a Function URL for stainlesssteeltoolwrap.com. This is that, again.
#
# The cost note in main.tf's header has been amended rather than left to contradict this.
#
#
# ## The header that breaks everything if you forget it
#
# **`Managed-AllViewerExceptHostHeader`, not `Managed-AllViewer`.**
#
# A Lambda Function URL validates the `Host` header against its own hostname. Forward the
# viewer's Host — which is what "all viewer headers" means — and the origin receives
# `spindle.mattrickslauer.com`, does not recognise it, and answers 403 on every single
# request. The failure names neither CloudFront nor the header; it looks like the
# function is broken or the permissions are wrong, and the function logs nothing because
# it is never invoked.
#
# This managed policy exists for exactly this case. It forwards every viewer header,
# cookie and query string *except* Host, so the origin sees its own name and the app sees
# everything else. Cookies matter here specifically: sign-in is a session cookie, and a
# cache behaviour that dropped it would log everybody out at the edge.
#
#
# ## Caching is off, deliberately
#
# `Managed-CachingDisabled`. Every route this console serves is either authenticated,
# per-tenant, or a per-recipient listen token — there is nothing here that two viewers
# should ever be handed the same copy of. Static assets are served by the app from
# `spindle/static` and are small enough that caching them is not worth the risk of
# caching something else by accident. Turn caching on per-path, if ever, with a behaviour
# scoped to `/static/*` and nothing wider.
#
#
# ## The Function URL stays open, for now
#
# `aws_lambda_function_url.console` keeps `authorization_type = "NONE"`, so the old URL
# answers alongside the new domain. That is not an oversight and it is not the end state:
# the correct finish is an Origin Access Control with the URL flipped to `AWS_IAM`, so
# CloudFront is the only caller.
#
# It is a *second* apply on purpose. Sign-in is email-OTP only with no break-glass
# credential by design (`auth.py` records that trade), so an OAC misconfiguration does
# not degrade access — it removes it, from everybody, with the recovery path being a
# hand-minted session token against the database. Verify the domain answers, then close
# the side door in a change whose blast radius is one resource.

resource "aws_acm_certificate" "console" {
  count = local.dns_enabled ? 1 : 0

  # CloudFront reads viewer certificates from us-east-1 and nowhere else, whatever region
  # the rest of the stack runs in. `var.region` is us-east-1 today, so the aliased
  # provider below is a no-op — it exists so that changing var.region moves the stack
  # without silently producing a certificate CloudFront refuses to attach.
  provider = aws.us_east_1

  domain_name       = local.console_host
  validation_method = "DNS"

  lifecycle {
    # ACM will not let a certificate in use be destroyed, and replacing one in place
    # would blank the distribution's viewer certificate for the duration.
    create_before_destroy = true
  }
}

# With the zone in Route 53 this is a resource rather than a runbook step, which was the
# whole argument for moving it — see dns.tf's header.
resource "aws_route53_record" "console_cert_validation" {
  for_each = local.dns_enabled ? {
    for option in aws_acm_certificate.console[0].domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  } : {}

  zone_id         = aws_route53_zone.main[0].zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 60
  records         = [each.value.record]
  allow_overwrite = true
}

# Blocks the apply until ACM has actually seen the record and issued. Without it the
# distribution can be created with a certificate still `PENDING_VALIDATION`, which fails
# at CloudFront with an error about the certificate rather than about DNS.
#
# **This will not complete until the registrar's nameservers point at this zone.** ACM
# resolves the validation record over public DNS, so while Namecheap is still
# authoritative the record exists in Route 53 and nobody can see it. That is the expected
# ordering, and the runbook says to expect the wait.
resource "aws_acm_certificate_validation" "console" {
  count                   = local.dns_enabled ? 1 : 0
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.console[0].arn
  validation_record_fqdns = [for record in aws_route53_record.console_cert_validation : record.fqdn]
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "console" {
  count           = local.dns_enabled ? 1 : 0
  enabled         = true
  is_ipv6_enabled = true
  comment         = "${local.name} console at ${local.console_host}"
  aliases         = [local.console_host]

  # PriceClass_100 is North America and Europe. The cheapest class, and the honest one:
  # the origin is a single Lambda in us-east-1, so an edge in Singapore saves a viewer
  # there nothing but the TLS handshake while costing more per GB.
  price_class = "PriceClass_100"

  origin {
    # `function_url` arrives as `https://<id>.lambda-url.<region>.on.aws/`, and an origin
    # wants the bare host. Both ends have to come off: the scheme, and the trailing slash
    # that `aws_s3_bucket_cors_configuration` above already has a comment about.
    domain_name = trimsuffix(trimprefix(aws_lambda_function_url.console.function_url, "https://"), "/")
    origin_id   = "console"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id = "console"

    # The console is a server-rendered app with forms: sign-in posts, approvals post,
    # artists post. GET and HEAD alone would turn every write into a 405 at the edge.
    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.console[0].certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

resource "aws_route53_record" "console_a" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = local.console_host
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.console[0].domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "console_aaaa" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = local.console_host
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.console[0].domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}
