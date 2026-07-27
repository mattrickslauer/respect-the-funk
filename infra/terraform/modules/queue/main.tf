# SQS: one job queue and one dead-letter queue.
#
# infra/README ②. Generation is minutes; it can never be a request (BUILD-SPEC §2b
# rule 3). Pay-per-request, 1M/month free — no idle floor.

variable "name_prefix" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

variable "visibility_timeout_seconds" {
  type = number
  # Must exceed the longest a kit can take, or SQS redelivers a message that is still
  # being worked and a second Batch job starts on the same kit. Generation is capped
  # at RK_GENERATION_TIMEOUT_S (900s default); this leaves headroom for the container
  # start and the upload afterwards.
  default     = 1200
  description = "Must exceed the worst-case kit duration, including container start."
}

variable "max_receive_count" {
  type = number
  # Three attempts, then the DLQ. Jobs are idempotent (§2b rule 4), so a retry is safe;
  # what a retry cannot fix is a malformed message or a permanently failing provider,
  # and those should stop costing money rather than loop.
  default = 3
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name_prefix}-kits-dlq"
  message_retention_seconds = 1209600 # 14 days — long enough to notice and inspect
  tags                      = var.tags
}

resource "aws_sqs_queue" "kits" {
  name                       = "${var.name_prefix}-kits"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = 345600 # 4 days
  # Long polling: fewer empty receives, and the worker's drain loop blocks instead of
  # spinning.
  receive_wait_time_seconds = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = var.tags
}

output "queue_url" { value = aws_sqs_queue.kits.url }
output "queue_arn" { value = aws_sqs_queue.kits.arn }
output "dlq_arn" { value = aws_sqs_queue.dlq.arn }
output "dlq_url" { value = aws_sqs_queue.dlq.url }
