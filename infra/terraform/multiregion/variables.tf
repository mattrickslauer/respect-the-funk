# Every variable that can be wrong in a way that costs money or leaks personal data has
# a validation block. The ones that can only be wrong in a way that is obvious at apply
# time do not.

variable "cluster_name" {
  type        = string
  description = <<-EOT
    Name of the ephemeral demo cluster. Must NOT be `respect-the-funk`: that is the live
    Basic cluster holding the system of record, it is not managed by this module, and a
    name collision here is the one mistake in this file that cannot be undone.
  EOT
  default     = "rtf-residency-demo"

  validation {
    condition     = var.cluster_name != "respect-the-funk"
    error_message = "Refusing: `respect-the-funk` is the live cluster and is not managed by this module. See import.tf.example if you genuinely mean to convert it."
  }
}

# ---------------------------------------------------------------- the three regions
#
# These are AWS region codes as CockroachDB Cloud names them at the *cluster* level
# (`us-east-1`). SQL names them differently — `SHOW REGIONS FROM CLUSTER` returns
# `aws-us-east-1`, with the cloud provider prefixed — and migration 024 uses the SQL
# form. The two spellings are the same place and the mismatch is a genuine trap, so both
# are written out in outputs.tf rather than left to be remembered.

variable "primary_region" {
  type        = string
  description = "Primary region. us-east-1 because that is where the live cluster is and where the roster and every US radio licensee already sit."
  default     = "us-east-1"
}

variable "eu_region" {
  type        = string
  description = <<-EOT
    Where rows for data subjects in the EEA, the UK and Switzerland are domiciled.
    eu-west-1 is Ireland: inside the EEA, so a transfer to it is not a transfer under
    Chapter V of the GDPR at all, which is a stronger position than any adequacy
    decision or set of standard contractual clauses can give.
  EOT
  default     = "eu-west-1"
}

variable "apac_region" {
  type        = string
  description = <<-EOT
    ap-southeast-1 is Singapore. It is the residency home for the APAC jurisdictions
    enumerated in migration 024 — and, importantly, NOT for mainland China or Russia,
    whose localisation laws Singapore does not satisfy. 024 leaves those unmapped so a
    write fails loudly rather than landing in the wrong country.
  EOT
  default     = "ap-southeast-1"
}

variable "provisioned_vcpus" {
  type        = number
  description = "Cluster-wide provisioned vCPUs. Billed by the hour whether idle or not — this is the entire compute cost of the demo."
  default     = 2

  validation {
    condition     = var.provisioned_vcpus <= 4
    error_message = "Refusing: the project budget is $10 total and Standard bills provisioned vCPUs hourly. Raise this only with a number in front of you."
  }
}

variable "storage_mib_limit" {
  type        = number
  description = "Hard storage ceiling in MiB. Remember that SURVIVE REGION FAILURE stores 5 replicas, not 3, so billable storage is ~1.67x the logical size."
  default     = 10240
}

# ---------------------------------------------------------------- access
#
# No defaults. A default here would be either 0.0.0.0/0 — a public front door on a
# database of contact details for named individuals — or somebody's home IP from months
# ago, silently letting the wrong machine in and locking the right one out. Both are the
# kind of quiet wrong answer this repo does not ship, so Terraform prompts instead.

variable "operator_cidr_ip" {
  type        = string
  description = "Your public IPv4 address, e.g. from `curl -s https://checkip.amazonaws.com`. This is the only address that may reach the cluster."

  validation {
    condition     = var.operator_cidr_ip != "0.0.0.0"
    error_message = "Refusing: 0.0.0.0/0 on a cluster holding contact_route means every address on the internet can reach personal data for named people."
  }
}

variable "operator_cidr_mask" {
  type        = number
  description = "Mask for operator_cidr_ip. 32 for a single address."
  default     = 32

  validation {
    condition     = var.operator_cidr_mask >= 24
    error_message = "Refusing: a mask below /24 is a range, not an operator. Narrow it."
  }
}

variable "sql_user_name" {
  type        = string
  description = "SQL user the migration runner and the console connect as."
  default     = "rtf_app"
}

variable "sql_user_password" {
  type        = string
  sensitive   = true
  description = <<-EOT
    Password for sql_user_name. Sensitive, and still written to local state in plaintext
    — Terraform has no way not to. That is survivable only because this cluster and this
    password both live for one afternoon; do not reuse a password from anywhere else,
    and delete terraform.tfstate with the cluster.
  EOT
}
