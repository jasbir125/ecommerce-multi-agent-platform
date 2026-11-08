#!/bin/sh
# Creates two fresh orders (one for the per-service requests, one for the end-to-end walk-through) and runs the whole collection.
set -e
cd "$(dirname "$0")"
A=$(./new_order.sh); sleep 1; B=$(./new_order.sh)
exec npx -y newman run ecommerce-platform.postman_collection.json -e local.postman_environment.json \
  --env-var orderId="$A" --env-var walkOrderId="$B" --delay-request 300 --timeout-request 120000 "$@"
