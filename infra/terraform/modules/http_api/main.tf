# API Gateway HTTP API — the front door, because Function URLs are blocked here.
#
# infra/README §"Deliberately not here" rules out API Gateway, and the reasoning was
# sound: "Function URLs are free and sufficient". That reasoning has a precondition
# this account does not currently meet.
#
# What we measured, rather than assumed: a Function URL with `AuthType: NONE` and
# AWS's own canonical `FunctionURLAllowPublicAccess` statement returns
# `403 AccessDeniedException` — and so does a trivial hello-world Lambda created the
# same way in the same account. The block is account-level, not configuration. The
# account also reports `ConcurrentExecutions: 10` against a default of 1000, which is
# the signature of a new account still under review.
#
# So this is a *workaround for an account state*, not a revision of the architecture.
# HTTP API keeps the properties the Function URL was chosen for — no idle floor, no
# minimum capacity, ~$1.00/million requests — and costs about ten lines. When the
# account restriction lifts, delete this module and the Function URL still works.
#
# HTTP API, not REST API: roughly a third of the price, payload format 2.0 is what the
# Lambda Web Adapter already expects, and none of REST's extra features are wanted.

variable "name_prefix" { type = string }
variable "lambda_arn" { type = string }
variable "lambda_name" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name_prefix}-http"
  protocol_type = "HTTP"
  description   = "RemixKit artist console. No authentication — see remixkit/auth/."

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["*"]
    allow_headers = ["*"]
  }

  tags = var.tags
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id           = aws_apigatewayv2_api.this.id
  integration_type = "AWS_PROXY"
  integration_uri  = var.lambda_arn
  # 2.0 is what the Lambda Web Adapter parses, and it is what the Function URL sends —
  # so the application sees an identical event shape either way and cannot tell which
  # front door a request arrived through.
  payload_format_version = "2.0"
  # Below the Lambda's own 30s ceiling, so the gateway is never the thing that gives up
  # first. A slow handler should surface as the app's timeout, not the gateway's.
  timeout_milliseconds = 29000
}

# $default catches every method and path — routing is FastAPI's job, and enumerating
# routes here would be a second place to keep them in sync.
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowInvokeFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_name
  principal     = "apigateway.amazonaws.com"
  # Scoped to this API. Without the source ARN any API in any account could invoke it.
  source_arn = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

output "api_url" { value = aws_apigatewayv2_stage.default.invoke_url }
output "api_id" { value = aws_apigatewayv2_api.this.id }
