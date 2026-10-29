# Freshness check for every customer (task 9.2 code, task 9.3 deploy).
# Deploy only after the operator confirms that specific apply.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.4" }
  }
}

variable "name_prefix" {
  type    = string
  default = "kiro-mon"
}

variable "config" {
  description = "Rendered into the Lambda CONFIG variable. Shape: see lambda/freshness.py docstring."
  type = object({
    dc_bucket          = string
    collector_function = string
    cur_bucket         = string
    customers = list(object({
      slug                  = string
      kiro_account          = string
      report_bucket         = string
      sample_results_bucket = optional(string)
      trail                 = optional(object({ bucket = string, region = string }))
      promptmeta_bucket     = optional(string)
      cur                   = optional(object({ prefix = string, created = string }))
    }))
  })

  validation {
    condition     = alltrue([for c in var.config.customers : can(regex("^[0-9]{12}$", c.kiro_account))])
    error_message = "Every customers[].kiro_account must be 12 digits."
  }
}

variable "alert_emails" {
  type    = list(string)
  default = []
}

variable "schedule" {
  type    = string
  default = "cron(0 5 * * ? *)"
}

variable "permissions_boundary_arn" {
  description = "Optional IAM permissions boundary for the two roles, when the account requires one."
  type        = string
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "dry_run" {
  description = "true allows event.now to simulate a date (task 9.3 test). Keep false in steady state."
  type        = bool
  default     = false
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  fn_name = "${var.name_prefix}-freshness"
  buckets = distinct(compact(concat(
    [var.config.dc_bucket, var.config.cur_bucket],
    [for c in var.config.customers : c.sample_results_bucket],
    [for c in var.config.customers : c.promptmeta_bucket],
    [for c in var.config.customers : try(c.trail.bucket, null)],
  )))
  arn_prefix    = "arn:${data.aws_partition.current.partition}"
  account_id    = data.aws_caller_identity.current.account_id
  region        = data.aws_region.current.region
  collector_log = "${local.arn_prefix}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${var.config.collector_function}:*"
  own_log       = "${local.arn_prefix}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.fn_name}:*"
  tags          = { Project = "kiro-monitoring", Component = "freshness" }
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.name_prefix}-alerts"
  kms_master_key_id = "alias/aws/sns"
  tags              = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = toset(var.alert_emails)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

data "archive_file" "code" {
  type        = "zip"
  source_file = "${path.module}/lambda/freshness.py"
  output_path = "${path.module}/.build/freshness.zip"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "ListMonitoredBuckets"
    actions   = ["s3:ListBucket"]
    resources = [for b in local.buckets : "${local.arn_prefix}:s3:::${b}"]
  }
  statement {
    sid       = "ReadCollectorResultLines"
    actions   = ["logs:FilterLogEvents"]
    resources = [local.collector_log]
  }
  statement {
    sid       = "ReadLambdaErrorMetric"
    actions   = ["cloudwatch:GetMetricStatistics"]
    resources = ["*"] # GetMetricStatistics does not support resource-level permissions
  }
  statement {
    sid       = "PublishAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
  statement {
    sid       = "OwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.own_log]
  }
}

resource "aws_iam_role" "lambda" {
  name                 = "${local.fn_name}-lambda"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
  tags                 = local.tags
}

resource "aws_iam_role_policy" "lambda" {
  name   = "freshness-read-and-alert"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "fn" {
  name              = "/aws/lambda/${local.fn_name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_lambda_function" "freshness" {
  function_name    = local.fn_name
  description      = "Daily freshness check of Kiro monitoring data - counts only, no content"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "freshness.handler"
  filename         = data.archive_file.code.output_path
  source_code_hash = data.archive_file.code.output_base64sha256
  timeout          = 120
  memory_size      = 256

  environment {
    variables = {
      CONFIG    = jsonencode(var.config)
      TOPIC_ARN = aws_sns_topic.alerts.arn
      DRY_RUN   = var.dry_run ? "1" : "0"
    }
  }

  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.lambda]
  tags       = local.tags
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name                 = "${local.fn_name}-scheduler"
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  permissions_boundary = var.permissions_boundary_arn
  tags                 = local.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-freshness"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.freshness.arn }]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name                         = local.fn_name
  schedule_expression          = var.schedule
  schedule_expression_timezone = "UTC"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_function.freshness.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})
  }
}

output "topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "function_name" {
  value = aws_lambda_function.freshness.function_name
}
