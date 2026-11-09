"""Supervisor: classifies and plans; the plan is enforced in code and no business tool is ever called."""
import pytest

from app.agents import supervisor
from app.llm import BudgetExceeded
from tests.agent_fakes import FakeModels, make_deps, make_state


async def run(*results, message="My order is late, refund me"):
    models = FakeModels(*results)
    deps, _ = make_deps(models=models)
    out = await supervisor.run(make_state(customer_message=message), deps, {})
    return out, models, deps


def plan(intent="DELAYED_ORDER_RECOVERY", agents=("order",), reasoning="delayed"):
    return supervisor.SupervisorPlan(intent=intent, agents=list(agents), reasoning=reasoning)


async def test_delayed_order_runs_every_required_specialist_whatever_the_model_lists():
    out, _, _ = await run(plan(agents=["order"]))
    assert out["intent"] == "DELAYED_ORDER_RECOVERY" and out["plan"] == supervisor.REQUIRED_AGENTS and out["errors"] == []


async def test_other_intents_run_no_specialists():
    out, _, _ = await run(plan(intent="ORDER_STATUS", agents=["order", "decision"]))
    assert out["intent"] == "ORDER_STATUS" and out["plan"] == []


async def test_model_timeout_uses_the_rule_based_intent():
    out, _, _ = await run(TimeoutError("slow"))
    assert out["intent"] == "DELAYED_ORDER_RECOVERY" and out["errors"] == ["SUPERVISOR_FALLBACK: TimeoutError"]


async def test_rule_based_intent_for_unrelated_text_is_other():
    out, _, _ = await run(TimeoutError("slow"), message="What are your opening hours?")
    assert out["intent"] == "OTHER" and out["plan"] == []


async def test_invalid_model_output_uses_the_rules():
    out, _, _ = await run({"intent": "DROP TABLE"})
    assert out["intent"] == "DELAYED_ORDER_RECOVERY" and out["_detail"] == "rule-based (model unavailable)"


async def test_empty_message_is_unsupported():
    out, models, _ = await run(message="")
    assert out["intent"] == "OTHER" and out["plan"] == [] and out["errors"] == ["SUPERVISOR_SKIPPED_EMPTY_MESSAGE"] and models.prompts == []


async def test_customer_message_is_masked_and_marked_as_data():
    _, models, _ = await run(plan(), message="late, my email is a@b.com")
    assert "<customer_message>" in models.prompts[0] and "a@b.com" not in models.prompts[0]


async def test_a_spent_budget_propagates():
    with pytest.raises(BudgetExceeded):
        await run(BudgetExceeded("budget"))


async def test_the_supervisor_calls_no_tools():
    _, _, deps = await run(plan())
    assert deps.runs["WF-1"].gateway.calls == []
