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
  description = "The changefeed's webhook sink. Empty until changefeed_webhook_token is set. Feed it to `python -m rtf_platform.changefeed --dry-run --url <this>` to render the exact CREATE CHANGEFEED statement — which a human then runs, because a changefeed draws request units continuously and nothing in this repo is allowed to start one on its own."
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

# Whether this deployment can send at all — and therefore whether anybody can sign in,
# now that an emailed code is the only credential. Printed after every apply because the
# answer is not obvious from the resources: the identity can exist while the domain is
# still unverified and while the account is still in the SES sandbox.
output "mail_configured" {
  description = "True when all three PLATFORM_MAIL_* values are set on the console function."
  value       = var.mail_sender != "" && var.mail_reply_to != "" && var.mail_postal_address != ""
}
