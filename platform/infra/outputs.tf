output "console_url" {
  value       = aws_lambda_function_url.console.function_url
  description = "The console. This is the demo URL PLATFORM-SPEC §8 day 12 requires."
}

output "function_name" {
  value = aws_lambda_function.console.function_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.console.name
}

output "masters_bucket" {
  value       = aws_s3_bucket.masters.bucket
  description = "Where masters live. Set PLATFORM_MASTERS_BUCKET to this for a local console or worker; the deployed function gets it from its own environment."
}

output "classifier_repository_url" {
  value       = aws_ecr_repository.classifier.repository_url
  description = "Push the classifier image here, then set classifier_image_uri to the @sha256 digest and apply again."
}

output "classifier_function_name" {
  value       = length(aws_lambda_function.classifier) > 0 ? aws_lambda_function.classifier[0].function_name : ""
  description = "Empty until an image is pushed. Set PLATFORM_CLASSIFIER_FUNCTION to this for a worker."
}

output "changefeed_webhook_url" {
  value       = length(aws_lambda_function_url.changefeed) > 0 ? aws_lambda_function_url.changefeed[0].function_url : ""
  description = "The changefeed's webhook sink. Empty until changefeed_webhook_token is set. Feed it to `python -m spindle.changefeed --dry-run --url <this>` to render the exact CREATE CHANGEFEED statement — which a human then runs, because a changefeed draws request units continuously and nothing in this repo is allowed to start one on its own."
}

# The DKIM records, for the case where the domain's DNS is not in Route 53 and
# `aws_route53_record.ses_dkim` therefore created nothing. Three CNAMEs; add them at the
# registrar and SES verifies the domain on its own within the hour.
#
# Empty when `mail_domain` is unset, and empty when the zone id *is* set — in the second
# case the records already exist and printing them would invite somebody to add them
# twice. See docs/runbooks/ses-sign-in-mail.md.
output "ses_dkim_records" {
  description = "CNAMEs to add manually when mail_route53_zone_id is unset."
  value = (
    var.mail_domain == "" || var.mail_route53_zone_id != ""
    ? []
    : [for token in aws_ses_domain_dkim.mail[0].dkim_tokens : {
      name  = "${token}._domainkey.${var.mail_domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }]
  )
}

# Two answers, because there are two questions and one of them is the one that matters
# after an apply. Both are printed because reading only `mail_configured` would say "mail
# is not configured" on a deployment where sign-in works perfectly well.

# **Can anybody get in?** A sign-in code is transactional: it needs a verified sender and
# a reply-to, and no postal address — CAN-SPAM requires that of commercial mail, and
# `otp.py` never calls `compliant_body`. `settings.transactional_mail_configured` is the
# check this mirrors, and it is the one that decides whether the console is enterable.
output "sign_in_mail_configured" {
  description = "True when sign-in codes can be sent. This is the one that decides whether anybody can log in."
  value       = var.mail_sender != "" && var.mail_reply_to != ""
}

# **Can we pitch a curator?** Strictly stronger: outreach is commercial, so CAN-SPAM
# §7704(a)(5) requires a physical postal address in the body. False here means `sender.py`
# refuses to claim the outbox and the changefeed attaches no sender — sign-in is
# unaffected. Mirrors `settings.mail_configured`.
output "outreach_mail_configured" {
  description = "True when commercial sends are lawful — needs PLATFORM_MAIL_POSTAL_ADDRESS as well."
  value       = var.mail_sender != "" && var.mail_reply_to != "" && var.mail_postal_address != ""
}

# The two records the custom MAIL FROM subdomain needs, for the registrar to publish.
#
# Until both resolve, SES falls back to `*.amazonses.com` as the envelope sender
# (`behavior_on_mx_failure = "UseDefaultValue"`), which is exactly today's behaviour —
# so nothing breaks while they are missing, and nothing improves either. SPF only
# *aligns* with the From domain once these exist, and alignment is the half that was
# failing when sign-in codes landed in spam.
#
# The MX value is region-specific: `feedback-smtp.<region>.amazonses.com`.
output "ses_mail_from_records" {
  description = "MX and TXT to publish for the MAIL FROM subdomain, so SPF aligns."
  value = var.mail_domain == "" ? [] : [
    {
      name  = "bounce.${var.mail_domain}"
      type  = "MX"
      value = "10 feedback-smtp.${var.region}.amazonses.com"
    },
    {
      name  = "bounce.${var.mail_domain}"
      type  = "TXT"
      value = "v=spf1 include:amazonses.com ~all"
    },
  ]
}
