# Postman collection

Covers every REST endpoint of all services (66 requests, 13 folders): order, shipment, payment, inventory, approval, policy, audit, workflow (+AI), notification, agent host, MCP server HTTP routes, actuator health, and an end-to-end walk-through.

## Use
1. Postman → Import → `ecommerce-platform.postman_collection.json` and `local.postman_environment.json`; select the *Local (docker compose)* environment. Stack must be running (`make up`).
2. Every request sends `X-Service-Token` plus the customer / roles it acts for (`X-Customer-Id`, `X-Roles`): customer requests act as `CUST-1001`, approvals as a manager, policy ingest as admin, traces and admin metrics as an operator.
3. Run requests in folder order, or run `99 End-to-end walk-through`. Scripts save `workflowId`, `approvalId`, `reservationId`, `refundId` and `policyId` for later requests.

## Fresh orders
A refund can be issued once per order. `./new_order.sh` creates a new lost-shipment order for CUST-1001 and prints its id.
The collection uses two orders: `orderId` (per-service requests) and `walkOrderId` (the end-to-end folder).

## Run everything from the CLI
```bash
./run_newman.sh      # creates two fresh orders and runs all requests (~1 min); 
```
Expected: every request passes. One non-2xx response is intended and asserted: cancelling a shipped order (409).
Regenerate the files with `python3 build_collection.py`.
