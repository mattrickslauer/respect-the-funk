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
