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
