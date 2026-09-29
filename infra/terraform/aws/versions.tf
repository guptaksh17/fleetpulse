terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
  # Remote state (S3 + DynamoDB lock) is configured per environment with -backend-config.
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "fleetpulse", env = var.env } }
}
