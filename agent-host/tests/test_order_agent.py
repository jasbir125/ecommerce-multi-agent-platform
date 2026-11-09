"""Order Agent: verified order and payment facts only; ownership enforced by the service; never cancels or refunds."""
import json
from decimal import Decimal

import pytest

from app.agents import order_agent
from app.guardrails.tool_guardrails import ToolCallError
from app.llm import BudgetExceeded
from tests.agent_fakes import FakeGateway, make_deps, make_state, patch_react

ORDER = {"orderId": "ORD-7842", "status": "SHIPPED", "paymentStatus": "CAPTURED", "totalAmount": 129.99, "currency": "USD",
         "productId": "PRD-100", "productName": "Headphones", "quantity": 1}


def tool_call(payload=ORDER):
    return {"tool": "get_order_status", "ok": True, "result": json.dumps(payload)}


async def run(monkeypatch, gateway=None, calls=None, error=None, state=None):
    patch_react(monkeypatch, order_agent, tool_calls=calls, error=error)
    deps, workflows = make_deps(gateway or FakeGateway({"get_order_status": ORDER}))
    out = await order_agent.run(state or make_state(), deps, {})
    return out, workflows


async def test_happy_path_uses_the_tool_result(monkeypatch):
    gw = FakeGateway()
    out, _ = await run(monkeypatch, gw, calls=[tool_call()])
    assert out["order"].total_amount == Decimal("129.99") and out["order"].payment_status == "CAPTURED"
    assert out["errors"] == [] and gw.calls == []            # no direct lookup was needed
    assert out["evidence"] == ["order ORD-7842: SHIPPED, paid 129.99 USD"]


async def test_model_skipping_the_lookup_triggers_a_direct_read(monkeypatch):
    out, _ = await run(monkeypatch)
    assert "ORDER_LOOKUP_DIRECT" in out["errors"] and out["order"].order_id == "ORD-7842"


async def test_missing_ids_skip_the_agent(monkeypatch):
    out, _ = await run(monkeypatch, state=make_state(order_id=""))
    assert out["errors"] == ["ORDER_SKIPPED_MISSING_IDS"] and "order" not in out


async def test_another_customers_order_is_reported_as_access_denied_and_audited(monkeypatch):
    gw = FakeGateway({"get_order_status": ToolCallError("NOT_ORDER_OWNER: not your order")})
    out, workflows = await run(monkeypatch, gw)
    assert out["errors"][0].startswith("ORDER_ACCESS_DENIED") and "order" not in out
    assert "ORDER_LOOKUP_FAILED" in workflows.events()


async def test_tool_failure_leaves_the_order_unverified(monkeypatch):
    out, workflows = await run(monkeypatch, FakeGateway({"get_order_status": ToolCallError("UPSTREAM_503: down")}))
    assert out["errors"][0].startswith("ORDER_UNVERIFIED") and "order" not in out and workflows.events() == ["ORDER_LOOKUP_FAILED"]


async def test_timeout_from_the_tool_is_an_unverified_order(monkeypatch):
    out, _ = await run(monkeypatch, FakeGateway({"get_order_status": TimeoutError("slow")}))
    assert out["errors"][0].startswith("ORDER_UNVERIFIED")


async def test_model_failure_is_recorded_and_the_direct_read_still_verifies(monkeypatch):
    out, _ = await run(monkeypatch, error=TimeoutError("model"))
    assert "ORDER_AGENT_ERROR: TimeoutError" in out["errors"] and out["order"] is not None


async def test_a_spent_budget_propagates(monkeypatch):
    with pytest.raises(BudgetExceeded):
        await run(monkeypatch, error=BudgetExceeded("budget"))


@pytest.mark.parametrize("payload", [{**ORDER, "orderId": "ORD-OTHER"}, {**ORDER, "totalAmount": "abc"}, {**ORDER, "totalAmount": 0},
                                     {**ORDER, "totalAmount": -5}, {k: v for k, v in ORDER.items() if k != "paymentStatus"}])
async def test_invalid_or_mismatched_order_data_is_unverified(monkeypatch, payload):
    out, _ = await run(monkeypatch, FakeGateway({"get_order_status": payload}))
    assert out["errors"][0].startswith("ORDER_UNVERIFIED") and "order" not in out


async def test_duplicate_tool_results_use_the_latest(monkeypatch):
    newer = {**ORDER, "status": "DELIVERED"}
    out, _ = await run(monkeypatch, FakeGateway(), calls=[tool_call(), tool_call(newer)])
    assert out["order"].status == "DELIVERED"


async def test_the_agent_never_calls_a_write_tool(monkeypatch):
    gw = FakeGateway({"get_order_status": ORDER})
    await run(monkeypatch, gw)
    assert set(gw.names()) <= {"get_order_status"}
