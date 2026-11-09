"""Response Agent: writes the customer-facing message. It has no business tools.

The message may only state outcomes that appear under "Verified outcomes" (built from workflow state written by
services). The model's text is untrusted: if it claims a refund, replacement, approval, id or delivery date that is
not verified, it is rejected and a fixed template is sent instead.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.base import MODEL_ERRORS, AgentInputError, AgentRun, AgentUpdate, describe
from app.guardrails.output_guardrails import response_violations
from app.models.resolution import ExecutionResult
from app.observability import metrics as m

log = logging.getLogger("agent.response")

# ---------------------------------------------------------------------------------------------- constants
AGENT_NAME = "response"
HUMAN_REVIEW_STATUSES = ("ESCALATED", "REJECTED")
REFUND_DONE_STATUSES = ("COMPLETED", "PARTIALLY_COMPLETED")
FABRICATED_ID_PREFIX = "FABRICATED_ID"
SYSTEM_PROMPT = """You write a short, friendly message (max 3 sentences) to an ecommerce customer about their order request.
State ONLY the outcomes listed under "Verified outcomes", and include every listed refund and replacement id. Never mention a refund or replacement as done unless it is listed.
If everything completed, do not say that a human will follow up.
Do not invent ids, dates or amounts, and never promise a delivery or arrival date or time. If the request needs a human, say a support agent will follow up."""


# -------------------------------------------------------------------------------------------------- models
class CustomerReply(BaseModel):
    """The model's answer. Untrusted text."""
    message: str


class ResponseInput(BaseModel):
    order_id: str
    status: str
    approval_status: str | None = None
    execution: ExecutionResult | None = None
    shipment_unverified: bool = False


class ResponseResult(AgentUpdate):
    customer_response: str | None = None


# ------------------------------------------------------------------------------------------------- run()
async def run(state, deps, config) -> dict[str, Any]:
    """Returns the workflow update: {"customer_response": text, "errors": notes, "evidence": []}."""
    timer = AgentRun(AGENT_NAME, state)
    notes: list[str] = []
    try:
        request = validate_input(state)
    except AgentInputError as e:
        return timer.finish(handle_error(request=None, notes=[e.code])).to_update()
    context = prepare_context(request)
    candidate = await invoke_tools_or_agent(deps, config, context, notes, timer)
    problems = apply_business_rules(candidate, request)
    text = select_decision(candidate, problems, request, notes)
    return timer.finish(build_result(text, notes)).to_update()


# ---------------------------------------------------------------------------------------- validate_input()
def validate_input(state) -> ResponseInput:
    if not state.order_id or not state.status:
        raise AgentInputError("RESPONSE_SKIPPED_MISSING_STATE")
    return ResponseInput(order_id=state.order_id, status=state.status, approval_status=state.approval_status, execution=state.execution,
                         shipment_unverified=state.shipment is not None and not state.shipment.verified)


# --------------------------------------------------------------------------------------- prepare_context()
def prepare_context(request: ResponseInput) -> str:
    """The "Verified outcomes" block: the only facts the message may state."""
    execution = request.execution
    lines = [f"status: {request.status}"]
    if execution:
        lines.extend(execution_lines(execution, request.order_id))
    if request.status in HUMAN_REVIEW_STATUSES:
        lines.append("a human support agent will review the request")
    return "\n".join(lines)


def execution_lines(execution: ExecutionResult, order_id: str) -> list[str]:
    lines: list[str] = []
    if execution.refund_id:
        lines.append(f"refund {execution.refund_id} status {execution.refund_status} for order {order_id}")
    if execution.replacement_order_id:
        lines.append(f"replacement order {execution.replacement_order_id} created")
    if execution.compensated:
        lines.append("replacement could not be completed; reserved stock was released")
    return lines


# ------------------------------------------------------------------------------- invoke_tools_or_agent()
async def invoke_tools_or_agent(deps, config, outcomes: str, notes: list[str], timer: AgentRun) -> str | None:
    """Ask the model for the message. None if it is unavailable or returned nothing usable."""
    try:
        reply = await deps.models.structured(AGENT_NAME, CustomerReply, [
            SystemMessage(SYSTEM_PROMPT), HumanMessage(f"Verified outcomes:\n{outcomes}")], config)
    except MODEL_ERRORS as e:
        notes.append(f"RESPONSE_MODEL_ERROR: {describe(e)}")
        timer.failed("RESPONSE_MODEL_ERROR", e)
        return None
    return normalize_model_output(reply)


def normalize_model_output(reply: Any) -> str | None:
    if not isinstance(reply, CustomerReply):
        return None
    return reply.message.strip() or None


# ------------------------------------------------------------------------------------ apply_business_rules()
def apply_business_rules(candidate: str | None, request: ResponseInput) -> list[str]:
    """Violations of 'state only what is verified'. No candidate means nothing to check."""
    if candidate is None:
        return []
    return response_violations(candidate, request.execution, request.order_id, request.status, request.approval_status)


# ----------------------------------------------------------------------------------------- select_decision()
def select_decision(candidate: str | None, problems: list[str], request: ResponseInput, notes: list[str]) -> str:
    """The model's text if it is clean; otherwise the fixed template."""
    if candidate is not None and not problems:
        return candidate
    if problems:
        record_rejection(problems, notes)
    return template_reply(request)


def record_rejection(problems: list[str], notes: list[str]) -> None:
    notes.append(f"GUARDRAIL_RESPONSE_REJECTED: {', '.join(problems)}")
    for code in problems:
        m.guardrail("output", metric_code(code))


def metric_code(violation: str) -> str:
    """Fabricated ids are reported as one code so the metric has a fixed set of labels."""
    return violation.split("_ID_")[0] if violation.startswith(FABRICATED_ID_PREFIX) else violation


def template_reply(request: ResponseInput) -> str:
    """Fixed wording for each verified outcome. Used when the model is unavailable or its text is rejected."""
    execution, order_id = request.execution, request.order_id
    if request.status == "COMPLETED" and execution and execution.refund_id:
        replacement = f" Your replacement order {execution.replacement_order_id} has been created." if execution.replacement_order_id else ""
        return f"Your refund {execution.refund_id} for order {order_id} has been initiated.{replacement}"
    if request.status == "PARTIALLY_COMPLETED" and execution and execution.refund_id:
        return (f"Your refund {execution.refund_id} for order {order_id} has been initiated. We could not finish the replacement, "
                "and our support team will contact you.")
    if request.shipment_unverified:
        return f"We could not verify the shipment status for order {order_id} right now. Our support team will follow up."
    return f"Your request for order {order_id} needs a manual review. Our support team will contact you."


# ----------------------------------------------------------------------------------------- build_evidence()
def build_evidence(text: str) -> list[str]:
    """The response adds no evidence: every fact in it is already in the workflow evidence."""
    return []


# --------------------------------------------------------------------------------------------- build_result()
def build_result(text: str, notes: list[str]) -> ResponseResult:
    return ResponseResult(customer_response=text, errors=notes, evidence=build_evidence(text))


# -------------------------------------------------------------------------------------------- handle_error()
def handle_error(request: ResponseInput | None, notes: list[str]) -> ResponseResult:
    """Missing state: no message is written (the caller keeps whatever is already there)."""
    return ResponseResult(errors=notes)
