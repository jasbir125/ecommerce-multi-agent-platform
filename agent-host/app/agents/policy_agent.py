"""Policy Agent: grounded policy answers from the RAG pipeline (agent) + deterministic eligibility check (code).

The agent calls `ask_policy`, which runs the RAG pipeline (retrieval, rerank, grounded answer, validation) and
returns the policy version, source, score and citations. Whether a refund is allowed is decided by the policy
service's validation endpoint, which we call directly with the verified facts so a model can never pass different
numbers. Retrieved policy text is data: instructions found in it are never followed, and a suspected injection is
audited and the text withheld.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from app.agents.base import (DETAIL_CHARS, MODEL_ERRORS, TOOL_ERRORS, AgentInputError, AgentRun, AgentUpdate, describe,
                             run_react, tool_results)
from app.llm import BudgetExceeded
from app.models.money import money_as_number
from app.models.policy import Citation, PolicyDecision
from app.models.order import OrderSnapshot
from app.models.shipment import ShipmentSnapshot
from app.rag.models import RagResult
from app.rag.pipeline import build_pipeline, result_for_llm

log = logging.getLogger("agent.policy")

# ---------------------------------------------------------------------------------------------- constants
AGENT_NAME = "policy"
ASK_TOOL = "ask_policy"
ELIGIBILITY_TOOL = "validate_refund_eligibility"
INJECTION_MARKERS = ("injection:", "withheld:")
SUMMARY_CHARS = 600
EVIDENCE_CHARS = 200
SYSTEM_PROMPT = ("You are the Policy Agent. Use the ask_policy tool to find what company policy says about the customer's situation. "
                 "Report only what the tool returns, with its policy name and version. If it says it could not verify, say so.")
POLICY_QUESTION = ("Is a customer whose shipment is delayed or lost eligible for a refund or a replacement, "
                   "and what approval is needed for the refund?")


# -------------------------------------------------------------------------------------------------- models
class PolicyRequest(BaseModel):
    order: OrderSnapshot
    shipment: ShipmentSnapshot


class EligibilityVerdict(BaseModel):
    """The policy service's deterministic answer (the source of truth for eligibility)."""
    refund_eligible: bool = False
    approval_required: bool = True
    reasons: list[str] = Field(default_factory=list)
    policy_key: str | None = None
    policy_version: str | None = None


class PolicyResult(AgentUpdate):
    policy: PolicyDecision | None = None


class PolicyValidationError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ------------------------------------------------------------------------------------------------- run()
async def run(state, deps, config) -> dict[str, Any]:
    timer = AgentRun(AGENT_NAME, state)
    notes: list[str] = []
    try:
        request = validate_input(state)
    except AgentInputError as e:
        return timer.finish(handle_error(e.code, [])).to_update()
    ctx = deps.run(state)
    ask = build_ask_tool(deps, ctx, config)
    summary = await invoke_tools_or_agent(deps, ctx, request, ask, config, notes, timer)
    rag = await normalize_tool_results(ctx, ask, notes)
    try:
        verdict = await apply_business_rules(ctx, state.order_id, request)
    except PolicyValidationError as e:
        timer.failed("POLICY_VALIDATION_FAILED", e, tool=ELIGIBILITY_TOOL)
        return timer.finish(handle_error(f"POLICY_VALIDATION_FAILED: {e.reason[:160]}", notes)).to_update()
    injected = await audit_injection(state, deps, rag)
    decision = select_decision(verdict, rag, injected)
    return timer.finish(build_result(decision, rag, notes, summary)).to_update()


# ---------------------------------------------------------------------------------------- validate_input()
def validate_input(state) -> PolicyRequest:
    if state.order is None or state.shipment is None:
        raise AgentInputError("POLICY_SKIPPED_MISSING_FACTS")
    return PolicyRequest(order=state.order, shipment=state.shipment)


# --------------------------------------------------------------------------------------- prepare_context()
def prepare_context(request: PolicyRequest) -> str:
    shipment = request.shipment
    return (f"Find the policy answer to: {POLICY_QUESTION} Situation: shipment {shipment.exception_type}, "
            f"{shipment.delay_days} days late.")


def build_ask_tool(deps, ctx, config) -> StructuredTool:
    pipeline = build_pipeline(deps, ctx.gateway, config)

    async def ask_policy(question: str) -> str:
        return result_for_llm(await pipeline.answer(question))
    return StructuredTool.from_function(coroutine=ask_policy, name=ASK_TOOL, args_schema=None,
                                        description="Ask a question about company policy. Returns a grounded answer with citations, "
                                                    "a confidence, and insufficientEvidence=true when policy cannot answer.")


# ------------------------------------------------------------------------------- invoke_tools_or_agent()
async def invoke_tools_or_agent(deps, ctx, request: PolicyRequest, ask: StructuredTool, config, notes: list[str], timer: AgentRun) -> str:
    """Let the model ask the policy question. A spent token budget stops the run; any other model failure is recorded."""
    try:
        summary, _ = await run_react(deps, ctx, AGENT_NAME, SYSTEM_PROMPT, prepare_context(request), config,
                                     extra_tools={ASK_TOOL: ask})
    except BudgetExceeded:
        raise
    except MODEL_ERRORS as e:
        notes.append(f"POLICY_AGENT_ERROR: {describe(e)}")
        timer.failed("POLICY_AGENT_ERROR", e)
        return ""
    return summary


