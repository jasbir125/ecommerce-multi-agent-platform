"""End-to-end smoke test: talks to the running MCP servers over HTTP exactly like the agent host will.

Needs the stack up (docker compose --profile apps up -d). Usage:
    python mcp-servers/scripts/smoke_test.py            # uses the demo token and CUST-1001
"""
import asyncio
import os
import uuid

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

TOKEN = os.getenv("MCP_SERVICE_TOKEN", "local-demo-token")
CUSTOMER = os.getenv("SMOKE_CUSTOMER", "CUST-1001")
ORDER = os.getenv("SMOKE_ORDER", "ORD-1234")      # CONFIRMED, $74.50, owned by CUST-1001
LOST_ORDER = "ORD-7842"                           # shipment LOST


def client(url: str, workflow_id: str | None = None, customer: str = CUSTOMER) -> Client:
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Customer-Id": customer, "X-Correlation-Id": str(uuid.uuid4())}
    if workflow_id:
        headers["X-Workflow-Id"] = workflow_id
    return Client(StreamableHttpTransport(url, headers=headers))


async def expect_error(label: str, coro, contains: str) -> None:
    try:
        await coro
    except ToolError as e:
        ok = contains in str(e)
        print(f"{'PASS' if ok else 'FAIL'}  {label}: {str(e)[:90]}")
        assert ok, e
    else:
        raise AssertionError(f"{label}: expected an error containing {contains!r}")


async def main() -> None:
    svc = {"X-Service-Token": TOKEN, "X-Customer-Id": CUSTOMER}
    wf = httpx.post("http://localhost:8088/api/workflows", headers=svc, json={
        "orderId": ORDER, "message": "Order delayed, refund me", "requestId": str(uuid.uuid4())}).json()["workflowId"]
    print("workflow", wf)

    async with client("http://localhost:8090/mcp", wf) as eco, client("http://localhost:8091/mcp", wf) as pol:
        print("ecommerce tools:", len(await eco.list_tools()), "| policy tools:", len(await pol.list_tools()))

        shipment = (await eco.call_tool("get_shipment_status", {"order_id": LOST_ORDER})).data
        print("shipment:", shipment["status"], shipment["exceptionType"], f"{shipment['delayDays']}d")
        order = (await eco.call_tool("get_order_status", {"order_id": ORDER})).data
        print("order:", order["status"], order["totalAmount"])

        await expect_error("other customer's order blocked", eco.call_tool("get_order_status", {"order_id": "ORD-9001"}), "NOT_ORDER_OWNER")

        rag = (await pol.call_tool("search_policy", {"query": "Is a delayed order eligible for a refund?"})).data
        print("policy:", rag["policyVersion"], rag["confidence"], [s["policyKey"] for s in rag["sources"]])
        none = (await pol.call_tool("search_policy", {"query": "warranty on quantum toasters"})).data
        assert none["insufficientEvidence"] and none["answer"].startswith("I could not verify")
        print("PASS  insufficient evidence handled")
        v = (await pol.call_tool("validate_refund_eligibility", {
            "order_id": LOST_ORDER, "order_amount": 129.99, "refund_amount": 129.99, "delay_days": 5, "shipment_status": "LOST"})).data
        print("validate:", v["refundEligible"], v["reasons"], v["citation"])

        # write-tool guardrails (rejected before reaching any service)
        await expect_error("amount over limit", eco.call_tool("initiate_refund", {
            "workflow_id": wf, "order_id": ORDER, "amount": 9999, "approval_id": "APR-X", "idempotency_key": f"rf-{wf}"}), "AMOUNT_OUT_OF_RANGE")
        await expect_error("wrong workflow for this session", eco.call_tool("create_carrier_case", {
            "workflow_id": "WF-SOMEONEELSE", "order_id": LOST_ORDER}), "WORKFLOW_MISMATCH")

        # refund: request approval, show it cannot be skipped, approve as a human, then refund
        amount = float(order["totalAmount"])
        apr = (await eco.call_tool("create_approval_request", {
            "workflow_id": wf, "requested_action": "REFUND", "amount": amount, "reason": "delayed order", "risk_level": "LOW"})).data
        await expect_error("refund blocked while approval PENDING", eco.call_tool("initiate_refund", {
            "workflow_id": wf, "order_id": ORDER, "amount": amount, "approval_id": apr["approvalId"], "idempotency_key": f"rf-{wf}"}), "APPROVAL_INVALID")
        d = httpx.post(f"http://localhost:8085/api/approvals/{apr['approvalId']}/decision", headers={**svc, "X-Roles": "MANAGER"},
                       json={"decision": "APPROVED", "decidedBy": "manager-1"}).json()
        print("human decision:", d["status"])
        args = {"workflow_id": wf, "order_id": ORDER, "amount": amount, "approval_id": apr["approvalId"], "idempotency_key": f"rf-{wf}"}
        r1 = (await eco.call_tool("initiate_refund", args)).data
        r2 = (await eco.call_tool("initiate_refund", args)).data
        assert r1["refundId"] == r2["refundId"] and r1["status"] == "INITIATED"
        print("PASS  refund", r1["refundId"], r1["status"], "(duplicate call returned same refund)")

        # replacement: reservation first, then order; then compensation
        prods = (await eco.call_tool("search_products", {"category": "headphones", "max_price": 100})).data["products"]
        pid = prods[0]["productId"]
        rsv = (await eco.call_tool("reserve_inventory", {
            "workflow_id": wf, "product_id": pid, "quantity": 1, "idempotency_key": f"res-{wf}"})).data
        rep = (await eco.call_tool("create_replacement_order", {
            "workflow_id": wf, "original_order_id": ORDER, "reservation_id": rsv["reservationId"], "idempotency_key": f"rep-{wf}"})).data
        print("PASS  replacement", rep["replacementOrderId"], "for", pid, rep["amount"])
        rel = (await eco.call_tool("release_inventory", {"workflow_id": wf, "reservation_id": rsv["reservationId"]})).data
        print("compensation: reservation", rel["status"])

        case = (await eco.call_tool("create_carrier_case", {"workflow_id": wf, "order_id": LOST_ORDER, "reason": "lost"})).data
        print("carrier case:", case["caseId"], "created" if case["created"] else "already existed")

        res = await eco.read_resource(f"ecommerce://orders/{ORDER}")
        print("resource ok:", "orderId" in res[0].text)
        prompt = await eco.get_prompt("delayed_shipment_investigation", {"order_id": ORDER})
        print("prompt ok:", "Investigate order" in prompt.messages[0].content.text)

    audit = httpx.get(f"http://localhost:8087/api/audit/workflows/{wf}", headers=svc).json()
    print("audit events:", sorted({a["eventType"] for a in audit if a["eventType"].startswith("TOOL_")}))


asyncio.run(main())
