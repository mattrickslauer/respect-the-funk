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

# Sign-in. Off by default so applying this alone changes nothing about a live console.
# To turn it on, in this order:
#
#   1. aws ssm put-parameter --name /remixkit/prod/SESSION_SECRET  --type SecureString \
#        --value "$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" --overwrite
#   2. aws ssm put-parameter --name /remixkit/prod/ZEPTOMAIL_TOKEN --type SecureString \
#        --value "…" --overwrite
#   3. set auth_backend = "otp", mail_backend = "zeptomail", and allowed_emails here.
#
# Secrets first: a console that turns on auth before it can sign or send is one nobody
# can enter, including whoever needs to fix it.
variable "auth_backend" {
  type    = string
  default = "none"
}

variable "mail_backend" {
  type    = string
  default = "console"
}

variable "mail_from" {
  type = string
  # A verified ZeptoMail sender on a domain with SPF/DKIM already published. A sign-in
  # code from an unverified domain is silently spam-filed, which is indistinguishable
  # from a broken login.
  default = "rtp@agfarms.dev"
}

variable "allowed_emails" {
  type        = list(string)
  default     = []
  description = "Addresses permitted to sign in. This is the user table."
}
