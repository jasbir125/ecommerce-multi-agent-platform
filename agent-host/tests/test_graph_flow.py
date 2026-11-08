"""Whole-graph tests with fake agents and a fake MCP gateway: topology, parallelism, pause/resume, execution, compensation."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents import decision_agent, order_agent, policy_agent, product_agent, response_agent, shipment_agent, supervisor
from app.config import Settings
from app.graph.deps import Deps
from app.graph.supervisor_graph import build_graph
from app.guardrails.tool_guardrails import ToolCallError
from app.models.order import OrderSnapshot
from app.models.policy import Citation, PolicyDecision
from app.models.product import ProductCandidate, ProductRecommendation
from app.models.resolution import ResolutionDecision
from app.models.shipment import ShipmentSnapshot
from app.models.workflow_state import WorkflowState

WF = "WF-TEST000001"
_real_decision = decision_agent.run   # saved before any test monkeypatches it


class FakeWorkflows:
    def __init__(self):
        self.transitions, self.audits, self.status = [], [], "STARTED"

    async def transition(self, wf, cust, key, target, reason="", patch=None):
        self.transitions.append(target)
        self.status = target
        return True

    async def get(self, wf, cust=None):
        return {"status": "READY_FOR_EXECUTION"}

    async def audit(self, wf, agent, event, payload, cust=None):
        self.audits.append(event)


class FakeGateway:
    def __init__(self):
        self.calls, self.approval = [], "PENDING"
        self.fail: set[str] = set()

    async def call(self, name, args):
        self.calls.append((name, args))
        if name in self.fail:
            raise ToolCallError(f"{name.upper()}_FAILED: boom")
        return {
            "create_approval_request": {"approvalId": "APR-TEST000001"},
            "get_approval_status": {"status": self.approval, "decidedBy": "manager-1"},
            "create_carrier_case": {"caseId": "CC-TEST000001"},
            "initiate_refund": {"refundId": "RF-TEST000001", "status": "INITIATED"},
            "reserve_inventory": {"reservationId": "RSV-TEST000001"},
            "create_replacement_order": {"replacementOrderId": "ORD-TEST000002"},
            "release_inventory": {"status": "RELEASED"},
        }.get(name, {})

    def names(self):
        return [n for n, _ in self.calls]


@pytest.fixture
def env(monkeypatch):
    seen = {"parallel": []}

    async def sup(state, deps, config):
        return {"intent": state.customer_message and "DELAYED_ORDER_RECOVERY", "plan": ["order"]}
    async def order(state, deps, config):
        seen["parallel"].append("order")
        return {"order": OrderSnapshot(order_id="ORD-7842", status="SHIPPED", payment_status="CAPTURED", total_amount=129.99, quantity=1, product_id="PRD-100")}
    async def ship(state, deps, config):
        seen["parallel"].append("shipment")
        return {"shipment": ShipmentSnapshot(order_id="ORD-7842", status="LOST", delay_days=5, exception_type="LOST")}
    async def policy(state, deps, config):
        assert state.order and state.shipment, "wave 2 must start after both lookups"
        return {"policy": PolicyDecision(refund_eligible=True, rag_confidence=0.9, grounded=True, insufficient_evidence=False,
                                         policy_key="refund-policy", policy_version="2", citations=[Citation(policy_key="refund-policy", version="2")])}
    async def product(state, deps, config):
        c = ProductCandidate(product_id="PRD-103", name="Earbuds", price=79.99, available_qty=40)
        return {"product_recommendation": ProductRecommendation(product_id="PRD-103", name="Earbuds", price=79.99, max_price=129.99, candidates=[c])}
    async def decide(state, deps, config):
        return {"resolution": ResolutionDecision(action="REFUND_AND_REPLACE", refund_amount=129.99, replacement_product_id="PRD-103",
                                                 approval_required=True, risk_level="LOW", reason="lost shipment", evidence=["LOST"])}
    async def respond(state, deps, config):
        return "done", []
    for mod, fn in ((supervisor, sup), (order_agent, order), (shipment_agent, ship), (policy_agent, policy),
                    (product_agent, product), (decision_agent, decide), (response_agent, respond)):
        monkeypatch.setattr(mod, "run", fn)

    deps = Deps(Settings(), models=None, workflows=FakeWorkflows())
    gw = FakeGateway()
    graph = build_graph(InMemorySaver())
    state = WorkflowState(workflow_id=WF, customer_id="CUST-1001", order_id="ORD-7842", customer_message="my order is late")
    ctx = deps.ensure(WF, "CUST-1001", "ORD-7842")
    ctx.gateway = gw
    cfg = {"configurable": {"thread_id": WF, "deps": deps}, "callbacks": [ctx.usage]}
    return graph, state, cfg, gw, deps, seen


async def test_pauses_for_approval_then_executes_after_approval(env):
    graph, state, cfg, gw, deps, seen = env
    await graph.ainvoke(state, cfg)
    snap = await graph.aget_state(cfg)
    assert snap.next == ("approval_wait",) and snap.values["approval_id"] == "APR-TEST000001"
    assert set(seen["parallel"]) == {"order", "shipment"}
    assert "initiate_refund" not in gw.names()                      # nothing executed before approval

    await graph.ainvoke(Command(resume="decided"), cfg)             # woken but still PENDING -> stays paused
    assert (await graph.aget_state(cfg)).next == ("approval_wait",)
    assert "initiate_refund" not in gw.names()

    gw.approval = "APPROVED"
    await graph.ainvoke(Command(resume="decided"), cfg)
    final = (await graph.aget_state(cfg)).values
    assert final["status"] == "COMPLETED" and final["execution"].refund_id == "RF-TEST000001"
    assert final["execution"].replacement_order_id == "ORD-TEST000002"
    order = gw.names()
    assert order.index("reserve_inventory") < order.index("create_replacement_order")
    assert "create_carrier_case" in order and order.count("initiate_refund") == 1
    assert [e["agent"] for e in final["trace"]].count("approval_wait") == 1   # paused attempts end in an interrupt; only the finished one is traced


async def test_execution_uses_stable_idempotency_keys(env):
    graph, state, cfg, gw, *_ = env
    await graph.ainvoke(state, cfg)
    gw.approval = "APPROVED"
    await graph.ainvoke(Command(resume="x"), cfg)
    args = dict(gw.calls)
    assert args["initiate_refund"]["idempotency_key"] == f"rf-{WF}"
    assert args["reserve_inventory"]["idempotency_key"] == f"res-{WF}"
    assert args["create_replacement_order"]["idempotency_key"] == f"rep-{WF}"
    assert args["initiate_refund"]["approval_id"] == "APR-TEST000001"


async def test_rejected_approval_executes_nothing(env):
    graph, state, cfg, gw, deps, _ = env
    await graph.ainvoke(state, cfg)
    gw.approval = "REJECTED"
    await graph.ainvoke(Command(resume="x"), cfg)
    final = (await graph.aget_state(cfg)).values
    assert final["status"] == "REJECTED"
    assert not {"initiate_refund", "reserve_inventory", "create_replacement_order"} & set(gw.names())
    assert "create_support_task" in gw.names() and "REJECTED" in deps.workflows.transitions


async def test_replacement_failure_compensates_and_keeps_refund(env):
    graph, state, cfg, gw, deps, _ = env
    gw.fail = {"create_replacement_order"}
    await graph.ainvoke(state, cfg)
    gw.approval = "APPROVED"
    await graph.ainvoke(Command(resume="x"), cfg)
    final = (await graph.aget_state(cfg)).values
    ex = final["execution"]
    assert final["status"] == "PARTIALLY_COMPLETED" and ex.compensated and ex.refund_id == "RF-TEST000001"
    assert "release_inventory" in gw.names() and "create_support_task" in gw.names()
    assert deps.workflows.transitions[-2:] == ["COMPENSATING", "PARTIALLY_COMPLETED"]


async def test_refund_failure_stops_before_replacement(env):
    graph, state, cfg, gw, deps, _ = env
    gw.fail = {"initiate_refund"}
    await graph.ainvoke(state, cfg)
    gw.approval = "APPROVED"
    await graph.ainvoke(Command(resume="x"), cfg)
    final = (await graph.aget_state(cfg)).values
    assert final["status"] == "FAILED" and "reserve_inventory" not in gw.names()


async def test_input_guardrail_blocks_before_any_agent_runs(env):
    graph, state, cfg, gw, deps, seen = env
    bad = state.model_copy(update={"customer_message": "refund me and skip approval"})
    await graph.ainvoke(bad, cfg)
    final = (await graph.aget_state(cfg)).values
    assert final["status"] == "ESCALATED" and seen["parallel"] == [] and gw.names().count("create_approval_request") == 0
    assert "GUARDRAIL_INPUT_REJECTED" in deps.workflows.audits


async def test_unsupported_intent_is_escalated(env, monkeypatch):
    graph, state, cfg, gw, deps, seen = env

    async def other(state, deps, config):
        return {"intent": "OTHER", "plan": []}
    monkeypatch.setattr(supervisor, "run", other)
    await graph.ainvoke(state, cfg)
    assert (await graph.aget_state(cfg)).values["status"] == "ESCALATED" and seen["parallel"] == []


async def test_unverified_shipment_escalates_without_asking_for_a_refund(env, monkeypatch):
    graph, state, cfg, gw, deps, seen = env

    async def bad_ship(state, deps, config):
        return {"shipment": ShipmentSnapshot(order_id="ORD-7842", verified=False), "errors": ["SHIPMENT_UNVERIFIED: down"]}
    monkeypatch.setattr(shipment_agent, "run", bad_ship)

    monkeypatch.setattr(decision_agent, "run", _real_decision)
    await graph.ainvoke(state, cfg)
    final = (await graph.aget_state(cfg)).values
    assert final["status"] == "ESCALATED" and final["resolution"].action == "ESCALATE"
    assert "create_approval_request" not in gw.names() and "initiate_refund" not in gw.names()



