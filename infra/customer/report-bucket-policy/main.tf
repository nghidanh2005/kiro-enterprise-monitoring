# Bucket policy of the customer's Kiro report bucket (task 4.8, applied in 4.9 and 5.8).
#
# aws_s3_bucket_policy REPLACES the whole policy, so this module rebuilds it:
# every statement found by the survey (customers/<slug>/survey/bucket-policy-report.json)
# is kept as is, and only the two KiroMon statements are added or replaced.
# Runs with a provider for the customer's Kiro account.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0" }
  }
}

variable "bucket" {
  type = string
}

variable "report_prefix" {
  description = "Must stay kiro/: the CID Data Collection Stack does not pass another prefix to its Kiro module."
  type        = string
  default     = "kiro/"

  validation {
    condition     = can(regex("^[a-z0-9-]+/$", var.report_prefix))
    error_message = "report_prefix must look like kiro/ (lowercase, ending in a slash)."
  }
}

variable "existing_policy_json" {
  description = "Current policy from the survey. Empty string when the bucket had none."
  type        = string
  default     = ""
}

variable "reader_role_arns" {
  description = "CID collector role (LambdaRoleArn) and, after task 5.7, the sample's normalizer role."
  type        = list(string)

  validation {
    condition     = length(var.reader_role_arns) > 0 && alltrue([for a in var.reader_role_arns : can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/.+", a))])
    error_message = "reader_role_arns must be a non-empty list of IAM role ARNs."
  }
}

variable "require_kiro_write_statement" {
  description = "Refuse to apply a policy that no longer lets q.amazonaws.com write the report."
  type        = bool
  default     = true
}

data "aws_partition" "current" {}

locals {
  managed_sids = ["KiroMonReportRead", "KiroMonReportList"]
  existing_doc = jsondecode(var.existing_policy_json == "" ? "{\"Statement\":[]}" : var.existing_policy_json)
  # A policy may hold a single statement object instead of a list; flatten
  # turns both forms into a tuple without forcing one element type.
  existing_statements = flatten([local.existing_doc.Statement])
  kept                = [for s in local.existing_statements : s if !contains(local.managed_sids, try(s.Sid, ""))]
  kept_sids           = [for s in local.kept : try(s.Sid, "(no Sid)")]
  bucket_arn          = "arn:${data.aws_partition.current.partition}:s3:::${var.bucket}"

  ours = [
    {
      Sid       = "KiroMonReportRead"
      Effect    = "Allow"
      Principal = { AWS = var.reader_role_arns }
      Action    = "s3:GetObject"
      Resource  = "${local.bucket_arn}/${var.report_prefix}*"
    },
    {
      Sid       = "KiroMonReportList"
      Effect    = "Allow"
      Principal = { AWS = var.reader_role_arns }
      Action    = "s3:ListBucket"
      Resource  = local.bucket_arn
      Condition = { StringLike = { "s3:prefix" = "${var.report_prefix}*" } }
    },
  ]

  statements = concat(local.kept, local.ours)
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = local.statements
  })
  # Any statement whose service principal is q.amazonaws.com (string or list form).
  has_kiro_write = anytrue([
    for s in local.statements : contains(flatten([try(s.Principal.Service, [])]), "q.amazonaws.com")
  ])
}

resource "aws_s3_bucket_policy" "this" {
  bucket = var.bucket
  policy = local.policy

  lifecycle {
    precondition {
      condition     = !var.require_kiro_write_statement || local.has_kiro_write
      error_message = "The rebuilt policy has no statement for q.amazonaws.com, so Kiro could no longer write the report. Check existing_policy_json."
    }
  }
}

output "policy_json" {
  value = local.policy
}

output "kept_sids" {
  description = "Statements carried over from the existing policy."
  value       = local.kept_sids
}
