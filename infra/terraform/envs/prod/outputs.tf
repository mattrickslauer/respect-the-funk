output "console_url" {
  value       = module.api.function_url
  description = "The artist console. No authentication — see remixkit/auth/."
}

output "ecr_repository_url" {
  value       = module.ecr.repository_url
  description = "Push api-latest and worker-latest here before the first apply completes."
}

output "queue_url" { value = module.queue.queue_url }
output "dlq_url" { value = module.queue.dlq_url }
output "batch_job_queue" { value = module.worker.job_queue_arn }

output "secret_parameters" {
  value       = module.secrets.parameter_names
  description = "Created empty. Populate with `aws ssm put-parameter --overwrite`."
}
