"""The policy agent's job: turn a RAG result + the deterministic validation into a PolicyDecision."""
from app.agents import policy_agent
from app.config import Settings
from app.graph.deps import Deps
from app.models.order import OrderSnapshot
from app.models.shipment import ShipmentSnapshot
from app.models.workflow_state import WorkflowState
from app.rag.models import RagResult, RagSource


def rag(grounded=True, insufficient=False, dropped=()):
    src = [] if insufficient else [RagSource(policyId="9", chunkId="1", policyKey="refund-policy", version="2", source="Refund Policy",
                                             section="Delayed shipments", score=0.9, rerankScore=0.8, text="delayed 3 days refund")]
    return RagResult(answer="Delayed orders are refunded. [1]", confidence=0.82, grounded=grounded, policyVersion="2", sources=src,
                     insufficientEvidence=insufficient, diagnostics={"context": {"dropped": list(dropped)}})


class GW:
    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"refundEligible": True, "approvalRequired": True, "reasons": ["SHIPMENT_LOST"], "policyKey": "refund-policy", "policyVersion": "2"}


class Wf:
    audits = []

    async def audit(self, wf, agent, event, payload, cust=None):
        self.audits.append(event)


def setup(monkeypatch, rag_result, agent_calls_tool):
    deps = Deps(Settings(), models=None, workflows=Wf())
    Wf.audits = []
    gw = GW()
    deps.ensure("WF-1", "CUST-1001", "ORD-7842").gateway = gw
    state = WorkflowState(workflow_id="WF-1", customer_id="CUST-1001", order_id="ORD-7842", customer_message="late",
                          order=OrderSnapshot(order_id="ORD-7842", status="SHIPPED", payment_status="CAPTURED", total_amount=129.99),
                          shipment=ShipmentSnapshot(order_id="ORD-7842", status="LOST", delay_days=5, exception_type="LOST"))

    class FakePipeline:
        async def answer(self, q, filters=None):
            return rag_result
    monkeypatch.setattr(policy_agent, "build_pipeline", lambda *a, **k: FakePipeline())

    async def fake_react(deps_, ctx, agent, system, prompt, config, response_format=None, extra_tools=None):
        if agent_calls_tool:
            ctx.tool_log.append({"agent": "policy", "tool": "ask_policy", "ok": True, "result": rag_result.model_dump_json()})
        return "agent summary", None
    monkeypatch.setattr(policy_agent, "run_react", fake_react)
    return deps, state, gw


async def test_decision_combines_grounded_evidence_with_deterministic_eligibility(monkeypatch):
    deps, state, gw = setup(monkeypatch, rag(), agent_calls_tool=True)
    out = await policy_agent.run(state, deps, {})
    p = out["policy"]
    assert p.refund_eligible and p.grounded and p.rag_confidence == 0.82 and not p.insufficient_evidence
    assert [(c.policy_key, c.version) for c in p.citations] == [("refund-policy", "2")]
    assert gw.calls[0][0] == "validate_refund_eligibility"
    assert gw.calls[0][1]["delay_days"] == 5 and gw.calls[0][1]["refund_amount"] == 129.99    # facts from tools, not from the model
    assert "POLICY_SEARCH_DIRECT" not in out["errors"]


async def test_if_the_model_skips_retrieval_the_pipeline_is_run_directly(monkeypatch):
    deps, state, _ = setup(monkeypatch, rag(), agent_calls_tool=False)
    out = await policy_agent.run(state, deps, {})
    assert "POLICY_SEARCH_DIRECT" in out["errors"] and out["policy"].grounded


async def test_insufficient_evidence_is_carried_through_so_the_decision_escalates(monkeypatch):
    deps, state, _ = setup(monkeypatch, rag(grounded=False, insufficient=True), agent_calls_tool=True)
    p = (await policy_agent.run(state, deps, {}))["policy"]
    assert p.insufficient_evidence and not p.grounded and p.citations == []


async def test_withheld_policy_text_raises_an_injection_audit_event(monkeypatch):
    deps, state, _ = setup(monkeypatch, rag(dropped=["injection:77"]), agent_calls_tool=True)
    p = (await policy_agent.run(state, deps, {}))["policy"]
    assert p.injection_suspected and "PROMPT_INJECTION_SUSPECTED_IN_POLICY" in Wf.audits


async def test_missing_order_or_shipment_facts_skip_the_policy_step(monkeypatch):
    deps, state, _ = setup(monkeypatch, rag(), agent_calls_tool=True)
    state = state.model_copy(update={"shipment": None})
    assert (await policy_agent.run(state, deps, {}))["errors"] == ["POLICY_SKIPPED_MISSING_FACTS"]
