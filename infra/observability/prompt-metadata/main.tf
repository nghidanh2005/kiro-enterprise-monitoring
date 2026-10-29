# Receives prompt-log METADATA from one customer (task 7.5, only when D4 = yes).
# Provider: observability account, ap-southeast-1 (D6).

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0" }
  }
}

variable "slug" {
  type = string
}

variable "customer_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.customer_account_id))
    error_message = "customer_account_id must be 12 digits."
  }
}

variable "writer_role_arn" {
  description = "Role of the customer's metadata Lambda: arn:aws:iam::<customer>:role/kiro-mon-promptmeta-<slug>."
  type        = string
}

variable "retention_days" {
  type    = number
  default = 395
}

variable "glue_database_name" {
  description = "Existing database kiro_<slug> created by the sample. Null skips the table."
  type        = string
  default     = null
}

variable "projection_start" {
  type    = string
  default = "2026-01-01"
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  p          = data.aws_partition.current.partition
  bucket     = "kiro-mon-promptmeta-${var.slug}-${data.aws_caller_identity.current.account_id}"
  bucket_arn = "arn:${local.p}:s3:::${local.bucket}"
  root       = "promptmeta/account_id=${var.customer_account_id}"
  tags       = { Project = "kiro-monitoring", Customer = var.slug }

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CustomerMetadataWriter"
        Effect    = "Allow"
        Principal = { AWS = var.writer_role_arn }
        Action    = "s3:PutObject"
        Resource  = "${local.bucket_arn}/${local.root}/*"
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.bucket_arn, "${local.bucket_arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })

  columns = [
    ["record_type", "string"], ["request_id", "string"], ["conversation_id", "string"],
    ["utterance_id", "string"], ["user_id", "string"], ["event_ts", "string"],
    ["chat_trigger_type", "string"], ["customization_arn_present", "boolean"],
    ["prompt_chars", "int"], ["response_chars", "int"], ["web_link_count", "int"],
    ["code_reference_count", "int"], ["source_key_sha256", "string"],
  ]
}

resource "aws_s3_bucket" "this" {
  bucket = local.bucket
  tags   = local.tags

  lifecycle {
    precondition {
      condition     = can(regex("^arn:aws[a-z-]*:iam::${var.customer_account_id}:role/kiro-mon-promptmeta-${var.slug}$", var.writer_role_arn))
      error_message = "writer_role_arn must be the kiro-mon-promptmeta-<slug> role in the customer account."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    id     = "expire-metadata"
    status = "Enabled"
    filter {}
    expiration {
      days = var.retention_days
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket     = aws_s3_bucket.this.id
  policy     = local.policy
  depends_on = [aws_s3_bucket_public_access_block.this]
}

resource "aws_glue_catalog_table" "promptmeta" {
  count         = var.glue_database_name == null ? 0 : 1
  database_name = var.glue_database_name
  name          = "prompt_metadata"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"                    = "TRUE"
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "${var.projection_start},NOW"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "storage.location.template"   = "s3://${local.bucket}/${local.root}/dt=$${dt}/"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${local.bucket}/${local.root}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }
    dynamic "columns" {
      for_each = local.columns
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

output "bucket_name" {
  value = aws_s3_bucket.this.bucket
}

output "policy_json" {
  value = local.policy
}
