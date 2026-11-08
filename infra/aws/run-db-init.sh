#!/bin/sh
# Runs the one-off schema task on ECS and waits for it. Set LOAD_SEED_DATA=true for demo rows (never in production).
set -eu
TF="$(dirname "$0")/terraform"
out() { terraform -chdir="$TF" output -raw "$1"; }
CLUSTER="$(out cluster)"; FAMILY="$(out db_init_task_family)"; SG="$(out services_security_group_id)"
SUBNETS="$(terraform -chdir="$TF" output -json private_subnet_ids | tr -d '[]" ')"
OVERRIDES='{}'
[ "${LOAD_SEED_DATA:-false}" = "true" ] && OVERRIDES='{"containerOverrides":[{"name":"db-init","environment":[{"name":"LOAD_SEED_DATA","value":"true"}]}]}'
ARN=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$FAMILY" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides "$OVERRIDES" --query 'tasks[0].taskArn' --output text)
echo "Started $ARN"
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$ARN"
CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$ARN" --query 'tasks[0].containers[0].exitCode' --output text)
echo "db-init exit code: $CODE (logs: /ecs/$(out cluster)/db-init)"
[ "$CODE" = "0" ]
