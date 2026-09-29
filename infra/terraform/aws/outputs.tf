output "cluster_name" { value = module.eks.cluster_name }
output "postgres_endpoint" { value = aws_db_instance.postgres.address }
output "kafka_bootstrap_tls" { value = aws_msk_cluster.kafka.bootstrap_brokers_tls }
output "redis_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
