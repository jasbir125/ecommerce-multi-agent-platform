"""Runs a test case through the real LangGraph workflow with a scripted model and fake services (no network, no model).

Used for scenarios that need the model to misbehave on purpose (invalid output, hallucinated claims, wrong retrieval),
which cannot be produced reliably from a real model. Everything else is the production code: guardrails, the decision
and response agents, the RAG pipeline and validator, the approval pause and the deterministic execution.
"""
from __future__ import annotations

import sys
import uuid
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-host"))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.agents import decision_agent, order_agent, policy_agent, product_agent, shipment_agent, supervisor  # noqa: E402
from app.config import Settings  # noqa: E402
from app.graph.deps import Deps  # noqa: E402
from app.graph.supervisor_graph import build_graph  # noqa: E402
from app.guardrails.tool_guardrails import ToolCallError  # noqa: E402
from app.models.order import OrderSnapshot  # noqa: E402
from app.models.product import ProductCandidate, ProductRecommendation  # noqa: E402
from app.models.shipment import ShipmentSnapshot  # noqa: E402
from app.models.workflow_state import WorkflowState  # noqa: E402
from app.rag.answer_cache import AnswerCache  # noqa: E402
from app.rag.models import GroundedAnswer, Statement  # noqa: E402
from app.rag.pipeline import RagPipeline, RagSettings  # noqa: E402
from app.rag.reranker import Ranking  # noqa: E402
from app.rag.retriever import CircuitBreaker, PolicyRetriever  # noqa: E402

from .checks import Observed  # noqa: E402

CATALOG = {"PRD-101": ("Lite headphones", 99.99), "PRD-103": ("Earbuds", 79.99)}
GOOD_SOURCE = {"chunkId": "r2", "policyId": "9", "policyKey": "refund-policy", "version": "2", "source": "Refund Policy", "section": "Delayed shipments",
               "policyType": "refund", "score": 0.9, "rerankScore": 0.85, "injectionSuspected": False,
               "text": "Delayed shipments: Orders delayed by three (3) or more days beyond the promised delivery date are eligible for a full refund of the order amount."}


class FakeWorkflows:
    def __init__(self):
        self.audits: list[str] = []

    async def transition(self, *a, **k):
        return True

    async def get(self, wf, cust=None):
        return {"status": "READY_FOR_EXECUTION"}

    async def audit(self, wf, agent, event, payload, cust=None):
        self.audits.append(event)


class FakeGateway:
    """Stands in for the MCP servers: canned answers, recorded calls, and failures on request."""

    def __init__(self, setup: dict[str, Any]):
        self.setup, self.calls, self.call_log = setup, [], []
        self.approval = {"APPROVE": "APPROVED", "REJECT": "REJECTED"}.get(setup.get("approval", "APPROVE"), "PENDING")
        self.refund_amounts: list[float] = []
        self.replacement_product: str | None = None

    async def call(self, name: str, args: dict[str, Any]):
        self.calls.append(name)
        code = (self.setup.get("tool_failures") or {}).get(name)
        if code:
            raise ToolCallError(f"{code}: injected failure")
        order = self.setup["order"]
        ship = order.get("shipment")
        if name == "validate_refund_eligibility":
            eligible = bool(ship) and (ship["status"] == "LOST" or ship["delay_days"] >= 3)
            return {"refundEligible": eligible, "approvalRequired": True, "reasons": ["SHIPMENT_LOST" if eligible else "DELAY_BELOW_THRESHOLD"],
                    "policyKey": "refund-policy", "policyVersion": "2", "policyId": "9"}
        if name == "create_approval_request":
            return {"approvalId": "APR-EVAL000001"}
        if name == "get_approval_status":
            return {"status": self.approval, "decidedBy": "manager-1"}
        if name == "check_inventory":
            return {"category": "headphones", "price": order["amount"]}
        if name == "initiate_refund":
            self.refund_amounts.append(float(args["amount"]))
            return {"refundId": "RF-EVAL000001", "status": "INITIATED"}
        if name == "reserve_inventory":
            self.replacement_product = args["product_id"]
            return {"reservationId": "RSV-EVAL000001"}
        if name == "create_replacement_order":
            return {"replacementOrderId": "ORD-EVAL000002"}
        if name == "create_carrier_case":
            return {"caseId": "CC-EVAL000001"}
        return {}


class ScriptedModels:
    """Replaces the model factory: returns what the case scripts (or a sensible default), or raises."""

    def __init__(self, script: dict[str, Any], default_decision: dict[str, Any]):
        self.script, self.default_decision, self.n = script, default_decision, defaultdict(int)

    async def structured(self, agent: str, schema, messages, config=None):
        i = self.n[agent]
        self.n[agent] += 1
        if agent == "supervisor":
            return schema(intent="DELAYED_ORDER_RECOVERY", agents=["order"], reasoning="scripted")
        outputs = self.script.get(agent)
        if agent == "decision":
            outputs = outputs or [self.default_decision]
        if not outputs:
            raise RuntimeError("no scripted output")                      # the agent falls back to its deterministic path
        item = outputs[min(i, len(outputs) - 1)]
        if "__raise__" in item:
            raise RuntimeError(item["__raise__"])
        return schema(**item)

    def get(self, agent):
        raise RuntimeError("no model in component mode")


