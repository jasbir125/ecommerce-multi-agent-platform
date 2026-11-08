"""Policy Agent: grounded policy answers from the RAG pipeline (agent) + deterministic eligibility check (code).

The agent calls `ask_policy`, which runs the full RAG pipeline (rewrite, hybrid retrieval, rerank, grounded answer,
validation). Whether a refund is allowed is decided by the policy-service validation endpoint, which we call
directly with the verified facts so a model can never pass different numbers.
"""
from langchain_core.tools import StructuredTool

from app.agents.base import run_react, tool_results
from app.guardrails.tool_guardrails import ToolCallError
from app.models.policy import Citation, PolicyDecision
from app.rag.factory import build_pipeline, result_for_llm
from app.rag.models import RagResult

SYSTEM = ("You are the Policy Agent. Use the ask_policy tool to find what company policy says about the customer's situation. "
          "Report only what the tool returns, with its policy name and version. If it says it could not verify, say so.")
QUESTION = ("Is a customer whose shipment is delayed or lost eligible for a refund or a replacement, "
            "and what approval is needed for the refund?")


def _ask_tool(deps, ctx, config) -> StructuredTool:
    pipeline = build_pipeline(deps, ctx.gateway, config)

    async def ask_policy(question: str) -> str:
        return result_for_llm(await pipeline.answer(question))
    return StructuredTool.from_function(coroutine=ask_policy, name="ask_policy", args_schema=None,
                                        description="Ask a question about company policy. Returns a grounded answer with citations, "
                                                    "a confidence, and insufficientEvidence=true when policy cannot answer.")


async def run(state, deps, config) -> dict:
    ctx = deps.run(state)
    if state.order is None or state.shipment is None:
        return {"errors": ["POLICY_SKIPPED_MISSING_FACTS"]}
    notes, summary = [], ""
    ask = _ask_tool(deps, ctx, config)
    try:
        summary, _ = await run_react(
            deps, ctx, "policy", SYSTEM,
            f"Find the policy answer to: {QUESTION} Situation: shipment {state.shipment.exception_type}, "
            f"{state.shipment.delay_days} days late.", config, extra_tools={"ask_policy": ask})
    except Exception as e:  # noqa: BLE001
        if "BudgetExceeded" in type(e).__name__:
            raise
        notes.append(f"POLICY_AGENT_ERROR: {e}")
    answers = tool_results(ctx, "policy", "ask_policy")
    if not answers:
        # The model skipped retrieval: run it ourselves, so the decision always rests on retrieved evidence.
        notes.append("POLICY_SEARCH_DIRECT")
        answers = [(await _direct(ask)).model_dump()]
    rag = RagResult.model_validate(answers[-1])
    try:
        v = await ctx.gateway.call("validate_refund_eligibility", {
            "order_id": state.order_id, "order_amount": state.order.total_amount,
            "refund_amount": state.order.total_amount, "delay_days": state.shipment.delay_days,
            "shipment_status": state.shipment.status or "", "order_status": state.order.status})
    except ToolCallError as e:
        return {"errors": [f"POLICY_VALIDATION_FAILED: {str(e)[:160]}", *notes]}
    dropped = rag.diagnostics.get("context", {}).get("dropped", [])
    injection = any(d.startswith(("injection:", "withheld:")) for d in dropped)
    if injection:
        await deps.workflows.audit(state.workflow_id, "policy-agent", "PROMPT_INJECTION_SUSPECTED_IN_POLICY",
                                   {"dropped": dropped[:5], "note": "retrieved policy text treated as data and withheld"}, state.customer_id)
    decision = PolicyDecision(
        refund_eligible=bool(v.get("refundEligible")), approval_required=bool(v.get("approvalRequired", True)),
        reasons=v.get("reasons", []), policy_key=v.get("policyKey"), policy_version=v.get("policyVersion"),
        rag_confidence=rag.confidence, grounded=rag.grounded, insufficient_evidence=rag.insufficientEvidence,
        citations=[Citation(policy_key=s.policyKey, version=s.version, source=s.source, score=s.rerankScore) for s in rag.sources],
        summary=rag.answer[:600], injection_suspected=injection)
    return {"policy": decision, "errors": notes, "_detail": summary[:300],
            "evidence": [f"policy {decision.policy_key} v{decision.policy_version}: refund_eligible={decision.refund_eligible} {decision.reasons}",
                         f"policy evidence (conf {rag.confidence}): {rag.answer[:200]}"]}


async def _direct(ask: StructuredTool) -> RagResult:
    return RagResult.model_validate_json(await ask.coroutine(QUESTION))
