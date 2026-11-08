"""Write tools. The model may *request* these actions; the Spring services decide and execute.

Each tool runs the write guardrails first (workflow id, idempotency key, limits, rate limit),
writes an audit event, then calls the owning service. The result is the service's own answer;
an agent may only claim success when the returned status says so.
"""
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.deps import Deps

WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def register(mcp: FastMCP, deps: Deps) -> None:
    c, guard, audit = deps.clients, deps.guard, deps.auditor

    @mcp.tool(annotations=WRITE)
    async def create_approval_request(workflow_id: str, requested_action: str, amount: float,
                                      reason: str, risk_level: str = "MEDIUM") -> dict[str, Any]:
        """Ask a human manager to approve an action (e.g. requested_action="REFUND"). Required before initiate_refund."""
        await guard.check("create_approval_request", workflow_id, amount=amount)
        await audit.record("TOOL_CREATE_APPROVAL_REQUEST", workflow_id, {"action": requested_action, "amount": amount})
        return await c.approvals.post("/api/approvals", {
            "workflowId": workflow_id, "requestedAction": requested_action, "amount": amount,
            "reason": reason[:500], "riskLevel": risk_level})

    @mcp.tool(annotations=WRITE)
    async def initiate_refund(workflow_id: str, order_id: str, amount: float, approval_id: str,
                              idempotency_key: str) -> dict[str, Any]:
        """Request a refund. payment-service re-checks ownership, amount, idempotency and that approval_id
        is APPROVED. Only say a refund was initiated if the result status is INITIATED or COMPLETED."""
        await guard.check("initiate_refund", workflow_id, idempotency_key, amount=amount)
        await audit.record("TOOL_INITIATE_REFUND", workflow_id, {"orderId": order_id, "amount": amount, "approvalId": approval_id})
        return await c.payments.post("/api/payments/refunds", {
            "workflowId": workflow_id, "orderId": order_id, "amount": amount,
            "approvalId": approval_id, "idempotencyKey": idempotency_key})

    @mcp.tool(annotations=WRITE)
    async def reserve_inventory(workflow_id: str, product_id: str, quantity: int, idempotency_key: str) -> dict[str, Any]:
        """Reserve stock for a replacement. Must succeed before create_replacement_order."""
        await guard.check("reserve_inventory", workflow_id, idempotency_key, quantity=quantity)
        await audit.record("TOOL_RESERVE_INVENTORY", workflow_id, {"productId": product_id, "quantity": quantity})
        return await c.inventory.post("/api/inventory/reservations", {
            "workflowId": workflow_id, "productId": product_id, "quantity": quantity, "idempotencyKey": idempotency_key})

    @mcp.tool(annotations=WRITE)
    async def release_inventory(workflow_id: str, reservation_id: str) -> dict[str, Any]:
        """Compensation: release a reservation if a later step failed. Safe to call twice."""
        await guard.check("release_inventory", workflow_id)
        await audit.record("TOOL_RELEASE_INVENTORY", workflow_id, {"reservationId": reservation_id})
        return await c.inventory.post(f"/api/inventory/reservations/{reservation_id}/release")

    @mcp.tool(annotations=WRITE)
    async def create_replacement_order(workflow_id: str, original_order_id: str, reservation_id: str,
                                       idempotency_key: str) -> dict[str, Any]:
        """Create the replacement order. order-service requires a RESERVED reservation for this workflow."""
        await guard.check("create_replacement_order", workflow_id, idempotency_key)
        await audit.record("TOOL_CREATE_REPLACEMENT_ORDER", workflow_id, {"originalOrderId": original_order_id, "reservationId": reservation_id})
        return await c.orders.post("/api/orders/replacement", {
            "workflowId": workflow_id, "originalOrderId": original_order_id,
            "reservationId": reservation_id, "idempotencyKey": idempotency_key})

    @mcp.tool(annotations=WRITE)
    async def create_carrier_case(workflow_id: str, order_id: str, reason: str = "") -> dict[str, Any]:
        """Open a carrier exception case for a delayed or lost shipment (one case per shipment)."""
        await guard.check("create_carrier_case", workflow_id)
        await audit.record("TOOL_CREATE_CARRIER_CASE", workflow_id, {"orderId": order_id})
        return await c.shipments.post(f"/api/shipments/{order_id}/carrier-case",
                                      {"workflowId": workflow_id, "reason": reason[:300] or None})
