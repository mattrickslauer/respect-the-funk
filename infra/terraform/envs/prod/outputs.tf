output "console_url" {
  value       = module.http_api.api_url
  description = "The artist console. Sign-in is per RK_AUTH_BACKEND — see remixkit/auth/."
}

output "function_url" {
  value       = module.api.function_url
  description = "Direct Lambda Function URL. Returns 403 while this account blocks anonymous function-URL access; use console_url."
}

output "ecr_repository_url" { value = module.ecr.repository_url }
output "queue_url" { value = module.queue.queue_url }
output "dlq_url" { value = module.queue.dlq_url }
output "batch_job_queue" { value = module.worker.job_queue_arn }

output "secret_parameters" {
  value       = module.secrets.parameter_names
  description = "Created empty. Populate with `aws ssm put-parameter --overwrite`."
}
