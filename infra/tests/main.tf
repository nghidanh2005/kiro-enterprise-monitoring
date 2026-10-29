# Test root for every module under infra/. It creates nothing itself:
# each tests/*.tftest.hcl run block points `module { source = ... }` at one
# module and plans it against mocked providers, so no AWS credentials are used.
#
#   cd infra/tests
#   terraform init -backend=false
#   terraform test
terraform {
  required_version = ">= 1.7"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.4" }
  }
}
