# FleetPulse on AWS (Terraform)

Creates a VPC across three AZs, an EKS cluster, Multi-AZ RDS PostgreSQL, a three-broker MSK cluster
and a Redis replication group, all encrypted at rest and in transit. TimescaleDB runs either as
Timescale Cloud or as a StatefulSet in the cluster.

```bash
terraform init -backend-config="bucket=<state-bucket>" -backend-config="key=fleetpulse/demo.tfstate" -backend-config="region=ap-south-1"
terraform plan -var env=demo
```
Then apply the Kubernetes manifests with the AWS overlay: `kubectl apply -k infra/k8s/overlays/aws`.
Status: validated with `terraform validate`; not applied (no cloud account was used for the hackathon).
