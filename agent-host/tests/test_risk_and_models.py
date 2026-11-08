from app.agents.risk_agent import assess
from app.llm import ModelFactory, UsageTracker, model_name
from app.config import Settings
from app.models.order import OrderSnapshot
from app.models.shipment import ShipmentSnapshot


def order(amount=129.99, pay="CAPTURED"):
    return OrderSnapshot(order_id="ORD-1", status="SHIPPED", payment_status=pay, total_amount=amount)


def ship(exc="LOST", verified=True):
    return ShipmentSnapshot(order_id="ORD-1", verified=verified, exception_type=exc, status=exc, delay_days=5)


def test_risk_levels():
    assert assess(order(50), ship(), None, 100, 500).level == "LOW"
    assert assess(order(129.99), ship(), None, 100, 500).level == "MEDIUM"       # confirmed loss, but a high-value order
    assert assess(order(129.99), ship("NONE"), None, 100, 500).level == "HIGH"  # high value and the carrier shows no problem: possible false claim
    assert assess(order(), ship(verified=False), None, 100, 500).level in ("MEDIUM", "HIGH")
    assert assess(order(900), ship(), None, 100, 500).level in ("MEDIUM", "HIGH")
    assert assess(None, None, None, 100, 500).level == "HIGH"


def test_model_routing_ignores_names_from_the_wrong_provider():
    s = Settings(model_provider="openai", openai_model="gpt-4o-mini", model_decision_agent="claude-sonnet-5-5")
    assert model_name(s, "decision") == "gpt-4o-mini"
    s2 = Settings(model_provider="openai", model_decision_agent="gpt-4o")
    assert model_name(s2, "decision") == "gpt-4o"
    s3 = Settings(model_provider="anthropic", model_response_agent="claude-haiku-5-5")
    assert model_name(s3, "response") == "claude-haiku-5-5"


def test_fallback_only_when_other_provider_has_a_key():
    assert ModelFactory(Settings(model_provider="openai", openai_api_key="k")).fallback("decision") is None
    assert ModelFactory(Settings(model_provider="openai", openai_api_key="k", anthropic_api_key="a")).fallback("decision") is not None


async def test_usage_tracker_enforces_budget():
    import pytest
    from types import SimpleNamespace
    from uuid import uuid4
    from app.llm import BudgetExceeded
    t = UsageTracker(budget=100)
    rid = uuid4()
    await t.on_chat_model_start({}, [], run_id=rid, metadata={"agent": "decision", "ls_model_name": "gpt-4o-mini"})
    msg = SimpleNamespace(usage_metadata={"input_tokens": 80, "output_tokens": 50})
    resp = SimpleNamespace(generations=[[SimpleNamespace(message=msg)]])
    with pytest.raises(BudgetExceeded):
        await t.on_llm_end(resp, run_id=rid)
    assert t.by_agent["decision"]["calls"] == 1 and t.cost_usd > 0


async def test_product_agent_falls_back_to_category_search(monkeypatch):
    """If the model's searches return nothing, the agent searches the original item's category itself."""
    from app.agents import product_agent
    from app.graph.deps import Deps
    from app.models.workflow_state import WorkflowState

    class GW:
        calls = []

        async def call(self, name, args):
            self.calls.append(name)
            if name == "check_inventory":
                return {"category": "headphones"}
            return {"products": [
                {"productId": "PRD-103", "name": "Earbuds", "price": 79.99, "availableQty": 40},
                {"productId": "PRD-999", "name": "Pricey", "price": 300.0, "availableQty": 5}]}

    async def no_agent(*a, **k):
        return "", None
    monkeypatch.setattr(product_agent, "run_react", no_agent)
    deps = Deps(Settings(), models=None, workflows=None)
    state = WorkflowState(workflow_id="WF-1", customer_id="CUST-1001", order_id="ORD-1", customer_message="m",
                          order=OrderSnapshot(order_id="ORD-1", status="SHIPPED", payment_status="CAPTURED", total_amount=129.99,
                                              product_id="PRD-100", quantity=1))
    deps.ensure("WF-1", "CUST-1001", "ORD-1").gateway = GW()
    out = await product_agent.run(state, deps, {})
    rec = out["product_recommendation"]
    assert rec.product_id == "PRD-103" and [c.product_id for c in rec.candidates] == ["PRD-103"]   # over-budget item excluded
    assert "PRODUCT_SEARCH_DIRECT" in out["errors"] and "PRODUCT_PICK_REPLACED_BY_RULE" in out["errors"]


async def test_gateway_retries_rate_limits_but_not_other_errors():
    import pytest
    from app.guardrails.tool_guardrails import ToolCallError
    from app.mcp.client import McpGateway

    class Tool:
        def __init__(self, errors):
            self.errors, self.calls = errors, 0

        async def ainvoke(self, args):
            self.calls += 1
            if self.errors:
                raise RuntimeError(self.errors.pop(0))
            return [{"type": "text", "text": '{"ok": true}'}]

    gw = McpGateway(Settings(), "CUST-1001", "WF-1")
    gw.RATE_LIMIT_WAIT = 0
    limited = Tool(["RATE_LIMITED: slow down"])
    gw._tools = {"t": limited, "u": Tool(["APPROVAL_INVALID: no"])}
    assert await gw.call("t", {}) == {"ok": True} and limited.calls == 2
    with pytest.raises(ToolCallError, match="APPROVAL_INVALID"):
        await gw.call("u", {})
    assert gw._tools["u"].calls == 1                       # a real refusal is never retried
