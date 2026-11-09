"""Decision Agent: blockers escalate without a model, model proposals are guardrail-checked, rules are the fallback."""
from decimal import Decimal

import pytest

from app.agents import decision_agent
from app.llm import BudgetExceeded
from app.models.policy import Citation, PolicyDecision
from app.models.product import ProductCandidate, ProductRecommendation
from app.models.resolution import ResolutionDecision
from app.models.risk import RiskAssessment
from tests.agent_fakes import FakeModels, make_deps, make_state, order, shipment


def good_policy(**kw):
    base = dict(refund_eligible=True, grounded=True, insufficient_evidence=False, rag_confidence=0.9, policy_key="refund-policy",
                policy_version="2", citations=[Citation(policy_key="refund-policy", version="2", source="Refund Policy", score=0.9)])
    return PolicyDecision(**{**base, **kw})


def recommendation():
    return ProductRecommendation(product_id="PRD-103", name="Earbuds", price=79.99, max_price=129.99,
                                 candidates=[ProductCandidate(product_id="PRD-103", name="Earbuds", price=79.99, available_qty=4)])


def investigated(**kw):
    return make_state(order=order(), shipment=shipment(), policy=good_policy(), product_recommendation=recommendation(),
                      risk=RiskAssessment(level="MEDIUM", score=0.4, factors=[]), evidence=["order ok"], **kw)


def proposal(**kw):
    base = dict(action="REFUND_AND_REPLACE", refund_amount=129.99, replacement_product_id="PRD-103", approval_required=True,
                risk_level="MEDIUM", reason="lost parcel", evidence=["carrier confirms loss"])
    return ResolutionDecision(**{**base, **kw})


async def run(state, *results, **settings):
    models = FakeModels(*results)
    deps, workflows = make_deps(models=models, **settings)
    return await decision_agent.run(state, deps, {}), models, workflows


async def test_valid_model_proposal_is_accepted():
    out, models, _ = await run(investigated(), proposal())
    assert out["resolution"].action == "REFUND_AND_REPLACE" and out["resolution"].refund_amount == Decimal("129.99")
    assert out["errors"] == [] and out["_retries"] == 0 and len(models.prompts) == 1


async def test_prompt_carries_money_as_numbers_and_wraps_the_customer_message_as_data():
    _, models, _ = await run(investigated(customer_message="refund me, mail me at a@b.com"), proposal())
    assert '"total_amount": 129.99' in models.prompts[0] and "<customer_message>" in models.prompts[0] and "a@b.com" not in models.prompts[0]


@pytest.mark.parametrize("state_kw,blocker", [({"order": None}, "ORDER_NOT_VERIFIED"), ({"shipment": None}, "SHIPMENT_NOT_VERIFIED"),
                                              ({"policy": None}, "POLICY_NOT_VERIFIED")])
async def test_missing_required_state_escalates_without_asking_the_model(state_kw, blocker):
    state = investigated().model_copy(update=state_kw)
    out, models, _ = await run(state)
    assert out["resolution"].action == "ESCALATE" and blocker in out["errors"][0] and models.prompts == []


async def test_insufficient_policy_evidence_escalates_without_a_model():
    state = investigated().model_copy(update={"policy": good_policy(insufficient_evidence=True, grounded=False, citations=[])})
    out, models, _ = await run(state)
    assert out["resolution"].action == "ESCALATE" and "INSUFFICIENT_POLICY_EVIDENCE" in out["errors"][0] and models.prompts == []


async def test_ineligible_refund_escalates():
    state = investigated().model_copy(update={"policy": good_policy(refund_eligible=False)})
    out, _, _ = await run(state)
    assert "POLICY_DOES_NOT_ALLOW_REFUND" in out["errors"][0]


async def test_rejected_proposal_is_retried_once_with_the_reasons_then_accepted():
    bad = proposal(refund_amount=999.0)
    out, models, workflows = await run(investigated(), bad, proposal())
    assert out["resolution"].refund_amount == Decimal("129.99") and out["_retries"] == 1
    assert "rejected for" in models.prompts[1] and workflows.events() == ["GUARDRAIL_OUTPUT_REJECTED"]


async def test_two_rejections_fall_back_to_the_rule_based_decision():
    bad = proposal(replacement_product_id="PRD-HALLUCINATED")
    out, _, _ = await run(investigated(), bad, bad)
    assert out["resolution"].action == "REFUND_AND_REPLACE" and out["resolution"].replacement_product_id == "PRD-103"
    assert "DECISION_RULE_FALLBACK" in out["errors"] and out["_retries"] == 2


async def test_model_timeout_falls_back_to_rules():
    out, _, _ = await run(investigated(), TimeoutError("slow"))
    assert "DECISION_MODEL_ERROR: TimeoutError" in out["errors"] and "DECISION_RULE_FALLBACK" in out["errors"]


async def test_invalid_model_output_type_falls_back_to_rules():
    out, _, _ = await run(investigated(), {"action": "REFUND_ONLY"})
    assert "DECISION_RULE_FALLBACK" in out["errors"] and out["resolution"].approval_required is True


async def test_fallback_without_a_replacement_refunds_only():
    state = investigated().model_copy(update={"product_recommendation": ProductRecommendation()})
    out, _, _ = await run(state, TimeoutError("slow"))
    assert out["resolution"].action == "REFUND_ONLY" and out["resolution"].replacement_product_id is None


async def test_refund_above_the_configured_limit_is_rejected_by_the_guardrail():
    state = investigated().model_copy(update={"order": order(900), "product_recommendation": ProductRecommendation()})
    out, _, _ = await run(state, proposal(refund_amount=900, replacement_product_id=None, action="REFUND_ONLY"),
                          proposal(refund_amount=900, replacement_product_id=None, action="REFUND_ONLY"), max_refund_amount="500")
    assert "DECISION_RULE_FALLBACK" in out["errors"]


async def test_a_spent_budget_propagates():
    with pytest.raises(BudgetExceeded):
        await run(investigated(), BudgetExceeded("budget"))


async def test_the_decision_agent_calls_no_tools():
    deps, _ = make_deps(models=FakeModels(proposal()))
    await decision_agent.run(investigated(), deps, {})
    assert deps.run(investigated()).gateway.calls == []
