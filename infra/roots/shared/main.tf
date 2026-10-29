# One freshness check for every customer (task 9.3), in the observability account.
#
#   terraform init -backend-config="path=../../../customers/_shared/state-freshness/terraform.tfstate"
#   terraform plan -var-file=../../../customers/_shared/freshness.tfvars

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.4" }
  }
  backend "local" {}
}

variable "observability_account_id" {
  type = string
}

variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "config" {
  description = "See infra/observability/freshness/main.tf."
  type        = any
}

variable "alert_emails" {
  type    = list(string)
  default = []
}

variable "permissions_boundary_arn" {
  type    = string
  default = null
}

variable "dry_run" {
  type    = bool
  default = false
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.observability_account_id]
  default_tags {
    tags = { Project = "kiro-monitoring", ManagedBy = "terraform" }
  }
}

module "freshness" {
  source                   = "../../observability/freshness"
  config                   = var.config
  alert_emails             = var.alert_emails
  permissions_boundary_arn = var.permissions_boundary_arn
  dry_run                  = var.dry_run
}

output "topic_arn" {
  value = module.freshness.topic_arn
}
