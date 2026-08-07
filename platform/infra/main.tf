# The platform console, as cheaply as AWS can be made to run a web app.
#
# Idle cost: $0.00. Not "under a dollar" — zero. Every line below is chosen to
# keep it there, and each omission is deliberate rather than forgotten:
#
#   * No API Gateway. A Lambda Function URL does the same job for a server-rendered
#     app and costs nothing per request. API Gateway is ~$1.00/million on top of
#     Lambda for routing we are not using.
#   * No VPC, and therefore no NAT gateway. A NAT is ~$32/month of pure idle floor,
#     which is more than everything else here combined, forever, whether or not
#     anyone visits. The Lambda reaches CockroachDB over the public internet with
#     verify-full TLS, which is how CockroachDB Cloud expects to be reached anyway.
#     (infra/README.md makes this same argument for the RemixKit stack.)
#   * No Secrets Manager. $0.40/secret/month for two secrets, when Lambda
#     environment variables are already encrypted at rest with an AWS-managed KMS
#     key at no charge. Revisit when something needs rotation without a deploy.
#   * No ECR. A zip deployment avoids per-GB image storage entirely.
#   * No CloudFront, no custom domain, no ACM. The Function URL is a working HTTPS
#     endpoint. A domain is a $12/year decision to take when there is something
#     worth pointing it at.
#
# What does cost money, eventually: Lambda invocations beyond the free tier
# (1M requests + 400k GB-seconds per month, which this will not approach), and
# CloudWatch log ingestion, capped below by a 7-day retention.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }

  # Local state by default so a first `apply` needs nothing but credentials.
  # Uncomment before a second person ever runs this — two people applying against
  # local state is how infrastructure gets destroyed by accident.
  #
  # backend "s3" {
  #   bucket = "rtf-platform-tfstate"
  #   key    = "prod/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.region

  # Every taggable resource this stack creates carries these, without any
  # resource having to remember to. This is what makes the platform's spend
  # separable from RemixKit's in Cost Explorer — group by the Project tag.
  default_tags {
    tags = {
      Project   = "rtf-platform"
      Env       = var.env
      ManagedBy = "terraform"
      Repo      = "respect-the-funk"
      Component = "console"
    }
  }
}

locals {
  name = "rtf-platform-${var.env}"
}

# ------------------------------------------------------------------ the bundle
# Built by ./build.sh, which vendors dependencies for arm64 before this runs.

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = "${path.module}/build"
  output_path = "${path.module}/.terraform-tmp/console.zip"
}

# -------------------------------------------------------------------- identity

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "console" {
  name               = "${local.name}-console"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Logs and nothing else. The console talks to CockroachDB, not to AWS services,
# so there is no S3, no SQS and no Bedrock grant to make here yet.
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.console.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --------------------------------------------------------------------- compute

# Created explicitly rather than left to Lambda's implicit creation, because an
# implicitly created log group retains forever and log retention is the one line
# item here that grows without anyone deciding it should.
resource "aws_cloudwatch_log_group" "console" {
  name              = "/aws/lambda/${local.name}-console"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "console" {
  function_name = "${local.name}-console"
  role          = aws_iam_role.console.arn
  handler       = "rtf_platform.handler.handler"

  # Must stay in lockstep with build.sh's --python-version. Wheels fetched for one
  # minor version and run on another is the failure mode this pairing prevents.
  # Note the dev machine runs 3.14, which Lambda does not offer — so anything
  # version-sensitive is only truly proven once it runs in the deployed function.
  runtime = "python3.13"

  # Graviton: ~20% cheaper per GB-second than x86 for identical work.
  architectures = ["arm64"]

  # 512MB is not about memory. Lambda scales CPU with memory, and a cold start
  # that imports FastAPI is CPU-bound — 512 measurably beats 256 on wall-clock
  # for less money, because it finishes proportionally sooner.
  memory_size = var.memory_mb
  timeout     = 15

  filename         = data.archive_file.bundle.output_path
  source_code_hash = data.archive_file.bundle.output_base64sha256

  # -1 by default: unreserved. See variables.tf for why a reservation is not
  # available on this account, and why the account's own 10-execution limit is a
  # stricter ceiling than the one this was trying to set.
  reserved_concurrent_executions = var.max_concurrency

  environment {
    variables = {
      DATABASE_URL         = var.database_url
      PLATFORM_ADMIN_TOKEN = var.admin_token
      PLATFORM_TENANT_SLUG = var.tenant_slug
    }
  }

  depends_on = [aws_cloudwatch_log_group.console]
}

# ------------------------------------------------------------------------- URL

# authorization_type NONE because the app authenticates its own callers: anonymous
# principals may read, and writes require the operator cookie. IAM auth here would
# make the console unreachable by a browser, which defeats the point of a demo URL.
resource "aws_lambda_function_url" "console" {
  function_name      = aws_lambda_function.console.function_name
  authorization_type = "NONE"
}
