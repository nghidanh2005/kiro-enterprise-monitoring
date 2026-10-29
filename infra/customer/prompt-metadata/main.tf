# Prompt-log metadata extractor in the customer's Kiro account (task 7.5, only when D4 = yes).
# Reads full text (it must, to measure lengths) and writes only the allow-list
# of design.md 3.5 to the observability account. Provider: customer Kiro account.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.4" }
  }
}

variable "slug" {
  type = string
}

variable "prompt_bucket_name" {
  description = "Output bucket_name of infra/customer/prompt-logs."
  type        = string
}

variable "prompt_prefix" {
  type    = string
  default = "kiro-prompt-logs/"
}

variable "prompt_kms_key_arn" {
  description = "Output kms_key_arn of infra/customer/prompt-logs. Its key policy delegates to IAM, so the role policy below is enough."
  type        = string
}

variable "destination_bucket" {
  description = "Output bucket_name of infra/observability/prompt-metadata."
  type        = string
}

variable "observability_account_id" {
  type = string
}

variable "permissions_boundary_arn" {
  type    = string
  default = null
}

variable "log_retention_days" {
  type    = number
  default = 30
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  p          = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  fn_name    = "kiro-mon-promptmeta-${var.slug}"
  tags       = { Project = "kiro-monitoring", Customer = var.slug }

  trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "ReadPromptLogs", Effect = "Allow", Action = "s3:GetObject", Resource = "arn:${local.p}:s3:::${var.prompt_bucket_name}/${var.prompt_prefix}*" },
      { Sid = "DecryptPromptLogs", Effect = "Allow", Action = "kms:Decrypt", Resource = var.prompt_kms_key_arn },
      {
        Sid      = "WriteMetadataOnly"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "arn:${local.p}:s3:::${var.destination_bucket}/promptmeta/account_id=${local.account_id}/*"
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:${local.p}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.fn_name}:*"
      },
    ]
  })
}

data "archive_file" "code" {
  type        = "zip"
  source_file = "${path.module}/lambda/promptmeta.py"
  output_path = "${path.module}/.build/promptmeta.zip"
}

resource "aws_iam_role" "this" {
  name                 = local.fn_name
  assume_role_policy   = local.trust
  permissions_boundary = var.permissions_boundary_arn
  tags                 = local.tags
}

resource "aws_iam_role_policy" "this" {
  name   = "read-prompt-write-metadata"
  role   = aws_iam_role.this.id
  policy = local.policy
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${local.fn_name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_lambda_function" "this" {
  function_name    = local.fn_name
  description      = "Kiro prompt-log metadata - allow-listed fields only, no content"
  role             = aws_iam_role.this.arn
  runtime          = "python3.12"
  handler          = "promptmeta.handler"
  filename         = data.archive_file.code.output_path
  source_code_hash = data.archive_file.code.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      DEST_BUCKET       = var.destination_bucket
      SOURCE_ACCOUNT_ID = local.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]
  tags       = local.tags

  lifecycle {
    precondition {
      condition     = endswith(var.destination_bucket, "-${var.observability_account_id}")
      error_message = "destination_bucket must be the observability account's metadata bucket."
    }
  }
}

resource "aws_lambda_permission" "s3" {
  statement_id   = "AllowPromptBucket"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.this.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = "arn:${local.p}:s3:::${var.prompt_bucket_name}"
  source_account = local.account_id
}

# This bucket's only notification. The prompt-logs module defines none.
resource "aws_s3_bucket_notification" "this" {
  bucket = var.prompt_bucket_name
  lambda_function {
    lambda_function_arn = aws_lambda_function.this.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = var.prompt_prefix
  }
  depends_on = [aws_lambda_permission.s3]
}

output "role_arn" {
  value = aws_iam_role.this.arn
}

output "policy_json" {
  value = local.policy
}
