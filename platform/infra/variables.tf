variable "region" {
  type        = string
  default     = "us-east-1"
  description = "Same region as the CockroachDB cluster (aws-us-east-1) and Bedrock. Cross-region would add latency to every query and egress to every embedding."
}

variable "env" {
  type    = string
  default = "prod"
}

variable "database_url" {
  type        = string
  sensitive   = true
  description = "CockroachDB connection string. From .env — never committed."
}

variable "admin_token" {
  type        = string
  sensitive   = true
  description = "Shared operator secret. Empty means the console is read-only for everyone, which is the safe default for an unconfigured deployment."
  default     = ""
}

variable "tenant_slug" {
  type    = string
  default = "respect-the-funk"
}

variable "memory_mb" {
  type    = number
  default = 512
}

variable "max_concurrency" {
  type        = number
  default     = 10
  description = "Hard ceiling on simultaneous executions, so neither AWS nor CockroachDB can be billed by a runaway loop."
}

variable "log_retention_days" {
  type        = number
  default     = 7
  description = "CloudWatch charges for ingestion and storage. Seven days is enough to debug a deploy and cheap enough to ignore."
}
