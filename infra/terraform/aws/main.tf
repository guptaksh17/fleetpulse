data "aws_availability_zones" "available" {}

locals {
  name = "fleetpulse-${var.env}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name               = local.name
  cidr               = "10.40.0.0/16"
  azs                = local.azs
  private_subnets    = ["10.40.1.0/24", "10.40.2.0/24", "10.40.3.0/24"]
  public_subnets     = ["10.40.101.0/24", "10.40.102.0/24", "10.40.103.0/24"]
  enable_nat_gateway = true
  single_nat_gateway = false # one NAT per AZ: no single point of failure
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name                   = local.name
  cluster_version                = var.cluster_version
  vpc_id                         = module.vpc.vpc_id
  subnet_ids                     = module.vpc.private_subnets
  cluster_endpoint_public_access = false
  cluster_encryption_config      = { resources = ["secrets"] } # envelope encryption with KMS

  eks_managed_node_groups = {
    apps = {
      instance_types = ["m6i.xlarge"]
      min_size       = 3
      max_size       = 12
      desired_size   = 3
    }
  }
}

resource "aws_security_group" "data" {
  name   = "${local.name}-data"
  vpc_id = module.vpc.vpc_id
  ingress {
    description     = "From EKS nodes only"
    from_port       = 0
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "db" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}

# Business state (CP): Multi-AZ PostgreSQL, encrypted at rest (AES-256 via KMS), password managed by Secrets Manager.
resource "aws_db_instance" "postgres" {
  identifier                  = "${local.name}-pg"
  engine                      = "postgres"
  engine_version              = "16.4"
  instance_class              = var.db_instance_class
  allocated_storage           = 100
  max_allocated_storage       = 1000
  db_name                     = "fleetpulse"
  username                    = "fleetpulse"
  manage_master_user_password = true
  multi_az                    = true
  storage_encrypted           = true
  backup_retention_period     = 7
  deletion_protection         = true
  db_subnet_group_name        = aws_db_subnet_group.db.name
  vpc_security_group_ids      = [aws_security_group.data.id]
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${local.name}-pg-final"
}

# Telemetry stream: MSK across 3 AZs, TLS in transit, encrypted at rest.
resource "aws_msk_cluster" "kafka" {
  cluster_name           = local.name
  kafka_version          = "3.7.x"
  number_of_broker_nodes = 3
  broker_node_group_info {
    instance_type   = "kafka.m5.large"
    client_subnets  = module.vpc.private_subnets
    security_groups = [aws_security_group.data.id]
    storage_info {
      ebs_storage_info { volume_size = 500 }
    }
  }
  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }
}

# Hot state (dedup keys, streaming features, cache): Redis with replica and failover, TLS and at-rest encryption.
resource "aws_elasticache_subnet_group" "redis" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${local.name}-redis"
  description                = "FleetPulse dedup and feature state"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = "cache.r6g.large"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.data.id]
}

resource "aws_secretsmanager_secret" "jwt" {
  name = "fleetpulse/jwt-secret"
}
