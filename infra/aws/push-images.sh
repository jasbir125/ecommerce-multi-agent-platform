#!/bin/sh
# Builds every image and pushes it to ECR. Needs Docker, the AWS CLI and `terraform apply` already run (the repositories must exist).
#   ./push-images.sh [tag]            default tag: latest
set -eu
TAG="${1:-latest}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF="$(dirname "$0")/terraform"
REGISTRY="$(terraform -chdir="$TF" output -raw ecr_registry)"
REGION="$(echo "$REGISTRY" | sed -E 's/.*\.ecr\.([a-z0-9-]+)\.amazonaws.*/\1/')"
PREFIX="${NAME_PREFIX:-recovery}"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

push() { # name, context, extra build args...
  name="$1"; ctx="$2"; shift 2
  img="$REGISTRY/$PREFIX/$name:$TAG"
  echo "== $name"
  # ECS runs X86_64 tasks (see ecs.tf): build for that platform even on Apple silicon.
  docker build --platform linux/amd64 -t "$img" "$@" "$ctx"
  docker push "$img"
}

for m in order-service shipment-service payment-service inventory-service approval-service \
         policy-service audit-service workflow-service notification-service; do
  push "$m" "$ROOT/spring-boot-services" --build-arg MODULE="$m"
done
push agent-host "$ROOT/agent-host"
push ecommerce-mcp-server "$ROOT/mcp-servers" -f "$ROOT/mcp-servers/ecommerce-mcp-server/Dockerfile"
push policy-mcp-server "$ROOT/mcp-servers" -f "$ROOT/mcp-servers/policy-mcp-server/Dockerfile"
push streamlit-ui "$ROOT/streamlit-ui"
push db-init "$ROOT" -f "$ROOT/infra/aws/db-init/Dockerfile"
echo "Pushed all images with tag $TAG."