# ------------------------------------------------------------------------------- normalize_tool_results()
async def normalize_tool_results(ctx, ask: StructuredTool, notes: list[str]) -> RagResult:
    """The RAG result the model's tool call produced; if it skipped retrieval, run it here so the decision rests on evidence."""
    answers = tool_results(ctx, AGENT_NAME, ASK_TOOL)
    if answers:
        parsed = parse_rag_result(answers[-1])
        if parsed is not None:
            return parsed
        notes.append("POLICY_RESULT_MALFORMED")
    notes.append("POLICY_SEARCH_DIRECT")
    return RagResult.model_validate_json(await ask.coroutine(POLICY_QUESTION))


def parse_rag_result(raw: Any) -> RagResult | None:
    try:
        return RagResult.model_validate(raw)
    except ValidationError as e:
        log.warning("AGENT_ERROR agent_name=%s tool_name=%s error_code=POLICY_RESULT_MALFORMED error=%s", AGENT_NAME, ASK_TOOL, e)
        return None


# ------------------------------------------------------------------------------------ apply_business_rules()
async def apply_business_rules(ctx, order_id: str, request: PolicyRequest) -> EligibilityVerdict:
    """Deterministic eligibility from the policy service, using only verified order and shipment facts."""
    order, shipment = request.order, request.shipment
    amount = money_as_number(order.total_amount)
    try:
        raw = await ctx.gateway.call(ELIGIBILITY_TOOL, {
            "order_id": order_id, "order_amount": amount, "refund_amount": amount, "delay_days": shipment.delay_days,
            "shipment_status": shipment.status or "", "order_status": order.status})
    except TOOL_ERRORS as e:
        raise PolicyValidationError(str(e)) from e
    return parse_verdict(raw)


def parse_verdict(raw: dict[str, Any]) -> EligibilityVerdict:
    return EligibilityVerdict(refund_eligible=bool(raw.get("refundEligible")), approval_required=bool(raw.get("approvalRequired", True)),
                              reasons=raw.get("reasons", []), policy_key=raw.get("policyKey"), policy_version=raw.get("policyVersion"))


def is_injection_marker(dropped_entry: str) -> bool:
    return dropped_entry.startswith(INJECTION_MARKERS)


def suspects_injection(rag: RagResult) -> list[str]:
    dropped = rag.diagnostics.get("context", {}).get("dropped", [])
    return dropped if any(is_injection_marker(entry) for entry in dropped) else []


async def audit_injection(state, deps, rag: RagResult) -> bool:
    """Retrieved policy text is data. If it looked like instructions it was withheld; record that for the audit trail."""
    dropped = suspects_injection(rag)
    if not dropped:
        return False
    await deps.workflows.audit(state.workflow_id, "policy-agent", "PROMPT_INJECTION_SUSPECTED_IN_POLICY",
                               {"dropped": dropped[:5], "note": "retrieved policy text treated as data and withheld"}, state.customer_id)
    return True


# ----------------------------------------------------------------------------------------- select_decision()
def select_decision(verdict: EligibilityVerdict, rag: RagResult, injection_suspected: bool) -> PolicyDecision:
    """Eligibility comes from the deterministic verdict; the evidence quality comes from RAG. Neither is model-written."""
    return PolicyDecision(
        refund_eligible=verdict.refund_eligible, approval_required=verdict.approval_required, reasons=verdict.reasons,
        policy_key=verdict.policy_key, policy_version=verdict.policy_version, rag_confidence=rag.confidence, grounded=rag.grounded,
        insufficient_evidence=rag.insufficientEvidence, citations=build_citations(rag), summary=rag.answer[:SUMMARY_CHARS],
        injection_suspected=injection_suspected, rag=rag.model_dump())


def build_citations(rag: RagResult) -> list[Citation]:
    return [Citation(policy_key=s.policyKey, version=s.version, source=s.source, score=s.rerankScore) for s in rag.sources]


# ----------------------------------------------------------------------------------------- build_evidence()
def build_evidence(decision: PolicyDecision, rag: RagResult) -> list[str]:
    return [f"policy {decision.policy_key} v{decision.policy_version}: refund_eligible={decision.refund_eligible} {decision.reasons}",
            f"policy evidence (conf {rag.confidence}): {rag.answer[:EVIDENCE_CHARS]}"]


# --------------------------------------------------------------------------------------------- build_result()
def build_result(decision: PolicyDecision, rag: RagResult, notes: list[str], summary: str) -> PolicyResult:
    return PolicyResult(policy=decision, errors=notes, evidence=build_evidence(decision, rag), detail=summary[:DETAIL_CHARS])


# -------------------------------------------------------------------------------------------- handle_error()
def handle_error(code: str, notes: list[str]) -> PolicyResult:
    """No policy decision is written; later steps see policy=None and escalate."""
    return PolicyResult(errors=[code, *notes])
