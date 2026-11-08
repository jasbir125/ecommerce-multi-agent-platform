terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws    = { source = "hashicorp/aws", version = ">= 5.60, < 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
    tls    = { source = "hashicorp/tls", version = "~> 4.0" }
  }

  # Local state by default so the first `terraform init` works anywhere.
  # For a team, switch to an S3 backend with DynamoDB locking (see ../deployment.md).
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "order-recovery-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" { state = "available" }
