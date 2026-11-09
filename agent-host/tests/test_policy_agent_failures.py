"""Policy Agent branches not covered in test_policy_agent.py: tool failure, timeout, malformed results, unauthorized access."""
import json

import pytest

from app.agents import policy_agent
from app.guardrails.tool_guardrails import ToolCallError
from app.llm import BudgetExceeded
from app.rag.models import RagResult
from tests.agent_fakes import FakeGateway, make_deps, make_state, order, patch_react, shipment

VERDICT = {"refundEligible": True, "approvalRequired": True, "reasons": ["SHIPMENT_LOST"], "policyKey": "refund-policy", "policyVersion": "2"}
RAG = RagResult(answer="Lost parcels are refunded. [1]", confidence=0.8, grounded=True, policyVersion="2", sources=[], insufficientEvidence=False)


class Pipeline:
    def __init__(self, result=RAG):
        self.result = result

    async def answer(self, question, filters=None):
        return self.result


async def run(monkeypatch, gateway=None, calls=None, error=None, state=None, pipeline=None):
    monkeypatch.setattr(policy_agent, "build_pipeline", lambda *a, **k: pipeline or Pipeline())
    patch_react(monkeypatch, policy_agent, tool_calls=calls, error=error)
    deps, workflows = make_deps(gateway or FakeGateway({"validate_refund_eligibility": VERDICT}))
    state = state or make_state(order=order(), shipment=shipment())
    return await policy_agent.run(state, deps, {}), workflows


async def test_eligibility_tool_failure_returns_a_coded_error_and_no_policy(monkeypatch):
    out, _ = await run(monkeypatch, FakeGateway({"validate_refund_eligibility": ToolCallError("UPSTREAM_503: down")}))
    assert out["errors"][0].startswith("POLICY_VALIDATION_FAILED") and "policy" not in out


async def test_eligibility_timeout_is_a_validation_failure(monkeypatch):
    out, _ = await run(monkeypatch, FakeGateway({"validate_refund_eligibility": TimeoutError("slow")}))
    assert out["errors"][0].startswith("POLICY_VALIDATION_FAILED")


async def test_unauthorized_order_access_is_refused_by_the_service_not_by_the_agent(monkeypatch):
    out, _ = await run(monkeypatch, FakeGateway({"validate_refund_eligibility": ToolCallError("NOT_ORDER_OWNER: no")}))
    assert "NOT_ORDER_OWNER" in out["errors"][0] and "policy" not in out


async def test_model_failure_is_recorded_and_retrieval_runs_directly(monkeypatch):
    out, _ = await run(monkeypatch, error=TimeoutError("model"))
    assert "POLICY_AGENT_ERROR: TimeoutError" in out["errors"] and "POLICY_SEARCH_DIRECT" in out["errors"] and out["policy"].grounded


async def test_a_spent_budget_propagates(monkeypatch):
    with pytest.raises(BudgetExceeded):
        await run(monkeypatch, error=BudgetExceeded("budget"))


async def test_malformed_tool_result_falls_back_to_direct_retrieval(monkeypatch):
    bad = {"tool": "ask_policy", "ok": True, "result": json.dumps({"confidence": "not a number", "grounded": "maybe"})}
    out, _ = await run(monkeypatch, calls=[bad])
    assert "POLICY_RESULT_MALFORMED" in out["errors"] and "POLICY_SEARCH_DIRECT" in out["errors"]


async def test_eligibility_is_sent_the_verified_amount_as_a_number(monkeypatch):
    gw = FakeGateway({"validate_refund_eligibility": VERDICT})
    await run(monkeypatch, gw)
    sent = gw.calls[0][1]
    assert sent["refund_amount"] == 129.99 and isinstance(sent["refund_amount"], float) and sent["order_id"] == "ORD-7842"


async def test_the_model_cannot_change_eligibility(monkeypatch):
    """Even if the policy text says refunds are fine, eligibility is exactly what the validation service returned."""
    denied = {**VERDICT, "refundEligible": False, "reasons": ["WINDOW_EXPIRED"]}
    out, _ = await run(monkeypatch, FakeGateway({"validate_refund_eligibility": denied}))
    assert out["policy"].refund_eligible is False and out["policy"].reasons == ["WINDOW_EXPIRED"]


async def test_insufficient_evidence_is_explicit(monkeypatch):
    weak = RagResult(answer="I could not verify this.", confidence=0.2, grounded=False, sources=[], insufficientEvidence=True)
    out, _ = await run(monkeypatch, pipeline=Pipeline(weak))
    assert out["policy"].insufficient_evidence and out["policy"].citations == [] and not out["policy"].grounded
