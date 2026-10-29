# Trail in the customer's Kiro account (task 6.3). Data events of
# AWS::CodeWhisperer::Profile only, no management events. Delivers to the
# observability account's bucket. No KMS key: log files use the bucket's
# SSE-S3 default (decided 2026-10-29).
# Provider: customer Kiro account, Region of the Kiro profile.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0" }
  }
}

variable "trail_name" {
  type    = string
  default = "kiro-mon-audit"
}

variable "bucket_name" {
  description = "Output bucket_name of infra/observability/trail-bucket."
  type        = string
}

variable "observability_account_id" {
  type = string
}

variable "event_names" {
  description = "Optional eventName filter once OQ-1 has listed the event names. Empty = all event names."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  kiro_resource_type = "AWS::CodeWhisperer::Profile"
}

resource "aws_cloudtrail" "this" {
  name                          = var.trail_name
  s3_bucket_name                = var.bucket_name
  is_multi_region_trail         = false
  include_global_service_events = false
  enable_log_file_validation    = true
  tags                          = merge({ Project = "kiro-monitoring" }, var.tags)

  advanced_event_selector {
    name = "kiro-profile-data-events"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }

    field_selector {
      field  = "resources.type"
      equals = [local.kiro_resource_type]
    }

    dynamic "field_selector" {
      for_each = length(var.event_names) > 0 ? [1] : []
      content {
        field  = "eventName"
        equals = var.event_names
      }
    }
  }

  lifecycle {
    precondition {
      condition     = endswith(var.bucket_name, "-${var.observability_account_id}")
      error_message = "bucket_name must be the trail bucket of the observability account (name ends with its account ID)."
    }
  }
}

output "trail_arn" {
  value = aws_cloudtrail.this.arn
}
