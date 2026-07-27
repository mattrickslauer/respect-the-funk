# The generator: AWS Batch on Fargate Spot.
#
# infra/README ③ and caveat #1 — the one place the obvious GCP→AWS mapping is actively
# wrong. BUILD-SPEC §4 calls Genblaze with `timeout=900`, which is *exactly* Lambda's
# 15-minute ceiling with no headroom for cold start, provider retries, or the upload
# afterwards. It would fail by silent timeout on the most expensive path in the system.
# Batch has no such limit and still sits at zero via `minvCpus: 0`.
#
# Fargate Spot is safe here only because jobs are idempotent (§2b rule 4): a Spot
# reclaim mid-render costs wall-clock and nothing else. Relax that rule and this must
# become on-demand.

variable "name_prefix" { type = string }
variable "env" { type = string }
variable "image_uri" { type = string }
variable "queue_url" { type = string }
variable "queue_arn" { type = string }
variable "ssm_path" { type = string }
variable "b2_bucket" { type = string }
variable "tenant_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "tags" {
  type    = map(string)
  default = {}
}

variable "max_vcpus" {
  type    = number
  default = 16
}

variable "job_vcpu" {
  type    = string
  default = "2"
}

variable "job_memory_mb" {
  type    = string
  default = "4096"
}

variable "job_attempts" {
  type = number
  # Two attempts. Idempotency makes a retry cheap, and Spot reclaim is the expected
  # reason for one. Beyond that, a failing kit should stop rather than keep paying
  # providers — the SQS DLQ is where it lands.
  default = 2
}

variable "log_retention_days" {
  type    = number
  default = 14
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------- iam
data "aws_iam_policy_document" "batch_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["batch.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "batch_service" {
  name               = "${var.name_prefix}-batch-service"
  assume_role_policy = data.aws_iam_policy_document.batch_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "batch_service" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Pulls the image and writes logs — infrastructure concerns, not the app's.
resource "aws_iam_role" "execution" {
  name               = "${var.name_prefix}-worker-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# What the running container may do.
resource "aws_iam_role" "job" {
  name               = "${var.name_prefix}-worker-job"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "job" {
  # Consume and delete — the mirror of the API's send-only grant.
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [var.queue_arn]
  }

  statement {
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.ssm_path}/*"
    ]
  }

  statement {
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.name}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "${var.name_prefix}-worker-job"
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

# ---------------------------------------------------------------- batch
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/batch/${var.name_prefix}-worker"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_batch_compute_environment" "this" {
  # `compute_environment_name`, not `name` — the AWS provider v5 argument.
  compute_environment_name = "${var.name_prefix}-generator"
  type                     = "MANAGED"
  service_role             = aws_iam_role.batch_service.arn

  compute_resources {
    type = "FARGATE_SPOT"
    # The claim, in one line: no compute floor. Nothing runs, nothing is billed.
    min_vcpus          = 0
    max_vcpus          = var.max_vcpus
    subnets            = var.subnet_ids
    security_group_ids = var.security_group_ids
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_batch_job_queue" "this" {
  name     = "${var.name_prefix}-generator"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.this.arn
  }

  tags = var.tags
}

resource "aws_batch_job_definition" "this" {
  name                  = "${var.name_prefix}-generator"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  retry_strategy {
    attempts = var.job_attempts
  }

  timeout {
    # A ceiling above the app's own RK_GENERATION_TIMEOUT_S, so the application times
    # out and records the failure on the kit before Batch kills the container out from
    # under it. A kit that fails with a reason is worth more than one that vanishes.
    attempt_duration_seconds = 1800
  }

  container_properties = jsonencode({
    image            = var.image_uri
    command          = ["--poll"]
    jobRoleArn       = aws_iam_role.job.arn
    executionRoleArn = aws_iam_role.execution.arn

    resourceRequirements = [
      { type = "VCPU", value = var.job_vcpu },
      { type = "MEMORY", value = var.job_memory_mb },
    ]

    networkConfiguration = {
      # Public subnets with a public IP, deliberately: a private subnet would need a
      # NAT gateway to reach B2 and the providers, and a NAT is ~$32/month of pure
      # idle floor — which would defeat the premise on its own (infra/README).
      assignPublicIp = "ENABLED"
    }

    fargatePlatformConfiguration = {
      platformVersion = "LATEST"
    }

    environment = [
      { name = "RK_ENV", value = var.env },
      { name = "RK_STORAGE_BACKEND", value = "b2" },
      { name = "RK_GENERATOR_BACKEND", value = "genblaze" },
      { name = "RK_QUEUE_BACKEND", value = "sqs" },
      { name = "RK_SQS_QUEUE_URL", value = var.queue_url },
      { name = "RK_B2_BUCKET", value = var.b2_bucket },
      { name = "RK_DEFAULT_TENANT_ID", value = var.tenant_id },
      { name = "RK_AWS_REGION", value = data.aws_region.current.name },
      { name = "RK_SSM_PATH", value = var.ssm_path },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "worker"
      }
    }
  })

  tags = var.tags
}

output "job_queue_arn" { value = aws_batch_job_queue.this.arn }
output "job_definition_arn" { value = aws_batch_job_definition.this.arn }
output "job_role_arn" { value = aws_iam_role.job.arn }
