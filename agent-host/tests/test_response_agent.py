"""Response Agent: the customer text may only state verified outcomes; otherwise a fixed template is sent."""
import pytest

from app.agents import response_agent
from app.llm import BudgetExceeded
from app.models.resolution import ExecutionResult
from tests.agent_fakes import FakeModels, make_deps, make_state, shipment

DONE = ExecutionResult(refund_id="RF-ABC1234567", refund_status="INITIATED", replacement_order_id="ORD-REPL000001")
PARTIAL = ExecutionResult(refund_id="RF-ABC1234567", refund_status="INITIATED", compensated=True)


async def run(state, *results):
    models = FakeModels(*results)
    deps, _ = make_deps(models=models)
    return await response_agent.run(state, deps, {}), models


def completed():
    return make_state(status="COMPLETED", approval_status="APPROVED", execution=DONE)


def reply(text):
    return response_agent.CustomerReply(message=text)


async def test_clean_model_text_is_sent():
    text = "Your refund RF-ABC1234567 was initiated and replacement ORD-REPL000001 created."
    out, models = await run(completed(), reply(text))
    assert out["customer_response"] == text and out["errors"] == [] and out["evidence"] == []
    assert "refund RF-ABC1234567 status INITIATED" in models.prompts[0]


async def test_fabricated_id_is_rejected_and_the_template_is_used():
    out, _ = await run(completed(), reply("Your refund RF-ZZZ9999999 and ORD-REPL000001 are done."))
    assert out["customer_response"].startswith("Your refund RF-ABC1234567") and any(e.startswith("GUARDRAIL_RESPONSE_REJECTED") for e in out["errors"])


async def test_unverified_refund_claim_is_rejected():
    state = make_state(status="ESCALATED")
    out, _ = await run(state, reply("Your refund has been issued!"))
    assert "manual review" in out["customer_response"] and "UNVERIFIED_REFUND_CLAIM" in out["errors"][0]


async def test_delivery_date_promise_is_rejected():
    out, _ = await run(completed(), reply("Your replacement will arrive by Friday. RF-ABC1234567 ORD-REPL000001"))
    assert "UNSUPPORTED_DELIVERY_PROMISE" in out["errors"][0]


async def test_model_timeout_uses_the_template():
    out, _ = await run(completed(), TimeoutError("slow"))
    assert "RESPONSE_MODEL_ERROR: TimeoutError" in out["errors"] and "RF-ABC1234567" in out["customer_response"]


async def test_wrong_output_type_uses_the_template():
    out, _ = await run(completed(), {"message": "hi"})
    assert out["customer_response"].startswith("Your refund RF-ABC1234567") and out["errors"] == []


async def test_empty_model_text_uses_the_template():
    out, _ = await run(completed(), reply("   "))
    assert out["customer_response"].startswith("Your refund")


async def test_compensation_template_says_the_replacement_failed_and_support_will_call():
    state = make_state(status="PARTIALLY_COMPLETED", approval_status="APPROVED", execution=PARTIAL)
    out, models = await run(state, TimeoutError("slow"))
    assert "could not finish the replacement" in out["customer_response"]
    assert "replacement order" not in out["customer_response"].lower().replace("the replacement", "")


async def test_compensation_outcome_is_in_the_verified_block():
    state = make_state(status="PARTIALLY_COMPLETED", approval_status="APPROVED", execution=PARTIAL)
    _, models = await run(state, reply("Your refund RF-ABC1234567 was initiated; support will contact you."))
    assert "reserved stock was released" in models.prompts[0]


async def test_unverified_shipment_has_its_own_template():
    state = make_state(status="ESCALATED", shipment=shipment(verified=False))
    out, _ = await run(state, TimeoutError("slow"))
    assert "could not verify the shipment" in out["customer_response"]


async def test_human_review_statuses_are_listed_as_outcomes():
    _, models = await run(make_state(status="REJECTED"), reply("A support agent will follow up on ORD-7842."))
    assert "a human support agent will review the request" in models.prompts[0]


async def test_missing_status_writes_no_message():
    out, models = await run(make_state().model_copy(update={"status": ""}))
    assert out["errors"] == ["RESPONSE_SKIPPED_MISSING_STATE"] and "customer_response" not in out and models.prompts == []


async def test_a_spent_budget_propagates():
    with pytest.raises(BudgetExceeded):
        await run(completed(), BudgetExceeded("budget"))
