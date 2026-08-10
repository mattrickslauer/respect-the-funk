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

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.console.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# The console signs presigned URLs, and a presigned URL carries the *signer's*
# authority — it is not a capability the bucket grants to the URL holder. So the
# permissions below are exactly what an upload and a playback need, and the browser
# never holds credentials of its own.
#
# s3:ListBucket is here for a reason that is not listing. Without it, S3 answers a
# HEAD for a missing key with 403 instead of 404, because it will not confirm or deny
# the existence of an object to a principal that cannot enumerate the bucket. That
# would make `storage.head` unable to distinguish "the upload never finished" from
# "the IAM policy is wrong" — a misconfiguration reported to operators as their own
# mistake, forever. See that function's docstring.
data "aws_iam_policy_document" "masters" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.masters.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.masters.arn]
  }
}

resource "aws_iam_role_policy" "masters" {
  name   = "${local.name}-masters"
  role   = aws_iam_role.console.id
  policy = data.aws_iam_policy_document.masters.json
}

# ---------------------------------------------------------------------- masters
#
# The first resource in this stack that is not free at idle. Everything above is
# priced per invocation and costs nothing when nobody visits; a bucket is priced per
# GB-month and costs the same whether or not anyone ever plays a master. At
# $0.023/GB/month, the current roster — two albums — is a rounding error, and it is
# still worth naming the change of kind rather than letting the first non-zero line
# on a bill be a surprise.
#
# S3 is also the second qualifying AWS service in the CockroachDB × AWS hackathon
# submission, where today there is only Lambda. That is a consequence and not the
# reason: masters need somewhere to live regardless.

resource "aws_s3_bucket" "masters" {
  # Bucket names are globally unique across every AWS account, so this can collide
  # with a stranger's. That surfaces as `BucketAlreadyExists` at apply time — loud,
  # immediate, and fixed by changing `env` or this suffix. An account-id suffix would
  # prevent it at the cost of an unreadable name in every console listing and log
  # line; a failure this legible does not need preventing.
  bucket = "${local.name}-masters"

  tags = { Component = "masters" }
}

# Masters are unreleased audio. Nothing about this bucket is public, and the account
# level setting is not assumed to be on — this is per-bucket and explicit, because a
# public masters bucket is the single worst outcome available here and it is usually
# reached by inheriting a default rather than by anyone choosing it.
resource "aws_s3_bucket_public_access_block" "masters" {
  bucket                  = aws_s3_bucket.masters.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 is applied by default on new buckets, which is exactly why it is written out
# here: a default is a thing that can change, and `terraform plan` showing a drift on
# an explicit resource is how that would be noticed. SSE-KMS would add $1/month for
# the key plus a request charge, and buys nothing this threat model needs — the
# objects are already unreachable without a signature from the console's role.
resource "aws_s3_bucket_server_side_encryption_configuration" "masters" {
  bucket = aws_s3_bucket.masters.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning, because the delete path here is genuinely lossy in a way nothing else
# in this stack is. `assets.delete` removes a row and hands the caller an object key;
# a mistaken click deletes the only copy of a master the label may not have elsewhere.
# Noncurrent versions expire after 30 days (below), so this buys a month of undo
# rather than an unbounded archive.
resource "aws_s3_bucket_versioning" "masters" {
  bucket = aws_s3_bucket.masters.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "masters" {
  bucket = aws_s3_bucket.masters.id

  # The rule that pays for itself. A browser PUT of a 90MB master that dies partway
  # leaves an incomplete multipart upload: invisible in every listing, and billed for
  # its parts indefinitely. This is the single most common way an S3 bill grows
  # without a corresponding object, and seven days is longer than any upload here.
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # The undo window described above. Thirty days of noncurrent versions, then gone —
  # long enough that a deletion noticed at the next monthly statement is still
  # recoverable, short enough that superseded masters do not accumulate forever.
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  depends_on = [aws_s3_bucket_versioning.masters]
}

# CORS, without which the whole presigned design silently does not work in a browser.
#
# The PUT goes from the page to `bucket.s3.amazonaws.com`, which is a different origin
# from the Function URL. The browser sends a preflight OPTIONS first, and S3 answers
# it from this configuration and not from the presigned signature — so a correctly
# signed URL fails with an opaque CORS error that names neither S3 nor the missing
# rule. It reads like broken JavaScript.
#
# `ETag` is exposed because it is the one response header the uploader reads back.
resource "aws_s3_bucket_cors_configuration" "masters" {
  bucket = aws_s3_bucket.masters.id

  cors_rule {
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = [
      # The console itself. `function_url` carries a trailing slash and an Origin
      # header never does, so an untrimmed value matches nothing at all.
      trimsuffix(aws_lambda_function_url.console.function_url, "/"),
      # `dev.sh`, so the upload path can be exercised before it is deployed. This is
      # the one origin here that is not ours, and it is bounded: an attacker who can
      # serve from a victim's localhost:8000 already has the machine.
      "http://localhost:8000",
    ]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
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
      # Empty is a legitimate state in a checkout that has never applied this stack,
      # and `settings.storage_configured` reports it. Here it is always set, so the
      # console offers uploads. AWS_REGION is provided by the runtime itself, which
      # is why `settings.load` reads it and no region is passed here.
      PLATFORM_MASTERS_BUCKET = aws_s3_bucket.masters.bucket
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

# authorization_type = "NONE" on the URL is necessary but NOT sufficient: it says
# "do not require SigV4", while the function's resource policy still has to allow
# the call. Without this the URL answers every request with
# {"Message":"Forbidden"}, which reads like a bug in the app and is not.
#
# `principal = "*"` is what public means here, and public is the intent — the
# console authenticates its own callers (anonymous reads, operator-token writes),
# so the authorization that matters happens in the app, not at the edge.
resource "aws_lambda_permission" "public_url" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.console.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# BOTH permissions are required, and this is the one every guide omits. With only
# the InvokeFunctionUrl grant above, the URL answers {"Message":"Forbidden"} with
# x-amzn-ErrorType: AccessDeniedException and the function is never invoked — no
# log stream is even created, so it reads like a networking fault rather than an
# authorization one. Verified by comparing against two working function URLs in
# this account: both carry this second statement.
#
# The narrower condition those two use — Bool lambda:InvokedViaFunctionUrl = true —
# is not expressible here: the AddPermission API rejects function_url_auth_type on
# the InvokeFunction action, and aws_lambda_permission exposes no arbitrary
# condition. The exposure that leaves is bounded: this function is deliberately
# public at the URL already, and all real authorization is in the app (anonymous
# reads, operator-token writes). Narrow it if that ever stops being true.
resource "aws_lambda_permission" "public_invoke" {
  statement_id  = "AllowInvokeViaFunctionUrl"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.console.function_name
  principal     = "*"
}
