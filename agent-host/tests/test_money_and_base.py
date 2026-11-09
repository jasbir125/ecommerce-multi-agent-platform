"""Shared pieces: money rules, the agent update contract, structured logging, error handling and temperatures."""
import logging
from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.agents.base import AgentRun, AgentUpdate, error_code
from app.llm import MODEL_ERRORS, BudgetExceeded, temperature_for
from app.models.money import Money, is_within_budget, same_currency, to_money
from app.models.order import OrderSnapshot
from tests.agent_fakes import make_state


@pytest.mark.parametrize("raw,expected", [(79.99, "79.99"), ("10", "10.00"), (0.1 + 0.2, "0.30"), ("2.675", "2.68"), (Decimal("1.005"), "1.01")])
def test_amounts_are_decimal_cents_rounded_half_up(raw, expected):
    assert to_money(raw) == Decimal(expected)


@pytest.mark.parametrize("bad", ["abc", None, True, float("nan"), float("inf"), ""])
def test_non_amounts_are_rejected(bad):
    with pytest.raises(ValueError):
        to_money(bad)


def test_budget_comparison_is_exact_after_rounding():
    assert is_within_budget(Decimal("129.99"), Decimal("129.99")) and not is_within_budget(Decimal("130.00"), Decimal("129.99"))


def test_currencies_compare_case_insensitively_and_missing_means_default():
    assert same_currency("usd", "USD") and same_currency(None, "USD") and not same_currency("EUR", "USD")


def test_money_fields_are_decimal_in_python_and_numbers_in_json():
    snapshot = OrderSnapshot(order_id="O", status="S", payment_status="P", total_amount=129.99)
    assert snapshot.total_amount == Decimal("129.99") and isinstance(snapshot.total_amount, Decimal)
    assert snapshot.model_dump(mode="json")["total_amount"] == 129.99 and '"total_amount":129.99' in snapshot.model_dump_json()


def test_money_type_validates_nested_models():
    class Row(BaseModel):
        price: Money | None = None
    assert Row(price="5").price == Decimal("5.00") and Row().price is None


class Out(AgentUpdate):
    thing: str | None = None


def test_agent_update_always_has_errors_and_evidence_and_skips_none_fields():
    assert Out().to_update() == {"errors": [], "evidence": []}
    update = Out(thing="x", errors=["E: y"], evidence=["e"], detail="why", retries=1).to_update()
    assert update == {"errors": ["E: y"], "evidence": ["e"], "thing": "x", "_detail": "why", "_retries": 1}


def test_error_code_is_the_part_before_the_colon():
    assert error_code("ORDER_UNVERIFIED: boom: more") == "ORDER_UNVERIFIED" and error_code("PLAIN") == "PLAIN"


def test_agent_run_logs_structured_context_and_outcome(caplog):
    caplog.set_level(logging.INFO, logger="agent.run")
    timer = AgentRun("order", make_state())
    timer.failed("ORDER_UNVERIFIED", RuntimeError("boom sk-abcdefghijklmnopqrstuv"), tool="get_order_status")
    timer.finish(Out(errors=["ORDER_UNVERIFIED: x"]))
    text = caplog.text
    assert "agent_name=order" in text and "tool_name=get_order_status" in text and "error_code=ORDER_UNVERIFIED" in text
    assert "workflow_id=WF-1" in text and "request_id=REQ-1" in text and "customer_id=CUST-1001" in text and "order_id=ORD-7842" in text
    assert "latency_ms=" in text and "outcome=degraded" in text and "sk-abcdefghijklmnopqrstuv" not in text


def test_only_the_customer_reply_uses_a_non_zero_temperature():
    assert temperature_for("response") == 0.3
    assert all(temperature_for(a) == 0.0 for a in ("supervisor", "order", "shipment", "policy", "product", "risk", "decision"))


def test_model_errors_cover_expected_failures_but_not_a_spent_budget():
    assert issubclass(TimeoutError, MODEL_ERRORS) and issubclass(RuntimeError, MODEL_ERRORS)
    assert not issubclass(BudgetExceeded, MODEL_ERRORS) and not issubclass(KeyError, MODEL_ERRORS)
