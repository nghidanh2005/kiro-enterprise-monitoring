# Receives one customer's Kiro data-event trail (task 6.1, applied in 6.2).
# Provider: observability account, in the Region chosen after OQ-11
# (ap-southeast-1 if CloudTrail delivers across Regions, else the trail's Region).
#
# Policies follow the CloudTrail docs (sources in design.md 13):
#   bucket  AWSCloudTrailAclCheck + AWSCloudTrailWrite with aws:SourceArn
# Encryption is SSE-S3 (decided 2026-10-29): no KMS key, so readers need only
# s3:GetObject in their own IAM policy, and reads cost no KMS requests.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0" }
  }
}

variable "slug" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,14}$", var.slug))
    error_message = "slug: lowercase letters, digits, hyphens, start with a letter, max 15."
  }
}

variable "customer_account_id" {
  description = "Customer's Kiro account (owner of the trail)."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.customer_account_id))
    error_message = "customer_account_id must be 12 digits."
  }
}

variable "trail_region" {
  description = "Region of the customer's Kiro profile, where the trail lives."
  type        = string
}

variable "trail_name" {
  type    = string
  default = "kiro-mon-audit"
}

variable "retention_days" {
  description = "D5. 395 days = 13 months, to compare with the same month last year."
  type        = number
  default     = 395
}

variable "glue_database_name" {
  description = "Existing database. The sample creates kiro_<slug> (STACK_PREFIX with - replaced by _). Null skips the table."
  type        = string
  default     = null
}

variable "projection_start" {
  description = "First day the partition projection covers (yyyy/MM/dd)."
  type        = string
  default     = "2026/01/01"
}

variable "force_destroy" {
  description = "true only for the temporary OQ experiment buckets."
  type        = bool
  default     = false
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  p          = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id
  bucket     = "kiro-mon-trail-${var.slug}-${local.account_id}"
  bucket_arn = "arn:${local.p}:s3:::${local.bucket}"
  trail_arn  = "arn:${local.p}:cloudtrail:${var.trail_region}:${var.customer_account_id}:trail/${var.trail_name}"
  log_root   = "AWSLogs/${var.customer_account_id}/CloudTrail"
  tags       = { Project = "kiro-monitoring", Customer = var.slug }

  bucket_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = local.bucket_arn
        Condition = { StringEquals = { "aws:SourceArn" = local.trail_arn } }
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${local.bucket_arn}/AWSLogs/${var.customer_account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.trail_arn
          }
        }
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

  # Columns of the AWS-published CloudTrail table for partition projection
  # (Athena user guide, create-cloudtrail-table-partition-projection).
  columns = [
    ["eventversion", "string"],
    ["useridentity", "struct<type:string,principalid:string,arn:string,accountid:string,invokedby:string,accesskeyid:string,username:string,onbehalfof:struct<userid:string,identitystorearn:string>,sessioncontext:struct<attributes:struct<mfaauthenticated:string,creationdate:string>,sessionissuer:struct<type:string,principalid:string,arn:string,accountid:string,username:string>,ec2roledelivery:string,webidfederationdata:struct<federatedprovider:string,attributes:map<string,string>>>>"],
    ["eventtime", "string"],
    ["eventsource", "string"],
    ["eventname", "string"],
    ["awsregion", "string"],
    ["sourceipaddress", "string"],
    ["useragent", "string"],
    ["errorcode", "string"],
    ["errormessage", "string"],
    ["requestparameters", "string"],
    ["responseelements", "string"],
    ["additionaleventdata", "string"],
    ["requestid", "string"],
    ["eventid", "string"],
    ["readonly", "string"],
    ["resources", "array<struct<arn:string,accountid:string,type:string>>"],
    ["eventtype", "string"],
    ["apiversion", "string"],
    ["recipientaccountid", "string"],
    ["serviceeventdetails", "string"],
    ["sharedeventid", "string"],
    ["vpcendpointid", "string"],
    ["vpcendpointaccountid", "string"],
    ["eventcategory", "string"],
    ["addendum", "struct<reason:string,updatedfields:string,originalrequestid:string,originaleventid:string>"],
    ["sessioncredentialfromconsole", "string"],
    ["edgedevicedetails", "string"],
    ["tlsdetails", "struct<tlsversion:string,ciphersuite:string,clientprovidedhostheader:string>"],
  ]
}

resource "aws_s3_bucket" "this" {
  bucket        = local.bucket
  force_destroy = var.force_destroy
  tags          = local.tags
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
    id     = "expire-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.retention_days
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket     = aws_s3_bucket.this.id
  policy     = local.bucket_policy
  depends_on = [aws_s3_bucket_public_access_block.this]
}

resource "aws_glue_catalog_table" "cloudtrail" {
  count         = var.glue_database_name == null ? 0 : 1
  database_name = var.glue_database_name
  name          = "cloudtrail_kiro"
  description   = "Kiro data events of customer ${var.slug}. Contains source IP: query only through the audit role."
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"                           = "TRUE"
    "projection.enabled"                 = "true"
    "projection.region.type"             = "enum"
    "projection.region.values"           = var.trail_region
    "projection.timestamp.type"          = "date"
    "projection.timestamp.format"        = "yyyy/MM/dd"
    "projection.timestamp.interval"      = "1"
    "projection.timestamp.interval.unit" = "DAYS"
    "projection.timestamp.range"         = "${var.projection_start},NOW"
    "storage.location.template"          = "s3://${local.bucket}/${local.log_root}/$${region}/$${timestamp}"
  }

  partition_keys {
    name = "region"
    type = "string"
  }
  partition_keys {
    name = "timestamp"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${local.bucket}/${local.log_root}/"
    input_format  = "com.amazon.emr.cloudtrail.CloudTrailInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hive.hcatalog.data.JsonSerDe"
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

output "trail_arn_expected" {
  value = local.trail_arn
}

output "bucket_policy_json" {
  value = local.bucket_policy
}
