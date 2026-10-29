# Kiro-only CUR 2.0 export in the customer's payer account (D2 = only Kiro; tasks 4.1, 4.3).
# With D2 = full CUR, use CID's CID-DataExports-Source stack instead of this root.
#
#   terraform init -backend-config="path=../../../customers/<slug>/state-payer/terraform.tfstate"

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.0" }
  }
  backend "local" {}
}

variable "payer_account_id" {
  type = string
}

variable "payer_profile" {
  type    = string
  default = null
}

variable "observability_account_id" {
  type = string
}

variable "destination_region" {
  type    = string
  default = "ap-southeast-1"
}

provider "aws" {
  region              = "us-east-1" # Data Exports exists only in us-east-1
  profile             = var.payer_profile
  allowed_account_ids = [var.payer_account_id]
}

module "cur_export" {
  source                   = "../../customer/cur-export"
  observability_account_id = var.observability_account_id
  destination_region       = var.destination_region
}

output "destination" {
  value = module.cur_export.destination
}
