# Temporary resources to measure OQ-1, OQ-2, OQ-3, OQ-4, OQ-9 and OQ-11 (task 3.1).
# All in ONE account (D7). Objects expire after 3 days, buckets use force_destroy,
# so `terraform destroy` (task 3.7) removes everything. Apply is task 3.2 [XÁC NHẬN].
#
# OQ-11: the CloudTrail bucket is put in bucket_region (ap-southeast-1) while the
# trail is in the Kiro region. If CloudTrail rejects that, set
# bucket_region = kiro_region, record the error in design.md, and apply again.
#
#   terraform init -backend-config="path=../../../customers/<D7>/state-oq/terraform.tfstate"

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0" }
  }
  backend "local" {}
}

variable "account_id" {
  description = "D7: the single account used for the experiment."
  type        = string
}

variable "profile" {
  type    = string
  default = null
}

variable "kiro_region" {
  type = string
}

variable "bucket_region" {
  type    = string
  default = "ap-southeast-1"
}

provider "aws" {
  alias               = "bucket"
  region              = var.bucket_region
  profile             = var.profile
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "kiro-monitoring", Purpose = "oq-experiment", Expires = "3-days" }
  }
}

provider "aws" {
  alias               = "kiro"
  region              = var.kiro_region
  profile             = var.profile
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "kiro-monitoring", Purpose = "oq-experiment", Expires = "3-days" }
  }
}

module "trail_bucket" {
  source                   = "../../observability/trail-bucket"
  providers                = { aws = aws.bucket }
  slug                     = "oq"
  customer_account_id      = var.account_id
  trail_region             = var.kiro_region
  trail_name               = "kiro-mon-oq"
  retention_days           = 3
  force_destroy            = true
  glue_database_name       = null
  kms_deletion_window_days = 7
}

module "trail" {
  source                   = "../../customer/trail"
  providers                = { aws = aws.kiro }
  trail_name               = "kiro-mon-oq"
  bucket_name              = module.trail_bucket.bucket_name
  kms_key_arn              = module.trail_bucket.kms_key_arn
  observability_account_id = var.account_id
  tags                     = { Purpose = "oq-experiment" }
}

module "prompt_bucket" {
  source    = "../../customer/prompt-logs"
  providers = { aws = aws.kiro }
  slug      = "oq"
  # Same account by design (D7): this value can never appear in a policy, so the guard passes.
  observability_account_id = "not-applicable-same-account"
  retention_days           = 3
  create_athena            = false
  force_destroy            = true
  kms_deletion_window_days = 7
}

output "trail_bucket" {
  value = module.trail_bucket.bucket_name
}

output "prompt_bucket" {
  value = module.prompt_bucket.bucket_name
}

output "prompt_logs_s3_uri" {
  description = "Task 3.3: enter this in Kiro console > Settings > Logging."
  value       = module.prompt_bucket.s3_uri_for_kiro_console
}

output "measure_command" {
  description = "Task 3.6, run 30+ minutes after the last chat."
  value = join(" ", [
    "python tools/measure_oq.py",
    "--expected-account ${var.account_id} --region ${var.kiro_region}",
    "--trail-bucket ${module.trail_bucket.bucket_name}",
    "--prompt-bucket ${module.prompt_bucket.bucket_name} --prompt-prefix kiro-prompt-logs/",
    "--chat-times customers/<D7>/oq-chat-times.txt",
  ])
}
