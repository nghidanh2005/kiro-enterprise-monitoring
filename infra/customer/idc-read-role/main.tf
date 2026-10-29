# Read-only role in the customer's Identity Center account (task 5.6, narrowed in 5.8).
#
# Before the sample is deployed the identity-map role ARN is unknown, so the
# trust policy names the observability account and pins the caller with
# aws:PrincipalArn (role name pattern). Once identity_map_role_arn is set the
# principal becomes that exact role. Both forms require sts:ExternalId.

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

variable "identity_store_id" {
  type = string
  validation {
    condition     = can(regex("^d-[0-9a-z]{10}$", var.identity_store_id))
    error_message = "identity_store_id must be the d-... Identity Store ID, not the ssoins- instance ID."
  }
}

variable "observability_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.observability_account_id))
    error_message = "observability_account_id must be 12 digits."
  }
}

variable "external_id" {
  type = string
  validation {
    condition     = length(var.external_id) >= 16 && can(regex("^[A-Za-z0-9+=,.@:/-]+$", var.external_id))
    error_message = "external_id: at least 16 characters, sts:ExternalId character set."
  }
}

variable "identity_map_role_arn" {
  description = "Exact ARN of the sample's identity-map Lambda role. Null until task 5.7 has run."
  type        = string
  default     = null
}

variable "permissions_boundary_arn" {
  type    = string
  default = null
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  p                 = data.aws_partition.current.partition
  role_name         = "kiro-mon-idc-read-${var.slug}"
  principal_pattern = "arn:${local.p}:iam::${var.observability_account_id}:role/kiro-${var.slug}-identity-map*"
  narrowed          = var.identity_map_role_arn != null

  trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "IdentityMapLambda"
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = local.narrowed ? var.identity_map_role_arn : "arn:${local.p}:iam::${var.observability_account_id}:root" }
      Condition = merge(
        { StringEquals = { "sts:ExternalId" = var.external_id } },
        local.narrowed ? {} : { ArnLike = { "aws:PrincipalArn" = local.principal_pattern } },
      )
    }]
  })

  # Same resource pair the sample grants for same-account Identity Center
  # (cfn/03-identity-mapping.yaml lines 295-298).
  permissions = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "ListUsersOfOneStore"
      Effect = "Allow"
      Action = "identitystore:ListUsers"
      Resource = [
        "arn:${local.p}:identitystore::${data.aws_caller_identity.current.account_id}:identitystore/${var.identity_store_id}",
        "arn:${local.p}:identitystore:::user/*",
      ]
    }]
  })
}

resource "aws_iam_role" "this" {
  name                 = local.role_name
  description          = "Kiro monitoring - identity map reads Identity Center users - read only"
  assume_role_policy   = local.trust
  permissions_boundary = var.permissions_boundary_arn
  max_session_duration = 3600
  tags                 = { Project = "kiro-monitoring", Customer = var.slug }

  lifecycle {
    precondition {
      condition     = !local.narrowed || can(regex("^arn:${local.p}:iam::${var.observability_account_id}:role/", var.identity_map_role_arn))
      error_message = "identity_map_role_arn must be a role in the observability account."
    }
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "identitystore-list-users"
  role   = aws_iam_role.this.id
  policy = local.permissions
}

output "role_arn" {
  value = aws_iam_role.this.arn
}

output "trust_policy_json" {
  value = local.trust
}

output "permissions_json" {
  value = local.permissions
}
