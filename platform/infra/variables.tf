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
  type    = number
  default = -1

  # -1 means "no reservation", which is the only value this account can accept.
  #
  # The intent was a hard per-function ceiling so a runaway loop could not bill AWS
  # and CockroachDB unbounded. It cannot be had here: this account's total Lambda
  # concurrency is 10 (AWS's reduced default; the usual limit is 1000), and AWS
  # refuses any reservation that leaves fewer than 10 unreserved. Reserving even 1
  # is therefore rejected.
  #
  # The protection is not lost, only relocated — the account limit of 10 is itself a
  # stricter ceiling than the 10 this variable was trying to impose. What *is* lost
  # is isolation: this console shares those 10 with the other functions in the
  # account, RemixKit's included. At the console's traffic that is not a real risk,
  # but it is the reason to raise the account quota before anything here gets busy.
  description = "Reserved concurrency. -1 leaves the function unreserved, sharing the account pool."
}

variable "log_retention_days" {
  type        = number
  default     = 7
  description = "CloudWatch charges for ingestion and storage. Seven days is enough to debug a deploy and cheap enough to ignore."
}

variable "classifier_image_uri" {
  type    = string
  default = ""

  # Empty by default, and the classifier Lambda is `count = 0` while it is empty.
  #
  # That gate exists because a container-image Lambda cannot be created before the image
  # is pushed — Terraform would fail on an ECR repository it had just created and that
  # contains nothing. The ECR repository itself is always created, because it has to
  # exist before anything can be pushed to it. So the order is: apply (repo appears),
  # push the image, set this to the digest URI, apply again.
  #
  # Use the @sha256 digest form rather than a :tag. Lambda resolves a tag once at create
  # time and never again, so re-pushing :latest leaves the function silently running the
  # old image while every console and plan says it is current.
  description = "Digest URI of the pushed classifier image. Empty leaves the classifier undeployed; the console works without it and says so."
}