class FakeRagLlm:
    def __init__(self, rag: dict[str, Any]):
        self.rag = rag

    async def rewrite(self, question):
        return []

    async def rerank(self, question, chunks, scope=""):
        r = self.rag.get("rerank") or {"order": list(range(1, len(chunks) + 1)), "relevant": list(range(1, len(chunks) + 1))}
        return Ranking(**r)

    async def generate(self, question, pack, feedback, scope=""):
        if self.rag.get("generate"):
            return GroundedAnswer(**self.rag["generate"])
        first = pack.items[0]
        return GroundedAnswer(statements=[Statement(text=first.text.split(". ")[0].rstrip(".") + ".", cites=[first.n])])


async def run_component(case: dict[str, Any]) -> Observed:
    setup, script = case["setup"], case.get("script", {})
    order = setup["order"]
    order_id, wf = "ORD-EVAL000099", "WF-EVAL" + uuid.uuid4().hex[:6].upper()
    ship = order.get("shipment")
    cheaper = [(p, n, c) for p, (n, c) in CATALOG.items() if c < order["amount"]]
    default_decision = {"action": "REFUND_AND_REPLACE" if cheaper else "REFUND_ONLY", "refund_amount": order["amount"],
                        "replacement_product_id": max(cheaper, key=lambda x: x[2])[0] if cheaper else None, "approval_required": True,
                        "risk_level": "LOW", "reason": "scripted default", "evidence": ["verified facts"]}
    gateway, workflows = FakeGateway(setup), FakeWorkflows()
    models = ScriptedModels(script, default_decision)
    deps = Deps(Settings(), models=models, workflows=workflows)
    deps.ensure(wf, order["customer"], order_id).gateway = gateway
    agent_tools: set[str] = set()

    async def fake_order(state, deps_, config):
        agent_tools.update({"get_order_status", "get_payment_status"})
        return {"order": OrderSnapshot(order_id=order_id, status="SHIPPED", payment_status="CAPTURED", total_amount=order["amount"],
                                       product_id=order["product"], quantity=1)}

    async def fake_shipment(state, deps_, config):
        agent_tools.add("get_shipment_status")
        if not ship:
            await workflows.audit(wf, "shipment-agent", "SHIPMENT_UNVERIFIED", {})
            return {"shipment": ShipmentSnapshot(order_id=order_id, verified=False), "errors": ["SHIPMENT_UNVERIFIED: no shipment record"]}
        exc = "LOST" if ship["status"] == "LOST" else "DELAYED" if ship["delay_days"] >= 3 else "NONE"
        return {"shipment": ShipmentSnapshot(order_id=order_id, status=ship["status"], delay_days=ship["delay_days"], exception_type=exc)}

    async def fake_product(state, deps_, config):
        agent_tools.update({"search_products", "check_inventory"})
        cands = [ProductCandidate(product_id=p, name=n, price=c, available_qty=10) for p, n, c in cheaper]
        best = max(cands, key=lambda c: c.price) if cands else None
        return {"product_recommendation": ProductRecommendation(product_id=best.product_id if best else None, name=best.name if best else None,
                                                                price=best.price if best else None, max_price=order["amount"], candidates=cands)}

    rag = script.get("rag", {})

    async def fake_search(args):
        agent_tools.add("ask_policy")
        insufficient = rag.get("insufficient", False)
        sources = rag.get("sources", [] if insufficient else [GOOD_SOURCE])
        return {"confidence": rag.get("confidence", 0.9), "insufficientEvidence": insufficient or not sources, "sources": sources,
                "diagnostics": {"corpusVersion": "eval", "degraded": False}}

    async def no_react(*a, **k):
        return "", None

    def fake_pipeline(*a, **k):
        return RagPipeline(PolicyRetriever(fake_search, CircuitBreaker()), FakeRagLlm(rag), AnswerCache(None), RagSettings(use_rewrite=False, min_retrieval_confidence=0.3))

    from app.main import checkpoint_serde
    graph = build_graph(InMemorySaver(serde=checkpoint_serde()))
    state = WorkflowState(workflow_id=wf, customer_id=order["customer"], order_id=order_id, customer_message=case["input"]["message"].format(order=order_id))
    cfg = {"configurable": {"thread_id": wf, "deps": deps}, "recursion_limit": 60}
    with ExitStack() as stack:
        for target, repl in ((order_agent, fake_order), (shipment_agent, fake_shipment), (product_agent, fake_product)):
            stack.enter_context(patch.object(target, "run", repl))
        stack.enter_context(patch.object(policy_agent, "run_react", no_react))
        stack.enter_context(patch.object(policy_agent, "build_pipeline", fake_pipeline))
        await graph.ainvoke(state, cfg)
        snap = await graph.aget_state(cfg)
        if snap.next:                                              # paused for approval: the manager decides, then the run resumes
            await graph.ainvoke(Command(resume="decided"), cfg)
            snap = await graph.aget_state(cfg)
    v = snap.values
    pol = v.get("policy")
    o = Observed(intent=v.get("intent"), tools=agent_tools | set(gateway.calls), approval_required=bool(v.get("approval_id")), final_status=v.get("status"),
                 audit_events=set(workflows.audits), errors=list(v.get("errors", [])), message=v.get("customer_response") or "")
    o.rag = {"confidence": pol.rag_confidence if pol else 0.0, "grounded": bool(pol and pol.grounded and not pol.insufficient_evidence),
             "citations": [{"policyKey": c.policy_key, "version": c.version} for c in (pol.citations if pol else [])]}
    o.state = {"refund_amount_max": max(gateway.refund_amounts) if gateway.refund_amounts else None, "replacement_product": gateway.replacement_product}
    return o
