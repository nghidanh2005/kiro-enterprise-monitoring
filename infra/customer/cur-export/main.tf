# CUR 2.0 export with Kiro line items only (decision D2 = only Kiro; tasks 4.1, 4.3).
# Provider: the payer account, Region us-east-1 (Data Exports exists only there).
#
# Same column list, table configuration, prefix and export name as the CID
# Source stack, so CID's crawler and views read it unchanged. Only the WHERE
# clause differs. The filter value 'Kiro' is the one CID's own Kiro dashboard
# uses (cloud-intelligence-dashboards-framework @ 665a416,
# dashboards/kiro-user-activity/kiro-user-activity.yaml line 374).
#
# [CHƯA XÁC MINH] Cross-account delivery: neither the Data Exports API nor this
# provider resource has an S3BucketOwner field (botocore 1.42.2, provider
# 6.70.0). Whether delivery to the observability account's bucket works through
# its bucket policy alone is the first thing task 4.1 measures.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0" }
  }
}

variable "observability_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.observability_account_id))
    error_message = "observability_account_id must be 12 digits."
  }
}

variable "destination_region" {
  description = "Region of the CID data-exports bucket (D6)."
  type        = string
  default     = "ap-southeast-1"
}

variable "resource_prefix" {
  description = "CID ResourcePrefix. Bucket = <prefix>-<observability account>-data-exports, export = <prefix>-cur2."
  type        = string
  default     = "cid"
}

variable "product_codes" {
  type    = list(string)
  default = ["Kiro"]
}

variable "time_granularity" {
  description = "CID: HOURLY unless the invoice is above 50M USD."
  type        = string
  default     = "HOURLY"
}

data "aws_caller_identity" "current" {}

locals {
  # Column list copied from awslabs/cid-data-collection-framework (Apache-2.0),
  # data-exports/deploy/data-exports-aggregation.yaml line 243 (DefaultQuery), commit c747c93.
  cid_columns = file("${path.module}/cid_cur2_columns.txt")
  where       = join(" OR ", [for c in var.product_codes : "line_item_product_code = '${c}'"])
  query       = "SELECT ${trimspace(local.cid_columns)} FROM COST_AND_USAGE_REPORT WHERE ${local.where}"
  bucket      = "${var.resource_prefix}-${var.observability_account_id}-data-exports"
  prefix      = "cur2/${data.aws_caller_identity.current.account_id}"
}

resource "aws_bcmdataexports_export" "kiro" {
  export {
    name        = "${var.resource_prefix}-cur2"
    description = "CUR 2.0, Kiro line items only, for the Kiro monitoring dashboards"

    data_query {
      query_statement = local.query
      table_configurations = {
        COST_AND_USAGE_REPORT = {
          TIME_GRANULARITY                      = var.time_granularity
          INCLUDE_RESOURCES                     = "TRUE"
          INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY = "FALSE"
          INCLUDE_SPLIT_COST_ALLOCATION_DATA    = "FALSE"
          INCLUDE_IAM_PRINCIPAL_DATA            = "FALSE"
        }
      }
    }

    destination_configurations {
      s3_destination {
        s3_bucket = local.bucket
        s3_prefix = local.prefix
        s3_region = var.destination_region
        s3_output_configurations {
          overwrite   = "OVERWRITE_REPORT"
          format      = "PARQUET"
          compression = "PARQUET"
          output_type = "CUSTOM"
        }
      }
    }

    refresh_cadence {
      frequency = "SYNCHRONOUS"
    }
  }

  lifecycle {
    precondition {
      condition     = can(regex("line_item_resource_id", local.cid_columns))
      error_message = "The column list must select line_item_resource_id: CID counts licenses from it."
    }
  }
}

output "query" {
  value = local.query
}

output "destination" {
  value = "s3://${local.bucket}/${local.prefix}/${var.resource_prefix}-cur2/data/"
}
