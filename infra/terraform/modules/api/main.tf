# The web tier: Lambda from a container image, fronted by a Function URL.
#
# infra/README ①. No API Gateway — Function URLs are free and sufficient, and a
# gateway in front of a single-service app is a second thing to configure for no
# benefit. No provisioned concurrency, so idle is genuinely $0.
#
# This Lambda serves JSON and HTML only. It never touches asset bytes: uploads and
# downloads are presigned browser↔B2 (§2b rule 2), which is also why a 6 MB Lambda
# response limit is not a constraint here.

variable "name_prefix" { type = string }
variable "env" { type = string }
variable "image_uri" { type = string }
variable "queue_url" { type = string }
variable "queue_arn" { type = string }
variable "ssm_path" { type = string }
variable "b2_bucket" { type = string }
variable "tenant_id" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

variable "memory_mb" {
  type = number
  # Lambda scales CPU with memory. 1024 MB is roughly one vCPU — enough that template
  # rendering and JSON work are not artificially slow, while staying inside the free
  # tier's GB-seconds for a demo's traffic.
  default = 1024
}

variable "timeout_seconds" {
  type = number
  # 30s, not 900. Nothing slow runs here — that is the entire reason the generator is
  # Batch. A handler approaching this timeout is a bug, and a short ceiling surfaces
  # it instead of hiding it behind a 15-minute wait.
  default = 30
}

variable "log_retention_days" {
  type    = number
  default = 14 # infra/README: logs at 14-day retention, ~cents
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------- iam
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "app" {
  # Enqueue only. The API can start a kit; it cannot consume the queue, because
  # consuming is the worker's job and a web tier that could would be one bug away
  # from running a 15-minute generation inside a 30-second handler.
  statement {
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
    resources = [var.queue_arn]
  }

  # Read its own secrets, and nothing else's — scoped to this env's path.
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

resource "aws_iam_role_policy" "app" {
  name   = "${var.name_prefix}-api"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.app.json
}

# ---------------------------------------------------------------- function
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name_prefix}-api"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "this" {
  function_name = "${var.name_prefix}-api"
  role          = aws_iam_role.this.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = var.memory_mb
  timeout       = var.timeout_seconds

  environment {
    variables = {
      RK_ENV               = var.env
      RK_STORAGE_BACKEND   = "b2"
      RK_GENERATOR_BACKEND = "genblaze"
      RK_QUEUE_BACKEND     = "sqs"
      RK_AUTH_BACKEND      = "none" # deliberate: there is no auth yet
      RK_SQS_QUEUE_URL     = var.queue_url
      RK_B2_BUCKET         = var.b2_bucket
      RK_DEFAULT_TENANT_ID = var.tenant_id
      RK_AWS_REGION        = data.aws_region.current.name
      RK_SSM_PATH          = var.ssm_path
      AWS_LWA_INVOKE_MODE  = "buffered"
    }
  }

  depends_on = [aws_cloudwatch_log_group.this]
  tags       = var.tags
}

resource "aws_lambda_function_url" "this" {
  function_name = aws_lambda_function.this.function_name
  # NONE because the application has no authentication today — that is the current
  # instruction, recorded here rather than left implicit. When auth lands this becomes
  # the second gate (AWS_IAM, or an authorizer), and `remixkit/auth/` becomes the first.
  authorization_type = "NONE"

  cors {
    allow_origins = ["*"]
    allow_methods = ["*"]
    allow_headers = ["*"]
  }
}

output "function_url" { value = aws_lambda_function_url.this.function_url }
output "function_name" { value = aws_lambda_function.this.function_name }
output "role_arn" { value = aws_iam_role.this.arn }
