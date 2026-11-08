"""Read-only MCP resources: context an agent can load without calling a tool."""
import json

from fastmcp import FastMCP

from app.deps import Deps

RULES = """Business rules enforced by the Spring services (you cannot override them):
- Only the order owner can view or change an order.
- Shipped orders cannot be cancelled directly.
- A refund needs an APPROVED approval for the same workflow and amount, and an idempotency key.
- A refund can never exceed the order amount; only one refund per order.
- A replacement order needs a RESERVED inventory reservation first.
- One carrier case per shipment exception.
Never claim an action happened unless the tool result says so."""


def register(mcp: FastMCP, deps: Deps) -> None:
    @mcp.resource("ecommerce://rules/business", mime_type="text/plain")
    def business_rules() -> str:
        """The deterministic business rules behind the write tools."""
        return RULES

    @mcp.resource("ecommerce://orders/{order_id}", mime_type="application/json")
    async def order_snapshot(order_id: str) -> str:
        """Snapshot of an order owned by the current customer."""
        return json.dumps(await deps.clients.orders.get(f"/api/orders/{order_id}"))

    @mcp.resource("ecommerce://shipments/{order_id}", mime_type="application/json")
    async def shipment_snapshot(order_id: str) -> str:
        """Tracking snapshot for an order owned by the current customer."""
        return json.dumps(await deps.clients.shipments.get(f"/api/shipments/{order_id}"))
