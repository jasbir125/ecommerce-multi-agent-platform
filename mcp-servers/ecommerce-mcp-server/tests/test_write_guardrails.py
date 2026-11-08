import json

import httpx
import pytest
from fastmcp.exceptions import ToolError

from mcp_common.security import Identity

REFUND_ARGS = {"workflow_id": "WF-ABC12345", "order_id": "ORD-7842", "amount": 129.99,
               "approval_id": "APR-1", "idempotency_key": "rf-WF-ABC12345"}


async def call(client, tool, args):
    async with client as c:
        return await c.call_tool(tool, args)


async def test_refund_passes_through_and_returns_service_result(make_client, api):
    route = api.post("http://localhost:8083/api/payments/refunds").mock(
        return_value=httpx.Response(200, json={"refundId": "RF-1", "status": "INITIATED"}))
    r = await call(make_client(), "initiate_refund", REFUND_ARGS)
    assert r.data["status"] == "INITIATED"
    sent = json.loads(route.calls[0].request.content)
    assert sent["idempotencyKey"] == "rf-WF-ABC12345" and sent["approvalId"] == "APR-1"


async def test_audit_event_written_before_write(make_client, api):
    api.post("http://localhost:8083/api/payments/refunds").mock(return_value=httpx.Response(200, json={"refundId": "RF-1", "status": "INITIATED"}))
    await call(make_client(), "initiate_refund", REFUND_ARGS)
    audit = [c for c in api.calls if c.request.url.path == "/api/audit/events"]
    assert audit and json.loads(audit[0].request.content)["eventType"] == "TOOL_INITIATE_REFUND"


async def test_requires_authenticated_customer(make_client, identity):
    identity["value"] = Identity(None, None, None, frozenset())
    with pytest.raises(ToolError, match="IDENTITY_REQUIRED"):
        await call(make_client(), "initiate_refund", REFUND_ARGS)


@pytest.mark.parametrize("bad", ["", "ORD-1", "wf-abc12345", "WF-"])
async def test_rejects_invalid_workflow_id(make_client, bad):
    with pytest.raises(ToolError, match="INVALID_WORKFLOW_ID"):
        await call(make_client(), "initiate_refund", {**REFUND_ARGS, "workflow_id": bad})


async def test_session_cannot_act_on_another_workflow(make_client, identity):
    identity["value"] = Identity("CUST-1001", "WF-OTHER9999", "c", frozenset())
    with pytest.raises(ToolError, match="WORKFLOW_MISMATCH"):
        await call(make_client(), "initiate_refund", REFUND_ARGS)


@pytest.mark.parametrize("key", ["", "short", "has spaces in it!"])
async def test_requires_valid_idempotency_key(make_client, key):
    with pytest.raises(ToolError, match="IDEMPOTENCY_KEY_REQUIRED"):
        await call(make_client(), "initiate_refund", {**REFUND_ARGS, "idempotency_key": key})


@pytest.mark.parametrize("amount", [0, -5, 500.01])
async def test_refund_amount_limits(make_client, amount):
    with pytest.raises(ToolError, match="AMOUNT_OUT_OF_RANGE"):
        await call(make_client(), "initiate_refund", {**REFUND_ARGS, "amount": amount})


async def test_reservation_quantity_limit(make_client):
    with pytest.raises(ToolError, match="QUANTITY_OUT_OF_RANGE"):
        await call(make_client(), "reserve_inventory", {
            "workflow_id": "WF-ABC12345", "product_id": "PRD-101", "quantity": 6, "idempotency_key": "res-WF-ABC12345"})


async def test_write_rate_limit(make_client, api):
    api.post("http://localhost:8084/api/inventory/reservations").mock(
        return_value=httpx.Response(200, json={"reservationId": "RSV-1", "status": "RESERVED"}))
    client = make_client(mcp_writes_per_minute=2)
    async with client as c:
        args = {"workflow_id": "WF-ABC12345", "product_id": "PRD-101", "quantity": 1, "idempotency_key": "res-WF-ABC12345"}
        await c.call_tool("reserve_inventory", args)
        await c.call_tool("reserve_inventory", args)
        with pytest.raises(ToolError, match="RATE_LIMITED"):
            await c.call_tool("reserve_inventory", args)


async def test_write_tools_can_be_disabled(make_client):
    with pytest.raises(ToolError, match="WRITE_TOOLS_DISABLED"):
        await call(make_client(mcp_write_tools_enabled=False), "initiate_refund", REFUND_ARGS)


async def test_refused_refund_is_reported_not_hidden(make_client, api):
    api.post("http://localhost:8083/api/payments/refunds").mock(return_value=httpx.Response(
        403, json={"code": "APPROVAL_INVALID", "detail": "Approval is not APPROVED"}))
    with pytest.raises(ToolError, match="APPROVAL_INVALID"):
        await call(make_client(), "initiate_refund", REFUND_ARGS)


async def test_post_to_unreachable_service_says_not_to_claim_success(make_client, api):
    api.post("http://localhost:8083/api/payments/refunds").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ToolError, match="Do not claim success"):
        await call(make_client(), "initiate_refund", REFUND_ARGS)


async def test_notify_customer_uses_session_identity_not_arguments(make_client, api):
    route = api.post("http://localhost:8089/api/notifications/customer").mock(
        return_value=httpx.Response(200, json={"notificationId": 1, "status": "SENT", "message": "ok"}))
    await call(make_client(), "notify_customer", {"workflow_id": "WF-ABC12345", "template": "MANUAL_REVIEW", "params": {"orderId": "ORD-1"}})
    sent = json.loads(route.calls[0].request.content)
    assert sent["customerId"] == "CUST-1001" and sent["template"] == "MANUAL_REVIEW"


async def test_support_task_goes_to_internal_channel(make_client, api):
    route = api.post("http://localhost:8089/api/notifications/internal").mock(
        return_value=httpx.Response(200, json={"notificationId": 2, "status": "SENT", "message": "x"}))
    await call(make_client(), "create_support_task", {"workflow_id": "WF-ABC12345", "summary": "replacement failed"})
    assert json.loads(route.calls[0].request.content)["channel"] == "SLACK"
