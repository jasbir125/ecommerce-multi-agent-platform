# AWS deployment

Three deployment shapes are described. **Option A (ECS/Fargate) is implemented in Terraform** (`terraform/`); B and C are design guidance.

| | Option | State |
|---|---|---|
| A | ECS/Fargate + RDS + ElastiCache + MSK + Bedrock models and guardrail | Terraform in `infra/aws/terraform` (validated, not applied to a live account by the author) |
| B | EKS | Guidance below; same images, same environment variables |
| C | Bedrock-managed agents | Design in `bedrock-agents.md`, `action-groups.md`, `multi-agent-collaboration.md` |

## What Option A creates

```
Internet -> ALB (HTTP, or HTTPS with an ACM certificate)
              |-> streamlit-ui                         (always public)
              |-> auth / workflow / approval / agent   (only if expose_apis = true)

Private subnets (2 AZs), ECS Fargate, service discovery  <service>.recovery.internal
   auth, order, shipment, payment, inventory, approval, policy, audit, workflow, notification
   ecommerce-mcp-server, policy-mcp-server, agent-host, streamlit-ui
        |-> RDS PostgreSQL 16 (pgvector)      encrypted, private
        |-> ElastiCache Redis 7               private
        |-> MSK (Kafka 3.6)                   private, topics auto-created (3 partitions)
        |-> Secrets Manager                   service token, DB password, OpenAI key
        |-> Bedrock (agent-host only)         InvokeModel/Converse on the allowed models + ApplyGuardrail
CloudWatch Logs (per service), Container Insights, MSK broker logs
```

Security shape: only the load balancer is public; tasks sit in private subnets; the data stores accept traffic only from the tasks' security group; every secret comes from Secrets Manager; the agent host has a separate IAM role, the only one allowed to call Bedrock; the service token is generated (64 random characters), never the demo value.

## Steps

Prerequisites: Terraform >= 1.5, AWS CLI, Docker, credentials for the target account, Bedrock model access granted in the console for your region.

```bash
cd infra/aws/terraform
cp terraform.tfvars.example terraform.tfvars        # set region, allowed_ingress_cidrs, ...
terraform init
terraform plan -out tfplan && terraform apply tfplan

cd .. && ./push-images.sh                           # builds linux/amd64 images, pushes to ECR
./run-db-init.sh                                    # one-off task: pgvector extension + tables
# demo data only for a demo environment:  LOAD_SEED_DATA=true ./run-db-init.sh

terraform -chdir=terraform apply                    # (re)starts services if they were waiting for images
terraform -chdir=terraform output url
aws secretsmanager get-secret-value --secret-id "$(terraform -chdir=terraform output -raw demo_password_secret)" --query SecretString --output text
```

Services created before their images exist keep failing until `push-images.sh` has run; after that ECS starts them (or run `aws ecs update-service --force-new-deployment`).

Open the URL and pick a persona (customer / operator / manager) in the sidebar. Put the UI behind your own authentication (for example the ALB with Cognito) before sharing it.

### Changing the model

`bedrock_model_id` (Terraform variable) sets the default for all agents. Per-agent overrides use the same variables as locally (`MODEL_SUPERVISOR`, `MODEL_ORDER_AGENT`, ...) with a Bedrock id such as `anthropic.claude-3-5-haiku-20241022-v1:0`; add them to `extra_env` in `ecs.tf`. In some regions you must use an inference-profile id (`eu.anthropic...`, `apac.anthropic...`) and allow it in `bedrock_model_arns`.

### Remote state and teams

Local state is the default so `terraform init` works anywhere. For a team, add an S3 backend with DynamoDB locking in `versions.tf`. State contains the generated secrets: encrypt the bucket and restrict access.

### Tear down

`terraform destroy` (set `db_deletion_protection = false` first; that also lets ECR repositories be deleted with their images).

## Known gaps (read before production)

- **Not applied by the author.** The configuration passes `terraform validate`, the schema job was tested against a local pgvector Postgres, and the Bedrock provider has unit tests, but no live AWS account was used. Expect to fix small things on the first `plan`/`apply`.
- **OpenAI is still used for two things**: policy-service embeddings (the stored vectors are 1536-d `text-embedding-3-small`; the default `hash` embedder is offline and much weaker) and the workflow-service advisor (Spring AI, no Bedrock client wired). Set `TF_VAR_openai_api_key` and `policy_embedding_provider = "openai"`, or migrate both to Bedrock (Titan/Cohere embeddings need a re-embed because the vector size changes).
- **Kafka is plaintext** inside private subnets, and Redis has no TLS. Move to MSK TLS + IAM or SCRAM and in-transit Redis encryption before real customer data.
- **No user authentication.** Identity is carried in headers set by the front end (see `docs/security.md`). Put an authenticating gateway (ALB + Cognito/OIDC) in front before any real use.
- **Observability**: logs and Container Insights are provisioned. The OpenTelemetry collector, Prometheus, Grafana and Jaeger of the local stack are not deployed; services still expose `/metrics` and emit W3C trace headers. Use AWS Distro for OpenTelemetry with X-Ray/CloudWatch, or Amazon Managed Prometheus/Grafana (dashboards in `infra/grafana/dashboards` import as they are).
- Single NAT gateway and one task per service by default (cheap, not highly available): raise `desired_count`, set `single_nat_gateway = false` and `db_multi_az = true` for production.
- Cost drivers: NAT gateway, MSK brokers, RDS, 14 Fargate tasks. Destroy test environments.

## Option B: EKS

Same images and environment variables as `ecs.tf` (`local.common_env`, `extra_env`). Use one Deployment + Service per component (Service DNS replaces Cloud Map), External Secrets or the Secrets Store CSI driver for Secrets Manager, IRSA to give only the agent-host ServiceAccount the Bedrock permissions in `terraform/iam.tf`, an ALB Ingress for the UI, and the same RDS/ElastiCache/MSK definitions from `data.tf`. Liveness/readiness: `/actuator/health` for Spring services, `/health` for the Python services, `/_stcore/health` for the UI.
