variable "region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region. B2 egress is billed regardless of this, so pick for latency."
}

variable "env" {
  type    = string
  default = "prod"
}

variable "tenant_id" {
  type        = string
  default     = "respect-the-funk"
  description = "Tenant #1 — the label that dogfoods the product. Partition key everywhere."
}

variable "b2_bucket" {
  type        = string
  description = "Backblaze B2 bucket holding artists, identities, songs, masters, kits, manifests."
}

variable "b2_region" {
  type        = string
  default     = ""
  description = "B2 S3 endpoint region, e.g. us-west-000. Confirm with b2_authorize_account; the default guess is wrong for many buckets."
}

variable "api_image_tag" {
  type    = string
  default = "api-latest"
}

variable "worker_image_tag" {
  type    = string
  default = "worker-latest"
}

# Backend selection. Defaults are production; override in terraform.tfvars to bring a
# console up before credentials exist. See modules/api for the reasoning.
variable "storage_backend" {
  type    = string
  default = "b2"
}

variable "generator_backend" {
  type    = string
  default = "genblaze"
}

variable "queue_backend" {
  type    = string
  default = "sqs"
}
