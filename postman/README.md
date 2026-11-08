# Postman collection

Covers every REST endpoint of all services (80 requests, 14 folders): auth, order, shipment, payment, inventory, approval, policy, audit, workflow (+AI), notification, agent host, MCP server HTTP routes, actuator health, and an end-to-end walk-through.

## Use
1. Postman → Import → `ecommerce-platform.postman_collection.json` and `local.postman_environment.json`; select the *Local (docker compose)* environment. Stack must be running (`make up`).
2. Run `00 Auth > Login as alice` (and `manny`, `olivia`, `admin` as needed). Each login stores `token_<user>`; the `Switch to ...` requests change the active `accessToken` that the whole collection sends as a Bearer token.
3. Run requests in folder order, or run `99 End-to-end walk-through`. Scripts save `workflowId`, `approvalId`, `reservationId`, `refundId` and `policyId` for later requests.

Requests marked *(internal)* are platform-to-platform calls and use `X-Service-Token` (`serviceToken` variable), because internal endpoints reject end-user JWTs by design. Approve/reject needs manny; policy ingest/archive needs admin; trace and admin metrics need an operator.

## Fresh orders
A refund can be issued once per order. `./new_order.sh` creates a new lost-shipment order for CUST-1001 and prints its id.
The collection uses two orders: `orderId` (per-service requests) and `walkOrderId` (the end-to-end folder).

## Run everything from the CLI
```bash
./run_newman.sh      # creates two fresh orders and runs all requests (~1 min); tokens refresh automatically
```
Expected: every request passes. Two non-2xx responses are intended and asserted: wrong-password login (401) and cancelling a shipped order (409).
Regenerate the files with `python3 build_collection.py`.
