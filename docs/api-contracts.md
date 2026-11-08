# API Contracts (Phase 2)

All endpoints require `X-Service-Token`. `X-Customer-Id` identifies the customer being acted for; `X-Roles` carries `MANAGER`/`APPROVER` (approvals), `AGENT`/`OPERATOR`/`ADMIN` (workflow control, policy ingestion). Errors are RFC 7807 with a `code` field.

| Service (port) | Endpoints |
|---|---|
| order (8081) | `GET /api/orders/{id}`, `GET /api/customers/{id}/orders`, `GET /api/orders/{id}/owner`, `POST /api/orders/{id}/cancel`, `POST /api/orders/replacement` |
| shipment (8082) | `GET /api/shipments/{orderId}`, `POST /api/shipments/{orderId}/carrier-case` |
| payment (8083) | `POST /api/payments/refunds`, `GET /api/payments/refunds/{id}` |
| inventory (8084) | `GET /api/catalog/products/search`, `GET /api/inventory/{productId}`, `POST /api/inventory/reservations`, `GET /api/inventory/reservations/{id}`, `POST /api/inventory/reservations/{id}/release` |
| approval (8085) | `POST /api/approvals`, `GET /api/approvals?status=`, `GET /api/approvals/{id}`, `POST /api/approvals/{id}/decision` |
| policy (8086) | `POST /api/policies/search`, `POST /api/policies/validate`, `POST /api/policies/ingest` (ADMIN), `GET /api/policies/{id}`, `GET /api/policies/{key}/{version}` |
| audit (8087) | `POST /api/audit/events`, `GET /api/audit/workflows/{id}` |
| workflow (8088) | `POST /api/workflows`, `GET /api/workflows`, `GET /api/workflows/{id}`, `POST /api/workflows/{id}/resume`, `GET /api/workflows/{id}/trace`, `POST /api/ai/classify`, `POST /api/ai/recommend` |
| notification (8089) | `POST /api/notifications/customer`, `POST /api/notifications/internal` |

## Business rules enforced in services
- Ownership on every order/shipment/workflow read and write (403 + audit event otherwise).
- Shipped orders cannot be cancelled directly (409). Replacement needs a `RESERVED` inventory reservation for the same workflow.
- Refund: idempotency key, amount <= refundable balance, one refund per order, and an `APPROVED`, unexpired approval for the same workflow and amount.
- Inventory reservation is atomic (no overselling) and idempotent; release is idempotent.
- Carrier case is created once per shipment exception.
- Notifications use fixed templates; result templates (refund, replacement) are verified with the owning service before sending.
- `workflow_audit` is append-only (DB trigger); PII is masked before storage.

## RAG response
```json
{"answer":"...","confidence":0.0,"grounded":true,"policyVersion":"2",
 "sources":[{"policyId":"2","policyKey":"refund-policy","version":"2","source":"Refund Policy","score":0.0,"rerankScore":0.0,"snippet":"...","injectionSuspected":false}],
 "insufficientEvidence":false}
```
