# Terraform (ECS/Fargate)

See `../deployment.md` for the walkthrough, architecture and known gaps.

| File | What |
|---|---|
| `network.tf` | VPC, public/private subnets, NAT, security groups |
| `data.tf` | RDS PostgreSQL 16, ElastiCache Redis, MSK Kafka |
| `secrets.tf` | Secrets Manager (generated service token, DB password, OpenAI key) |
| `iam.tf` | Execution role, plain task role, agent-host role with Bedrock permissions |
| `bedrock.tf` | Bedrock guardrail and version |
| `ecr.tf` | Service catalogue (ports, sizes) and ECR repositories |
| `ecs.tf` | Cluster, service discovery, task definitions, services, load balancer |
| `db_init.tf` | One-off schema task |

Quick checks: `make tf-validate` (format check + validate, no AWS access needed).
