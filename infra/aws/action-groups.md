# Action groups (Option C)

Action groups call the existing REST APIs through Lambda functions (or return-of-control to the caller). The Lambda adds the service token and the end-user identity headers; it must never forward model-produced identity.

| Agent | Action group | Operations | Backing API | Writes? |
|---|---|---|---|---|
| Order | OrderActions | `getOrder`, `checkCancellation` | `GET /api/orders/{id}` | no |
| Shipment | ShipmentActions | `getShipment`, `createCarrierCase` | `GET /api/shipments/{orderId}`, `POST .../carrier-case` | carrier case only, idempotent |
| Policy | PolicyActions | `searchPolicy`, `validatePolicy` | `POST /api/policies/search`, `/validate` | no |
| Product | CatalogActions | `searchProducts`, `checkInventory` | `GET /api/catalog/products/search`, `/api/inventory/{id}` | no |
| Payment | PaymentActions | `getRefundStatus` (read) | `GET /api/payments/refunds/{id}` | no |

**`initiateRefund` is deliberately not an agent action.** A refund needs an APPROVED approval for the same workflow and amount (payment-service enforces it). In Option C run the refund from the workflow service after approval (return-of-control or a Step Functions task), exactly as the LangGraph deterministic nodes do.

Schemas: derive the OpenAPI documents from the services (`/v3/api-docs` is not enabled; the contracts are in `docs/api-contracts.md`) and keep each action group to the operations above. Lambda hardening: reserved concurrency, per-call timeout of a few seconds, strict argument validation (order ids match `^ORD-[A-Z0-9]+$`), and the same audit event on every call.
